# src/shanhai/providers/image.py
import base64
import io
import re
from pathlib import Path

import httpx
from PIL import Image, ImageStat

from shanhai.providers._http import request_with_retry

# 低于此值判定为近似纯色/异常(NaN 解码静默转黑图,见 2026-07-13 DGX 实测:ComfyUI 的
# VAE 解码输出 NaN 时,np.clip(NaN,0,255).astype(uint8) 静默转 0,execution_success 照常上报)。
# 纯黑图 stddev 恰好是 0.0;真实插画哪怕大片留白,主体的色彩/线条变化也远高于这个值。
MIN_CHANNEL_STDDEV = 2.0

# --- 分格边框检测(2026-07-26 实测标定) ---
# 图像模型常自作主张给画面加一圈漫画格边框(旧提示词里"连环画单页/漫画格"这类措辞是诱因)。
# 646 个线上成图里 171 页(26%)中招,最惨的作品 23/24 页。提示词已改,但模型不保证守约,
# 这里做第二道拦截:检出即抛 ImageGenError,由 S3/S4 现有的重试循环重生成。
#
# 判据只看**左右两条竖框线**:模型画的是完整矩形框,但生成图是 3:2、被 typeset 裁成 16:9 时
# 上下边框连同画面上下部一起被裁掉了,线上实际只剩两条黑竖条。
# 必须全分辨率扫描——框线常只有 1~2px,缩略图会把它平均掉(踩过这个坑)。
FRAME_MARGIN_MIN = 200   # 最外侧那圈"留白"至少这么亮才算留白
FRAME_LINE_DROP = 90     # 框线要比留白暗这么多才算数
FRAME_DEPTH_FROM = 0.012  # 从边缘往内多深开始找框线(跳过最外侧留白本身)
FRAME_DEPTH_TO = 0.08     # 找到多深为止(再往里就是画面内容了)
FRAME_SCANLINES = (0.3, 0.5, 0.7)   # 沿边取三条采样线
FRAME_MIN_VOTES = 2       # 三条里至少两条都看到框线,才认定这条边有框

# --- 信箱黑边检测(2026-07-30 实测标定) ---
# 与上面的框线是**两类**缺陷:框线是"外侧留白 + 内侧一条暗线",而这里是整条边就是一整片
# 纯黑(模型把画面当成带黑边的电影画幅来画)。上面那套判据要求 min(outer) > 200,遇到
# 纯黑边直接不成立,查上下边也拦不住——线上 f50b97f4 第 10 页右格正是这么漏过去的。
# 判据:一条边往内连续若干行/列都"近黑且极均匀",且**对边同时成立**。letterbox 恒是
# 成对出现的,要求成对能把夜景/大片深色画面的误伤压到零。
# 标定:DGX 全量 992 张成图扫描,命中 2 张,人眼复核**两张都是真黑边**,零误伤。
LETTERBOX_DARK = 40      # 边带平均亮度上限
LETTERBOX_FLAT = 12      # 边带亮度极差上限(纯色带才算,渐变的深色画面不算)
LETTERBOX_MIN_DEPTH = 0.01   # 边带至少占这么深(实测命中值 0.014~0.17,远高于此)
# 也必须是**少数**:整张图本身就暗且平(如纯色桩图、大片夜色)时,从任何一条边扫进去都
# 满足"近黑且均匀",会一路扫到底——那不是信箱边,是这张图就长这样。实测真黑边最深 0.17,
# 取 0.35 作上限,扫到上限即判定"不是边带"。少了这条,64×64 纯蓝的测试桩图会被全判成黑边。
LETTERBOX_MAX_DEPTH = 0.35
LETTERBOX_SAMPLES = 64   # 每行/列沿边取多少个采样点


class ImageGenError(Exception):
    pass


def _reject_if_blank(data: bytes) -> bytes:
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        stddev = ImageStat.Stat(im).stddev
    except Exception as e:  # noqa: BLE001 无法解码本身也是一种生成异常,统一包成 ImageGenError
        raise ImageGenError(f"生成图片无法解码: {e}") from e
    if max(stddev) < MIN_CHANNEL_STDDEV:
        raise ImageGenError(f"生成图片近似纯色(stddev={[round(s, 2) for s in stddev]}),疑似解码异常")
    return data


def _edge_has_line(depth: int, span: int, at) -> bool:
    """某条边是否存在"外侧留白 + 内侧一条明显暗线"的框线特征。
    at(i, k):沿垂直于该边的方向取第 i 个像素、沿平行方向第 k 条采样线。"""
    votes = 0
    for frac in FRAME_SCANLINES:
        k = min(span - 1, round(span * frac))
        outer = [at(i, k) for i in range(max(3, round(depth * 0.008)))]
        inner = [at(i, k) for i in range(round(depth * FRAME_DEPTH_FROM),
                                         round(depth * FRAME_DEPTH_TO))]
        if outer and inner and min(outer) > FRAME_MARGIN_MIN \
                and min(inner) < min(outer) - FRAME_LINE_DROP:
            votes += 1
    return votes >= FRAME_MIN_VOTES


def _band_depth(at, span: int, depth: int) -> float:
    """从某条边往内数,连续多少行/列是"近黑且均匀"的,返回占该方向的比例。
    at(i, x):距该边第 i 行/列、沿边方向第 x 个像素。"""
    step = max(1, span // LETTERBOX_SAMPLES)
    cap = round(depth * LETTERBOX_MAX_DEPTH)
    n = 0
    for i in range(cap):
        line = [at(i, x) for x in range(0, span, step)]
        if sum(line) / len(line) > LETTERBOX_DARK or max(line) - min(line) > LETTERBOX_FLAT:
            return n / depth
        n += 1
    return 0.0      # 一路扫到上限:整张图就是暗的,不是信箱边


def _has_letterbox(px, w: int, h: int) -> bool:
    """上下或左右**两条对边**都是整片纯黑边带。见文件头 LETTERBOX_* 的标定说明。"""
    top = _band_depth(lambda i, x: px[x, i], w, h)
    bottom = _band_depth(lambda i, x: px[x, h - 1 - i], w, h)
    left = _band_depth(lambda i, x: px[i, x], h, w)
    right = _band_depth(lambda i, x: px[w - 1 - i, x], h, w)
    return (min(top, bottom) >= LETTERBOX_MIN_DEPTH
            or min(left, right) >= LETTERBOX_MIN_DEPTH)


def reject_if_framed(data: bytes) -> bytes:
    """拦截"模型自己画了分格边框"或"自己加了信箱黑边"的图。判据与阈值见文件头常量的标定说明。

    ⚠️ 公开但**不在** generate() 里调用,由 s4_pages 在自己的调用点调:这是"S4 页面合格性"
    判据(阈值取自 646 个成图**页**样本),不是通用的图像合格性判据。挂在共享 generate() 上时
    S3 的三视图也要过一遍,而三视图是"纯白背景 + 三个全身像并排",左右留白极易被判成竖框线。
    线上误杀过两次,其中一次让用户上传的参考图被静默丢弃、角色退化成纯文字特征
    (2026-07-29「可可托海」)。判据没错,位置错了。"""
    try:
        im = Image.open(io.BytesIO(data)).convert("L")
    except Exception:  # noqa: BLE001 解码失败交给 _reject_if_blank 统一报,这里不重复
        return data
    w, h = im.size
    px = im.load()
    if px is None:      # Pillow 理论上可能返回 None(锁定的图像),此时放行不误伤
        return data
    left = _edge_has_line(w, h, lambda i, k: px[i, k])
    right = _edge_has_line(w, h, lambda i, k: px[w - 1 - i, k])
    if left and right:
        raise ImageGenError("生成图片左右两侧都有框线,疑似被画成了漫画分格页(应为满幅插画)")
    if _has_letterbox(px, w, h):
        raise ImageGenError("生成图片有整片纯黑的信箱边,疑似被画成了带黑边的电影画幅(应为满幅插画)")
    return data


class ImageClient:
    """OpenAI 兼容图像客户端,双上游形态。未来本地 ComfyUI 实现同签名 generate() 即可整体替换。"""

    def __init__(self, base_url: str, api_key: str, model: str, mode: str = "images_api",
                 timeout: float = 1200, lora_model: str | None = None):
        self.model = model
        self.mode = mode
        self.lora_model = lora_model  # LoRA 短名(不区分大小写),短名→safetensors 文件名的映射
        # 由 shim 侧负责,这边不必知道文件名;仅本地 ComfyUI 后端有意义,非空时随请求体一并
        # 发送,远程后端多半直接忽略这个字段。
        self.timeout = timeout  # 暴露为可读属性,供 s4_pages.py 的重试循环读取同一个
        # "单张图总耗时预算"上限(S3 单角色只生成一次、无重试循环,不需要读这个属性)。
        self._base_url = base_url
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,  # 本地 ComfyUI 扩散生成 + 队列可达数分钟(SHANHAI_IMAGE_TIMEOUT)
        )

    def generate(self, prompt: str, size: str = "1536x1024",
                 references: list[Path] | None = None, retries: int = 2) -> bytes:
        # 网络调用统一走 request_with_retry(idempotent=False:生成非幂等,连接层断连可能已被
        # 上游受理并计费,不盲重试,仅瞬时状态码 429/5xx 重试);非瞬时 HTTPStatusError(如内容
        # 审核 400)在各 _via_* 里 raise_for_status() 后直接抛出。
        # 只做一道普适检查:近似纯色 / 无法解码——那是任何环节都不能接受的生成失败。
        # 抛 ImageGenError,交给调用方(S3/S4 现有的 except 重试/降级逻辑)接管,不在此重试:
        # 重试策略连同时间预算都归 s4_pages 管,这里只负责判定"这张不合格"。
        # 分格边框那道检查**刻意不在这里**:它是 S4 页面专属判据,见 reject_if_framed 的说明。
        return _reject_if_blank(self._dispatch(prompt, size, references, retries))

    def route_for(self, references: list[Path] | None) -> str:
        """本次请求会走哪条路:"chat" / "edit" / "text2img"。

        抽出来是为了让 s4_pages 记录"这一页实际走了哪条路"时能复用同一份判据,而不是照抄一遍
        if——这个仓库已经因为"同一判断写两份"栽过四次(paneling._cover vs typeset._cover、
        _draw_flags 在两处各算一遍、_INVALIDATES 按位置而非依赖、caption 的 240 散在三处),
        所以 _dispatch 必须按本方法的返回值分派,判断只允许存在这一份。

        之所以要把这条路记下来:只有 "edit"(ComfyUI 的 image_edit 工作流)带 LoRA 节点;
        "text2img"(Text2IMGKrea2 模板)**没有** LoRA 节点,lora 字段传了也会被静默忽略。
        这正是"用户换了 LoRA 却有些页毫无变化"的原因——那些页恰好没有参考图,走的是 text2img。
        """
        if self.mode == "chat_api":     # chat 形态下有没有参考图都走同一个接口
            return "chat"
        return "edit" if references else "text2img"

    def _dispatch(self, prompt: str, size: str, references: list[Path] | None,
                  retries: int) -> bytes:
        route = self.route_for(references)
        if route == "chat":
            return self._via_chat(prompt, references or [], retries)
        if route == "edit":
            return self._via_edits(prompt, references or [], size, retries)
        return self._via_generations(prompt, size, retries)

    def _lora_extra(self) -> dict:
        # 字段名与值域(短名,不区分大小写)由 DGX 上的 image-shim 定义,两边必须一致;
        # 不传则由 shim 回落到它自己的默认 LoRA。
        return {"lora": self.lora_model} if self.lora_model else {}

    def _via_generations(self, prompt: str, size: str, retries: int) -> bytes:
        r = request_with_retry(lambda: self._client.post(
            "/images/generations",
            json={"model": self.model, "prompt": prompt, "size": size, "n": 1,
                  **self._lora_extra()}),
            retries, idempotent=False, base_url=self._base_url)
        r.raise_for_status()
        return _decode(_first(r.json()))

    def _via_edits(self, prompt: str, references: list[Path], size: str, retries: int) -> bytes:
        files = [("image[]", (p.name, p.read_bytes(), "image/png")) for p in references]
        r = request_with_retry(lambda: self._client.post(
            "/images/edits",
            data={"model": self.model, "prompt": prompt, "size": size, **self._lora_extra()},
            files=files),
            retries, idempotent=False, base_url=self._base_url)
        r.raise_for_status()
        return _decode(_first(r.json()))

    def _via_chat(self, prompt: str, references: list[Path], retries: int) -> bytes:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for p in references:
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        r = request_with_retry(lambda: self._client.post("/chat/completions", json={
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            **self._lora_extra(),
        }), retries, idempotent=False, base_url=self._base_url)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        for img in msg.get("images") or []:
            url = img.get("image_url", {}).get("url", "")
            if url.startswith("data:image"):
                return base64.b64decode(url.split(",", 1)[1])
            if url.startswith("http"):
                return _decode({"url": url})
        m = re.search(r"data:image/\w+;base64,([A-Za-z0-9+/=]+)", msg.get("content") or "")
        if m:
            return base64.b64decode(m.group(1))
        raise ImageGenError(f"响应中未找到图像: {str(msg)[:200]}")


def _first(resp: dict) -> dict:
    data = resp.get("data")
    if not data:
        raise ImageGenError(f"响应中无 data: {str(resp)[:200]}")
    return data[0]


def _decode(item: dict) -> bytes:
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        # 下载也走瞬时重试:免因单次抖断而回退到"重出整张图"(会重复计费)
        r = request_with_retry(lambda: httpx.get(item["url"], timeout=120), retries=2)
        r.raise_for_status()
        return r.content
    raise ImageGenError(f"未知的响应格式: {list(item)}")

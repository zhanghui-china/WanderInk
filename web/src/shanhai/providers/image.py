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


def _reject_if_framed(data: bytes) -> bytes:
    """拦截"模型自己画了分格边框"的图。判据与阈值见文件头常量的标定说明。"""
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
        # 两道内容合理性检查:近似纯色(解码异常)与自作主张画的分格边框。
        # 都抛 ImageGenError,交给调用方(S3/S4 现有的 except Exception 重试/降级逻辑)接管,
        # 不在此重试——重试策略连同时间预算都归 s4_pages 管,这里只负责判定"这张不合格"。
        return _reject_if_framed(_reject_if_blank(self._dispatch(prompt, size, references, retries)))

    def _dispatch(self, prompt: str, size: str, references: list[Path] | None,
                  retries: int) -> bytes:
        if self.mode == "chat_api":
            return self._via_chat(prompt, references or [], retries)
        if references:
            return self._via_edits(prompt, references, size, retries)
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

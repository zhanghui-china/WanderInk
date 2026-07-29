import concurrent.futures as cf
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from shanhai import typeset
from shanhai import paneling
from shanhai.providers.image import ImageClient
from shanhai.schema import CharacterCard, Panel, Project, StoryboardCell
from shanhai.styles import STYLE_PRESETS

MAX_ATTEMPTS = 3  # 1 次 + 重试 2 次(PRD F4)
REF_MAX = 768  # 参考图上传前按最长边缩到 768px,避免大图上传 WriteTimeout(M0 gate 结论)
CONCURRENCY = 3  # S4 逐页生成并发上限,平衡速度与代理过载(观测到 503"资源不足")

# "满幅无边框"这句是必须的:此前首句写的是"连环画单页画面",图像模型把"连环画单页"
# 理解成"画一页漫画",顺手就把分格边框一起画进去了——2026-07-26 实测 646 个成图里
# 171 页(26%)被画上了黑色框线,最严重的作品 23/24 页中招。措辞上也不再出现
# "连环画/漫画页"这类会诱导画格子的词。
NO_FRAME = ("画面必须满幅铺满整个画布,四周不要有任何边框、画框、分格线或白色留白边,"
            "不是漫画分格页。")

# 与 _downscaled_ref 的裁切互补:裁切从条件输入里拿掉"三个人并排"这个结构信号,
# 这两句从语义上补住裁切之后可能残留的情形。单靠提示词压不住 edit 条件的结构黏性
# (实测 5/7 的页出现同一角色画两三次),故两手都要,不是二选一。
ONE_INSTANCE = (
    "参考图仅用于识别角色的外观身份,不要复制参考图的构图、并排排列或纯白背景。"
    "画面表现单一瞬间的一个场景,每个角色在画面中只出现一次,"
    "不要画成拼贴、分身或同一角色的多个视角。"
)

PAGE_TMPL = (
    "{style}。整幅横向插画:{scene}。出场角色:{features}。"
    "严格保持角色与参考图中的形象一致(外观特征、色彩、服饰或体表覆盖物)。"
    + ONE_INSTANCE +
    "横向宽幅构图(16:9 横图),主体居中、上下留出安全边距。"
    + NO_FRAME +
    "画面中不要出现任何文字。"
)

PANEL_TMPL = (
    # 同样不能出现"漫画格"这个词——分格页的格线由 paneling 的 GUTTER 画,
    # 模型再画一层就成了"框中框"。这里只描述这一格的画面内容。
    "{style}。整幅横向插画:{scene}。出场角色:{features}。{shot}。"
    "严格保持角色与参考图中的形象一致(外观特征、色彩、服饰或体表覆盖物)。"
    # 分格页更需要这条:一格里画出同一角色的两个身位,和 paneling 的分格叠在一起
    # 会读成"格中格",比单图页的重复更难辨认
    + ONE_INSTANCE +
    # 与 PAGE_TMPL 同款的安全边距要求。分格页拼版时每格仍会按版位比例做少量裁切,
    # 主体贴边(尤其头顶/下巴)会被切掉——这是"人脸不全"投诉的一半原因。
    "主体居中,人物头顶与下巴距画面上下边缘留出充足安全边距(排版时边缘可能被裁切)。"
    # 分格页更不能让模型画边框:格线由 paneling 的 GUTTER 统一绘制,模型再画一层就是"框中框"。
    + NO_FRAME +
    "画面中不要出现任何文字。"
)

_FACE_MARGIN = "特写镜头,聚焦面部表情与细节,但完整保留头顶与下巴,不要让脸贴到画面边缘"
SHOT_HINTS = {
    "wide": "远景构图,交代场景全貌",
    "medium": "中景构图,人物与环境兼顾",
    # 特写本就要求脸占满画幅、几乎无余量,叠加拼版裁切最容易切额头/下巴,故单独强调留边
    "closeup": _FACE_MARGIN,
    "insert": _FACE_MARGIN,
}


# 三视图里正面像所占的横向比例。s3 的两个模板都规定"正面、侧面、背面并排",
# TURNAROUND_REF_TMPL 更是写死"左侧正面、中间侧面、右侧背面",故取最左一段。
# 略大于 1/3:模型排版不会精确等分,留一点余量总比把正面像切掉半个肩膀好;
# 多带进来的一点侧面像边缘会被后续 thumbnail 弱化,危害远小于漏切。
FRONT_VIEW_RATIO = 0.38

# 缓存文件名里的版本号。**改裁切逻辑必须同时改它**——_refs/ 下已有的旧缓存是
# 整张三视图的缩略图,mtime 又比源文件新,不换名字的话新逻辑会直接复用旧文件、
# 改动完全不生效(而且是那种"跑完一切正常、就是没效果"的静默失效)。
REF_CACHE_VERSION = "v2front"


def _downscaled_ref(src: Path, cache_dir: Path) -> Path:
    """把角色三视图裁成**单个正面像**再缩略,作为生图的身份参考。

    为什么必须裁:S3 产出的三视图是"同一角色正面/侧面/背面并排"的设定图
    (s3_characters.TURNAROUND_TMPL),而 S4 走的是 image **edit** 工作流
    (providers/image.py:route_for → 有参考图即 edit),图生图编辑传递的是**结构**,
    不只是身份。整张喂进去,模型会照着"三个人并排"去构图——实测泰山那部作品抽样
    7 页有 5 页出现同一角色画两三次,其中一页直接是三个同款冠袍男子并排、
    恰好一正面一侧面一背面,就是三视图原样搬进了雨景。
    提示词那句"严格保持角色与参考图中的形象一致"反而在帮倒忙:对 edit 模型而言
    它就是"贴近这张参考图",包含那个三联排版。提示词只能和结构条件拔河,裁切是剪绳子。

    已知代价:参考图不再含侧面/背面,需要背身构图的页一致性可能略降。
    与 5/7 的重复率相比这个取舍是明确的。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{src.stem}.{REF_CACHE_VERSION}.png"
    if out.exists() and src.stat().st_mtime <= out.stat().st_mtime:
        return out
    img = Image.open(src).convert("RGB")   # 坏图在此抛,由调用方的 per-cell try 捕获
    img = img.crop((0, 0, max(1, round(img.width * FRONT_VIEW_RATIO)), img.height))
    img.thumbnail((REF_MAX, REF_MAX))
    tmp = cache_dir / f".{out.name}.{threading.get_ident()}.tmp"   # 线程唯一临时名 + 原子替换,并发安全
    img.save(tmp, "PNG")
    os.replace(tmp, out)
    return out


ALIAS_CHARS = "甲乙丙丁戊己庚辛"


def _anonymize(present: list[CharacterCard], scene: str,
               cast: list[CharacterCard] | None = None,
               names: list[str] | None = None) -> tuple[str, str]:
    """把角色中文名从 features 与 scene 两处一起换成中性代号,返回 (features, scene)。

    为什么:名字是给人看的,对生图模型只是画面内容——「小虎」原样进 prompt,
    模型就照字面画出一只真老虎;「小龙女」「铁牛」同理。身份锚本来就是参考图,
    名字对成图零贡献,删掉没有代价。
    scene 这条路必须一起处理:S2 的 visual_desc 里同样写着「小虎站在山门前」,
    只洗 features 等于没洗。
    代号只需页内稳定(features 与 scene 用同一个),跨页不必一致。
    替换表必须覆盖 present 之外的名字(cast 全表 + storyboard 声明但 cast 里查不到的 names):
    (a) scene 常提到本页/本格没出场的角色,漏掉就是原样进 prompt;
    (b) present 里的短名会把 scene 里更长的未覆盖名字截碎(「小龙女」→「角色甲女」)。
    features 仍然只列 present——没出场的角色不该给参考特征。"""
    ordered = [c.name for c in present]
    ordered += [c.name for c in (cast or [])] + list(names or [])
    # 空名必须滤掉:CharacterCard.name 没有 min_length,模型返回空名是可能的,而
    # "少年推开山门".replace("", "角色甲") 会在**每个字之间**插一次代号,整段 scene
    # 变成「角色甲少角色甲年角色甲推…」——该页 prompt 直接报废,还不抛异常、不进 status。
    aliases = {n: f"角色{ALIAS_CHARS[i] if i < len(ALIAS_CHARS) else i + 1}"
               for i, n in enumerate(n for n in dict.fromkeys(ordered) if n)}
    # 必须按名字长度降序替换,否则「小虎」会先把「小虎子」截成「角色甲子」
    for name in sorted(aliases, key=len, reverse=True):
        scene = scene.replace(name, aliases[name])
    features = ";".join(f"{aliases[c.name]}({c.feature_prompt})"
                        for c in present if c.name) or "无固定角色"
    return features, scene


def _panel_prompt(panel: Panel, style: str, cards: dict,
                  names: list[str] | None = None) -> tuple[str, list[CharacterCard]]:
    present = [cards[n] for n in panel.characters if n in cards]
    features, scene = _anonymize(present, panel.visual_desc,
                                 cast=list(cards.values()), names=names)
    shot = SHOT_HINTS.get(panel.shot_type, SHOT_HINTS["medium"])
    prompt = PANEL_TMPL.format(style=style, scene=scene, features=features, shot=shot)
    return prompt, present


def _render_panel_cell(cell: StoryboardCell, style: str, cards: dict, image: ImageClient,
                       workdir: Path, pages_dir: Path, ref_cache: Path) -> None:
    imgs: list[bytes] = []
    kept_panels: list[Panel] = []
    gen_ms_total = 0
    # 各格的 refs **不相同**:_panel_prompt 是按 panel.characters(页面角色的子集,空集合也合法)
    # 逐格算 present 的,所以一页里完全可能"有人物的格走 edit、空镜格走 text2img"。
    # 只记最后一格会说反话——恰恰在这个 feature 最该说真话的混合场景上。
    # 故收集各格路径,最后归约:全部相同取该值,不同则记 "mixed"(= 这一页只有部分内容吃到 LoRA)。
    # 只统计**真正进了成品页**的格(kept_panels 那些):写盘失败被 except 丢掉的格不算数。
    panel_routes: list[str] = []
    lora = ""
    # 每格按它自己要塞进的版位比例出图,而不是所有格都用整页的 image_size:后者是 3:2,
    # 而版位比例从 1.79 到 3.61 不等,cover 裁切会吃掉 16%~58% 的高度、人物头部首当其冲。
    # 已知限制:某格生成失败被跳过时,compose_manga_page 会按幸存格数降级版式,届时这里
    # 算的比例又对不上了——那种情况靠 paneling 的 PANEL_ANCHOR_Y 保头兜底。
    sizes = paneling.slot_sizes(cell.panels)
    for i, panel in enumerate(cell.panels, 1):
        prompt, present = _panel_prompt(panel, style, cards, names=cell.characters)
        sw, sh = sizes[i - 1]
        panel_size = f"{sw}x{sh}"
        t0 = time.monotonic()
        for attempt in range(MAX_ATTEMPTS):
            if attempt > 0 and time.monotonic() - t0 >= image.timeout:
                break  # 这一格的时间预算已耗尽,不再重试这一格(不影响其它格各自的预算)
            try:
                refs = [_downscaled_ref(workdir / c.turnaround_image, ref_cache)
                        for c in present if c.turnaround_image]
                gen_t0 = time.monotonic()
                art = image.generate(prompt, size=panel_size, references=refs or None)
                gen_ms_total += round((time.monotonic() - gen_t0) * 1000)
                panel_route = image.route_for(refs or None)
                out = pages_dir / f"page_{cell.index:02d}_panel{i}.png"
                out.write_bytes(art)
                panel.image = str(out.relative_to(workdir))
                imgs.append(art)
                kept_panels.append(panel)
                # 落在写盘之后:这一格确定进成品页了才计入路径统计
                panel_routes.append(panel_route)
                lora = image.lora_model or ""
                break
            except Exception:  # noqa: BLE001 单格失败不拖垮整页,重试/预算耗尽后放弃该格(不占位符硬凑)
                continue
    if not imgs:
        cell.image_route = ""      # 同上:本轮无产出,不留描述上一次生成的陈旧值
        cell.image_lora = ""
        cell.status = "failed"
        return
    composed = paneling.compose_manga_page(imgs, kept_panels)
    out = pages_dir / f"page_{cell.index:02d}.png"
    typeset.compose_page(composed, out)
    cell.image = str(out.relative_to(workdir))
    cell.image_gen_ms = gen_ms_total
    cell.image_route = panel_routes[0] if len(set(panel_routes)) == 1 else "mixed"
    cell.image_lora = lora
    cell.status = "confirmed"


def _render_cell(cell: StoryboardCell, style: str, cards: dict, image: ImageClient,
                 image_size: str, workdir: Path, pages_dir: Path, ref_cache: Path,
                 multi_panel: bool = False) -> None:
    # 判据必须同时看 multi_panel:S2 给模型的 JSON Schema 里始终带着 panels 字段,
    # 模型可能在用户没开分格时自发填上;只看 cell.panels 就会静默走分格(与用户预期相反)。
    # 历史项目里已经存下的 panels 同理——开关关着就一律当单图页处理。
    if cell.panels and multi_panel:
        _render_panel_cell(cell, style, cards, image, workdir, pages_dir, ref_cache)
        return
    present = [cards[n] for n in cell.characters if n in cards]
    features, scene = _anonymize(present, cell.visual_desc,
                                 cast=list(cards.values()), names=cell.characters)
    prompt = PAGE_TMPL.format(style=style, scene=scene, features=features)
    out = pages_dir / f"page_{cell.index:02d}.png"
    t0 = time.monotonic()
    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0 and time.monotonic() - t0 >= image.timeout:
            break  # 上一次尝试已经把这张图的时间预算耗尽(大概率真的卡住了),不再重试
        try:
            refs = [_downscaled_ref(workdir / c.turnaround_image, ref_cache)
                    for c in present if c.turnaround_image]
            gen_t0 = time.monotonic()
            art = image.generate(prompt, size=image_size, references=refs or None)
            gen_ms = round((time.monotonic() - gen_t0) * 1000)
            typeset.compose_page(art, out)
            # 耗时与图同生共死:先存局部变量、等排版和 image 都落定了再写回 cell。
            # 否则 compose_page 抛异常时耗时已经写进去、图却没有,失败页会挂着一个"生成 X.Xs"。
            cell.image = str(out.relative_to(workdir))
            cell.image_gen_ms = gen_ms
            # 判据必须与 image.generate 传的 references 完全一致(refs or None),
            # 否则记录的路径可能和实际生成用的路径对不上。
            cell.image_route = image.route_for(refs or None)
            cell.image_lora = image.lora_model or ""
            cell.status = "confirmed"
            return
        except Exception:  # noqa: BLE001 单页失败不拖垮整轮,重试/预算耗尽后标 failed
            pass
    # 清掉可能残留的上一次成功值:这一轮没产出新图,旧的路径/LoRA 描述的是另一次生成,
    # 挂在 failed 页上就是三条假信息(image/image_gen_ms 的同类残留是既有行为,不在本次范围)。
    cell.image_route = ""
    cell.image_lora = ""
    cell.status = "failed"


def run(project: Project, image: ImageClient, workdir: Path, image_size: str,
        strict: bool = False, on_progress: Callable[[], None] | None = None,
        concurrency: int = CONCURRENCY,
        cancel_check: Callable[[], bool] | None = None) -> Project:
    if project.script is None or not project.storyboard:
        raise ValueError("先完成 S2/S3")
    style = STYLE_PRESETS[project.style_preset]
    cards = {c.name: c for c in project.script.characters}
    pages_dir = workdir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    ref_cache = workdir / "characters" / "_refs"
    pending = [c for c in project.storyboard
               if not (c.status == "confirmed" and c.image and (workdir / c.image).exists())]
    # 逐页记录"这一页有哪些出场角色没有三视图可用"。
    # 此前这里是 `if not any(c.turnaround_image for c in characters)` ——**所有**角色都没图才告警,
    # 三个角色活一个就通过。实测 DGX 上的 8f41283a 正是这样:第一主角的三视图比 7 页画面晚
    # 18~33 分钟才产出,那 7 页全程无锚点,而这道护栏一声不吭(另两个角色有图)。
    # 判据用 cell.characters(该页声明的出场阵容)而不是分格页各格的 present 并集:
    # 后者是前者的子集,极少数"列了角色但没有任何一格用到"的 S2 不一致会被多报一个名字——
    # 多报一个缺锚点是安全方向,漏报才是这次事故本身。
    # 每轮无条件覆盖(含空列表):补画三视图后重跑,旧的缺失记录必须消失,否则它描述的是上一轮。
    for cell in pending:
        cell.missing_refs = [n for n in cell.characters
                             if n in cards and not cards[n].turnaround_image]
    short = [c.index for c in pending if c.missing_refs]
    if short:
        msg = (f"第 {'、'.join(str(i) for i in short)} 页的出场角色缺三视图参考"
               f"(S3 未运行/未产出/部分失败),这些页的角色一致性无保证")
        if strict:
            raise ValueError(msg)
        print(f"⚠️ {msg}")
    with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(_render_cell, cell, style, cards, image, image_size,
                             workdir, pages_dir, ref_cache, project.params.multi_panel)
                   for cell in pending]
        for f in cf.as_completed(futures):
            if cancel_check and cancel_check():
                for pending_f in futures:
                    pending_f.cancel()  # 已开始的取消不了(Python 线程池物理限制),但能拦掉还没排上的
                break
            f.result()   # 传播非预期错误(生成失败已在 _render_cell 内吞掉并标 failed)
            if on_progress:
                on_progress()
    project.status["s4"] = "done" if all(
        c.status == "confirmed" for c in project.storyboard) else "partial"
    return project

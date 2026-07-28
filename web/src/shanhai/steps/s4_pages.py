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

PAGE_TMPL = (
    "{style}。整幅横向插画:{scene}。出场角色:{features}。"
    "严格保持角色与参考图中的形象一致(外观特征、色彩、服饰或体表覆盖物)。"
    "横向宽幅构图(16:9 横图),主体居中、上下留出安全边距。"
    + NO_FRAME +
    "画面中不要出现任何文字。"
)

PANEL_TMPL = (
    # 同样不能出现"漫画格"这个词——分格页的格线由 paneling 的 GUTTER 画,
    # 模型再画一层就成了"框中框"。这里只描述这一格的画面内容。
    "{style}。整幅横向插画:{scene}。出场角色:{features}。{shot}。"
    "严格保持角色与参考图中的形象一致(外观特征、色彩、服饰或体表覆盖物)。"
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


def _downscaled_ref(src: Path, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / src.name
    if out.exists() and src.stat().st_mtime <= out.stat().st_mtime:
        return out
    img = Image.open(src).convert("RGB")   # 坏图在此抛,由调用方的 per-cell try 捕获
    img.thumbnail((REF_MAX, REF_MAX))
    tmp = cache_dir / f".{src.name}.{threading.get_ident()}.tmp"   # 线程唯一临时名 + 原子替换,并发安全
    img.save(tmp, "PNG")
    os.replace(tmp, out)
    return out


def _panel_prompt(panel: Panel, style: str, cards: dict) -> tuple[str, list[CharacterCard]]:
    present = [cards[n] for n in panel.characters if n in cards]
    features = ";".join(f"{c.name}({c.feature_prompt})" for c in present) or "无固定角色"
    shot = SHOT_HINTS.get(panel.shot_type, SHOT_HINTS["medium"])
    prompt = PANEL_TMPL.format(style=style, scene=panel.visual_desc, features=features, shot=shot)
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
        prompt, present = _panel_prompt(panel, style, cards)
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
    features = ";".join(f"{c.name}({c.feature_prompt})" for c in present) or "无固定角色"
    prompt = PAGE_TMPL.format(style=style, scene=cell.visual_desc, features=features)
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
    if not any(c.turnaround_image for c in project.script.characters):
        msg = "无任何角色三视图参考(S3 未运行/未产出),M0 角色一致性机制被绕过"
        if strict:
            raise ValueError(msg)
        print(f"⚠️ {msg}")
    style = STYLE_PRESETS[project.style_preset]
    cards = {c.name: c for c in project.script.characters}
    pages_dir = workdir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    ref_cache = workdir / "characters" / "_refs"
    pending = [c for c in project.storyboard
               if not (c.status == "confirmed" and c.image and (workdir / c.image).exists())]
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

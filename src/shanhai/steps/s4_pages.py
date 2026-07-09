import concurrent.futures as cf
import os
import threading
from pathlib import Path

from PIL import Image

from shanhai import typeset
from shanhai.providers.image import ImageClient
from shanhai.schema import Project, StoryboardCell
from shanhai.styles import STYLE_PRESETS

MAX_ATTEMPTS = 3  # 1 次 + 重试 2 次(PRD F4)
REF_MAX = 768  # 参考图上传前按最长边缩到 768px,避免大图上传 WriteTimeout(M0 gate 结论)
CONCURRENCY = 3  # S4 逐页生成并发上限,平衡速度与代理过载(观测到 503"资源不足")

PAGE_TMPL = (
    "{style}。连环画单页画面:{scene}。出场角色:{features}。"
    "严格保持角色与参考图中的形象一致(发型、服饰、面部特征)。"
    "横向宽幅构图(16:9 横图),主体居中、上下留出安全边距。"
    "画面中不要出现任何文字。"
)


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


def _render_cell(cell: StoryboardCell, style: str, cards: dict, image: ImageClient,
                 image_size: str, workdir: Path, pages_dir: Path, ref_cache: Path) -> None:
    present = [cards[n] for n in cell.characters if n in cards]
    features = ";".join(f"{c.name}({c.feature_prompt})" for c in present) or "无固定角色"
    prompt = PAGE_TMPL.format(style=style, scene=cell.visual_desc, features=features)
    out = pages_dir / f"page_{cell.index:02d}.png"
    for attempt in range(MAX_ATTEMPTS):
        try:
            refs = [_downscaled_ref(workdir / c.turnaround_image, ref_cache)
                    for c in present if c.turnaround_image]
            art = image.generate(prompt, size=image_size, references=refs or None)
            typeset.compose_page(art, cell.caption, out)
            cell.image = str(out.relative_to(workdir))
            cell.status = "confirmed"
            return
        except Exception:  # noqa: BLE001 单页失败不拖垮整轮,重试后标 failed
            if attempt == MAX_ATTEMPTS - 1:
                cell.status = "failed"


def run(project: Project, image: ImageClient, workdir: Path, image_size: str) -> Project:
    if project.script is None or not project.storyboard:
        raise ValueError("先完成 S2/S3")
    if not any(c.turnaround_image for c in project.script.characters):
        print("⚠️ 无任何角色三视图参考(S3 未运行/未产出),M0 角色一致性机制被绕过")
    style = STYLE_PRESETS[project.style_preset]
    cards = {c.name: c for c in project.script.characters}
    pages_dir = workdir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    ref_cache = workdir / "characters" / "_refs"
    pending = [c for c in project.storyboard
               if not (c.status == "confirmed" and c.image and (workdir / c.image).exists())]
    with cf.ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futures = [ex.submit(_render_cell, cell, style, cards, image,
                             image_size, workdir, pages_dir, ref_cache) for cell in pending]
        for f in cf.as_completed(futures):
            f.result()   # 传播非预期错误(生成失败已在 _render_cell 内吞掉并标 failed)
    project.status["s4"] = "done" if all(
        c.status == "confirmed" for c in project.storyboard) else "partial"
    return project

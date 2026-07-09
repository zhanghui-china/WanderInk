from pathlib import Path

from PIL import Image

from shanhai import typeset
from shanhai.providers.image import ImageClient
from shanhai.schema import Project
from shanhai.styles import STYLE_PRESETS

MAX_ATTEMPTS = 3  # 1 次 + 重试 2 次(PRD F4)
REF_MAX = 768  # 参考图上传前按最长边缩到 768px,避免大图上传 WriteTimeout(M0 gate 结论)

PAGE_TMPL = (
    "{style}。连环画单页画面:{scene}。出场角色:{features}。"
    "严格保持角色与参考图中的形象一致(发型、服饰、面部特征)。画面中不要出现任何文字。"
)


def _downscaled_ref(src: Path, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / src.name
    if not out.exists() or src.stat().st_mtime > out.stat().st_mtime:
        img = Image.open(src).convert("RGB")
        img.thumbnail((REF_MAX, REF_MAX))
        img.save(out, "PNG")
    return out


def run(project: Project, image: ImageClient, workdir: Path, image_size: str) -> Project:
    if project.script is None or not project.storyboard:
        raise ValueError("先完成 S2/S3")
    style = STYLE_PRESETS[project.style_preset]
    cards = {c.name: c for c in project.script.characters}
    pages_dir = workdir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    ref_cache = workdir / "characters" / "_refs"
    for cell in project.storyboard:
        if cell.status == "confirmed" and cell.image and (workdir / cell.image).exists():
            continue
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
                break
            except Exception:  # noqa: BLE001 单页失败不拖垮整轮,重试后标 failed
                if attempt == MAX_ATTEMPTS - 1:
                    cell.status = "failed"
    project.status["s4"] = "done" if all(
        c.status == "confirmed" for c in project.storyboard) else "partial"
    return project

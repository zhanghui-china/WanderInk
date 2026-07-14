import io
import zipfile
from pathlib import Path

from PIL import Image

from shanhai import typeset
from shanhai.schema import Project

PDF_RESOLUTION = 150.0  # 供打印/查看引用,不影响像素内容


def build_exports(project: Project, workdir: Path) -> Project:
    """已确认页(status=="confirmed" 且有 image)逐页与字幕/水印叠加层合成,
    打包 output/pages.zip + output/book.pdf,回写 project.output["zip"|"pdf"]。
    无确认页则不产出、不报错。"""
    cells = [c for c in project.storyboard if c.status == "confirmed" and c.image]
    if not cells:
        return project
    out_dir = workdir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    pages: list[Image.Image] = []
    for cell in cells:
        base = Image.open(workdir / cell.image).convert("RGBA")
        overlay = typeset.overlay_image(cell.caption)
        pages.append(Image.alpha_composite(base, overlay).convert("RGB"))

    zip_path = out_dir / "pages.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i, page in enumerate(pages, start=1):
            buf = io.BytesIO()
            page.save(buf, "PNG")
            zf.writestr(f"page_{i:02d}.png", buf.getvalue())

    pdf_path = out_dir / "book.pdf"
    pages[0].save(pdf_path, save_all=True, append_images=pages[1:], resolution=PDF_RESOLUTION)

    project.output["zip"] = str(zip_path)
    project.output["pdf"] = str(pdf_path)
    return project

import io
import zipfile
from pathlib import Path

from PIL import Image

from shanhai import typeset
from shanhai.schema import Project

PDF_RESOLUTION = 150.0  # 供打印/查看引用,不影响像素内容


def build_exports(project: Project, workdir: Path) -> Project:
    """已确认页(status=="confirmed"、有 image 且图片文件确实存在)逐页与字幕/水印叠加层
    合成,打包 output/pages.zip + output/book.pdf,回写 project.output["zip"|"pdf"]。
    无入选页则不产出、不报错。存在性检查与 s6_compose._content_cells 的入选契约对齐:
    confirmed 页引用悬空时跳过该页(打印原因)而非让整体导出崩溃。"""
    cells = []
    for c in project.storyboard:
        if c.status != "confirmed" or not c.image:
            continue
        if not (workdir / c.image).exists():
            print(f"跳过第 {c.index} 页(图片缺失:{c.image})")
            continue
        cells.append(c)
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

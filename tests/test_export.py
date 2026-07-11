from pathlib import Path

from PIL import Image, PdfParser

from shanhai import export, typeset
from shanhai.schema import Project, StoryboardCell


def _confirmed_cell(index: int, image_rel: str) -> StoryboardCell:
    return StoryboardCell(index=index, scene_ref=f"1-{index}", visual_desc="v", characters=[],
                          caption=f"第{index}页字幕", emotion="宁静", image=image_rel,
                          status="confirmed")


def test_overlay_image_returns_rgba_frame():
    im = typeset.overlay_image("断桥初遇")
    assert im.mode == "RGBA" and im.size == (1920, 1080)


def test_build_exports_produces_zip_and_pdf(tmp_path: Path):
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    Image.new("RGB", (1920, 1080), "red").save(pages_dir / "page_01.png")
    Image.new("RGB", (1920, 1080), "blue").save(pages_dir / "page_02.png")

    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [
        _confirmed_cell(1, "pages/page_01.png"),
        _confirmed_cell(2, "pages/page_02.png"),
    ]

    result = export.build_exports(p, tmp_path)

    zip_path = tmp_path / "output" / "pages.zip"
    pdf_path = tmp_path / "output" / "book.pdf"
    assert zip_path.exists()
    assert pdf_path.exists()

    # Pillow 的 PDF 插件只支持写(无 Image.open 读取器),用其内置 PdfParser 校验页数。
    with pdf_path.open("rb") as fh:
        pdf = PdfParser.PdfParser(f=fh)
        assert len(pdf.pages) == 2
        pdf.close()

    assert result.output["zip"] == str(zip_path)
    assert result.output["pdf"] == str(pdf_path)
    assert p.output["zip"] == str(zip_path)
    assert p.output["pdf"] == str(pdf_path)


def test_build_exports_skips_unconfirmed_and_missing_image(tmp_path: Path):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                       caption="c", emotion="宁静", status="draft"),  # 未确认
        StoryboardCell(index=2, scene_ref="1-2", visual_desc="v", characters=[],
                       caption="c", emotion="宁静", status="confirmed"),  # 无 image
    ]

    export.build_exports(p, tmp_path)

    assert not (tmp_path / "output" / "pages.zip").exists()
    assert "zip" not in p.output
    assert "pdf" not in p.output

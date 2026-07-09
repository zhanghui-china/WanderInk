import io
from pathlib import Path
from unittest.mock import MagicMock
from PIL import Image
from shanhai.providers.image import ImageGenError
from shanhai.schema import CharacterCard, Project, Script, StoryboardCell
from shanhai.steps import s4_pages

def _png() -> bytes:
    buf = io.BytesIO(); Image.new("RGB", (64, 64), "blue").save(buf, "PNG")
    return buf.getvalue()

def _project(tmp_path: Path) -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    card = CharacterCard(name="白素贞", role="r", personality="p", appearance="a",
                         feature_prompt="白衣女子", turnaround_image="characters/白素贞.png",
                         locked=True)
    (tmp_path / "characters").mkdir(parents=True)
    (tmp_path / "characters" / "白素贞.png").write_bytes(_png())
    p.script = Script(title="t", theme="th", acts=[], characters=[card])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=["白素贞"], caption="西湖初遇。", emotion="宁静")]
    return p

def test_s4_generates_and_composes(tmp_path: Path):
    image = MagicMock(); image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "confirmed"
    assert (tmp_path / "pages" / "page_01.png").exists()
    refs = image.generate.call_args.kwargs["references"]
    assert refs and refs[0].name == "白素贞.png"      # 三视图作为参考图传入
    prompt = image.generate.call_args.args[0]
    assert "白衣女子" in prompt and "不要出现任何文字" in prompt

def test_s4_retries_then_fails(tmp_path: Path):
    image = MagicMock(); image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 3            # 1 + 重试 2(PRD F4)
    assert p.storyboard[0].status == "failed"

def test_s4_skips_confirmed(tmp_path: Path):
    proj = _project(tmp_path)
    proj.storyboard[0].status = "confirmed"
    image = MagicMock()
    s4_pages.run(proj, image, tmp_path, "1536x1024")
    image.generate.assert_not_called()               # 断点续跑

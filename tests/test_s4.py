import io
import os
from pathlib import Path
from unittest.mock import MagicMock
import pytest
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
    proj.storyboard[0].image = "pages/page_01.png"
    (tmp_path / "pages").mkdir(parents=True)
    (tmp_path / "pages" / "page_01.png").write_bytes(_png())
    image = MagicMock()
    s4_pages.run(proj, image, tmp_path, "1536x1024")
    image.generate.assert_not_called()               # 断点续跑:已确认且文件在则跳过


def test_s4_regenerates_confirmed_when_file_missing(tmp_path: Path):
    proj = _project(tmp_path)
    proj.storyboard[0].status = "confirmed"
    proj.storyboard[0].image = "pages/page_01.png"   # 引用的文件并不存在
    image = MagicMock(); image.generate.return_value = _png()
    s4_pages.run(proj, image, tmp_path, "1536x1024")
    image.generate.assert_called_once()              # 产物丢失则重新生成


def test_s4_bad_turnaround_fails_only_that_cell(tmp_path: Path):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    good = CharacterCard(name="白素贞", role="r", personality="p", appearance="a",
                         feature_prompt="白衣女子", turnaround_image="characters/白素贞.png",
                         locked=True)
    (tmp_path / "characters").mkdir(parents=True)
    (tmp_path / "characters" / "白素贞.png").write_bytes(b"not a png")   # 损坏的三视图
    p.script = Script(title="t", theme="th", acts=[], characters=[good])
    p.storyboard = [
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                       characters=["白素贞"], caption="c1", emotion="宁静"),
        StoryboardCell(index=2, scene_ref="1-2", visual_desc="西湖",
                       characters=[], caption="c2", emotion="宁静"),
    ]
    image = MagicMock(); image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"        # 坏参考图只拖垮该页
    assert p.storyboard[1].status == "confirmed"     # 其它页照常生成
    assert p.status["s4"] == "partial"


def test_s4_warns_when_no_turnaround(tmp_path: Path, capsys):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    card = CharacterCard(name="白素贞", role="r", personality="p", appearance="a")  # 无三视图
    p.script = Script(title="t", theme="th", acts=[], characters=[card])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=["白素贞"], caption="c", emotion="宁静")]
    image = MagicMock(); image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    assert "一致性" in capsys.readouterr().out         # S3 未产出三视图时告警(M0 被绕过)


def test_s4_strict_raises_when_no_turnaround(tmp_path: Path):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    card = CharacterCard(name="白素贞", role="r", personality="p", appearance="a")  # 无三视图
    p.script = Script(title="t", theme="th", acts=[], characters=[card])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=["白素贞"], caption="c", emotion="宁静")]
    image = MagicMock(); image.generate.return_value = _png()
    with pytest.raises(ValueError):                    # strict=True 时无三视图直接失败,堵 M0 绕过
        s4_pages.run(p, image, tmp_path, "1536x1024", strict=True)


def test_s4_parallel_all_cells_confirmed(tmp_path: Path):
    p = _project(tmp_path)
    p.storyboard = [StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v",
                                   characters=["白素贞"], caption=f"第{i}页。", emotion="宁静")
                    for i in range(1, 7)]                # 6 页并发生成
    image = MagicMock(); image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert all(c.status == "confirmed" for c in p.storyboard)   # 全部成功
    assert p.status["s4"] == "done"
    assert all((tmp_path / "pages" / f"page_{i:02d}.png").exists() for i in range(1, 7))


def test_s4_downscaled_ref_rebuilds_on_newer_source(tmp_path: Path):
    src = tmp_path / "白素贞.png"
    Image.new("RGB", (100, 100), "red").save(src, "PNG")
    cache = tmp_path / "_refs"
    old = s4_pages._downscaled_ref(src, cache).read_bytes()
    out_mtime = (cache / "白素贞.png").stat().st_mtime
    Image.new("RGB", (100, 100), "blue").save(src, "PNG")   # S3 重绘该角色
    os.utime(src, (out_mtime + 100, out_mtime + 100))
    new = s4_pages._downscaled_ref(src, cache).read_bytes()
    assert new != old                                # 源图更新后缩略图重建

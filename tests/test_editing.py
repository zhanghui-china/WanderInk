from pathlib import Path

import pytest

from shanhai import editing
from shanhai.schema import CharacterCard, Project, Script, StoryboardCell


def _project(tmp_path: Path, n: int = 4) -> Project:
    """造 n 页,每页写真实 page/audio 小文件,内容含页号以便断言不串位。"""
    p = Project(project_id="x", scenic_spot="雷峰塔")
    (tmp_path / "pages").mkdir(parents=True)
    (tmp_path / "audio").mkdir(parents=True)
    for i in range(1, n + 1):
        (tmp_path / "pages" / f"page_{i:02d}.png").write_bytes(f"IMG{i}".encode())
        (tmp_path / "audio" / f"page_{i:02d}.mp3").write_bytes(f"AUD{i}".encode())
        p.storyboard.append(StoryboardCell(
            index=i, scene_ref=f"1-{i}", visual_desc=f"v{i}", characters=[f"c{i}"],
            caption=f"cap{i}", emotion="宁静", image=f"pages/page_{i:02d}.png",
            audio=f"audio/page_{i:02d}.mp3", duration_ms=1000 + i, status="confirmed"))
    p.output = {"mp4": "output/final.mp4", "zip": "output/x.zip"}
    return p


def test_update_caption_clears_audio_keeps_image(tmp_path: Path):
    p = _project(tmp_path)
    editing.update_cell(p, 2, caption="新文案")
    cell = p.storyboard[1]
    assert cell.caption == "新文案"
    assert cell.audio == "" and cell.duration_ms == 0     # 文案变 → 配音过期
    assert cell.image == "pages/page_02.png"              # 画面不受影响
    assert cell.status == "confirmed"
    assert p.output == {}                                 # 成片过期


def test_update_visual_desc_cascades_image(tmp_path: Path):
    p = _project(tmp_path)
    editing.update_cell(p, 3, visual_desc="新画面")
    cell = p.storyboard[2]
    assert cell.visual_desc == "新画面"
    assert cell.status == "draft" and cell.image == ""    # 画面描述变 → 重画
    assert cell.audio == "audio/page_03.mp3"              # 配音不受影响
    assert cell.duration_ms == 1003


def test_update_characters_cascades_image(tmp_path: Path):
    p = _project(tmp_path)
    editing.update_cell(p, 1, characters=["白素贞", "许仙"])
    cell = p.storyboard[0]
    assert cell.characters == ["白素贞", "许仙"]
    assert cell.status == "draft" and cell.image == ""
    assert cell.audio == "audio/page_01.mp3"


def test_update_emotion_no_cascade(tmp_path: Path):
    p = _project(tmp_path)
    editing.update_cell(p, 1, emotion="悲伤")
    cell = p.storyboard[0]
    assert cell.emotion == "悲伤"
    assert cell.status == "confirmed"                     # 情绪仅影响 BGM,不动画/音
    assert cell.image == "pages/page_01.png"
    assert cell.audio == "audio/page_01.mp3"


def test_mark_redraw_and_revoice(tmp_path: Path):
    p = _project(tmp_path)
    editing.mark_redraw(p, 2)
    assert p.storyboard[1].status == "draft" and p.storyboard[1].image == ""
    editing.mark_revoice(p, 3)
    assert p.storyboard[2].audio == "" and p.storyboard[2].duration_ms == 0
    assert p.output == {}


def test_insert_front_reindexes_and_aligns_files(tmp_path: Path):
    p = _project(tmp_path, n=3)
    editing.insert_cell(p, tmp_path, after_index=0, caption="新首页",
                        visual_desc="开场", characters=["白素贞"])
    assert [c.index for c in p.storyboard] == [1, 2, 3, 4]   # index 连续
    assert p.storyboard[0].caption == "新首页"
    assert p.storyboard[0].status == "draft"
    assert p.storyboard[0].image == "" and p.storyboard[0].audio == ""
    # 原 3 页整体后移,文件名对齐到新 index,内容跟着走
    assert p.storyboard[1].image == "pages/page_02.png"
    assert (tmp_path / "pages" / "page_02.png").read_bytes() == b"IMG1"
    assert (tmp_path / "audio" / "page_04.mp3").read_bytes() == b"AUD3"
    assert p.output == {}


def test_insert_middle(tmp_path: Path):
    p = _project(tmp_path, n=3)
    editing.insert_cell(p, tmp_path, after_index=1, caption="插入", visual_desc="v")
    assert [c.caption for c in p.storyboard] == ["cap1", "插入", "cap2", "cap3"]
    assert p.storyboard[2].image == "pages/page_03.png"
    assert (tmp_path / "pages" / "page_03.png").read_bytes() == b"IMG2"


def test_insert_bad_after_index_raises(tmp_path: Path):
    p = _project(tmp_path, n=2)
    with pytest.raises(ValueError):
        editing.insert_cell(p, tmp_path, after_index=5, caption="c", visual_desc="v")


def test_delete_removes_files_and_reindexes(tmp_path: Path):
    p = _project(tmp_path, n=3)
    editing.delete_cell(p, tmp_path, 2)
    assert [c.index for c in p.storyboard] == [1, 2]
    assert [c.caption for c in p.storyboard] == ["cap1", "cap3"]
    assert not (tmp_path / "pages" / "page_03.png").exists()   # 尾部文件被 renumber 挪走
    # 原第 3 页产物内容对齐到新 index=2
    assert (tmp_path / "pages" / "page_02.png").read_bytes() == b"IMG3"
    assert (tmp_path / "audio" / "page_02.mp3").read_bytes() == b"AUD3"
    assert p.output == {}


def test_reorder_swap_3_4_files_follow_cells(tmp_path: Path):
    p = _project(tmp_path, n=4)
    editing.reorder_cells(p, tmp_path, [1, 2, 4, 3])       # 互换 3↔4
    assert [c.caption for c in p.storyboard] == ["cap1", "cap2", "cap4", "cap3"]
    assert [c.index for c in p.storyboard] == [1, 2, 3, 4]
    # 关键:两文件内容不串,产物跟着 cell 走(两阶段重命名防互相覆盖)
    third, fourth = p.storyboard[2], p.storyboard[3]
    assert third.image == "pages/page_03.png"
    assert fourth.image == "pages/page_04.png"
    assert (tmp_path / "pages" / "page_03.png").read_bytes() == b"IMG4"   # 原第4页
    assert (tmp_path / "pages" / "page_04.png").read_bytes() == b"IMG3"   # 原第3页
    assert (tmp_path / "audio" / "page_03.mp3").read_bytes() == b"AUD4"
    assert (tmp_path / "audio" / "page_04.mp3").read_bytes() == b"AUD3"


def test_reorder_not_permutation_raises(tmp_path: Path):
    p = _project(tmp_path, n=3)
    with pytest.raises(ValueError):
        editing.reorder_cells(p, tmp_path, [1, 2, 2])     # 非全排列
    with pytest.raises(ValueError):
        editing.reorder_cells(p, tmp_path, [1, 2])        # 长度不符


def test_update_bad_index_raises(tmp_path: Path):
    p = _project(tmp_path, n=2)
    with pytest.raises(ValueError):
        editing.update_cell(p, 9, caption="x")


def test_mark_character_redraw(tmp_path: Path):
    p = _project(tmp_path, n=1)
    p.script = Script(title="t", theme="th", acts=[], characters=[
        CharacterCard(name="白素贞", role="r", personality="p", appearance="a",
                      turnaround_image="characters/白素贞.png", locked=True)])
    editing.mark_character_redraw(p, "白素贞")
    assert p.script.characters[0].locked is False          # 解锁 → s3 重画
    assert p.output == {}
    with pytest.raises(ValueError):
        editing.mark_character_redraw(p, "查无此人")

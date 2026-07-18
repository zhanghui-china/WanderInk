from pathlib import Path

import pytest

from shanhai import editing
from shanhai.schema import CharacterCard, Panel, Project, Script, StoryboardCell


def _add_panels(p: Project, tmp_path: Path, index: int, n_panels: int) -> None:
    """给第 index 页装 n_panels 个分格,并写真实分格图小文件(内容含页号+格号防串位)。"""
    cell = editing._cell_at(p, index)
    cell.panels = []
    for i in range(1, n_panels + 1):
        rel = f"pages/page_{index:02d}_panel{i}.png"
        (tmp_path / rel).write_bytes(f"P{index}-{i}".encode())
        cell.panels.append(Panel(visual_desc=f"格{i}", image=rel))


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
            audio=f"audio/page_{i:02d}.mp3", duration_ms=1000 + i,
            image_gen_ms=500 + i, status="confirmed"))
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
    assert cell.image_gen_ms == 0                          # 旧图作废,旧生成耗时也一并失效
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
    assert p.storyboard[1].image_gen_ms == 0                # 标记重绘后旧生成耗时一并失效
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


def test_page_edit_invalidates_pipeline_and_downstream_status(tmp_path: Path):
    # 联动诚实化:页级编辑(改画面)使 s4 起的下游产物过期 —— pipeline 打回 partial、
    # s4/s5/s6(含其 _elapsed_s 计时键)复位,s4 之前的上游环节(s3)保持不动,output 清空。
    p = _project(tmp_path)
    p.status = {"pipeline": "done", "s3": "done", "s4": "done", "s4_elapsed_s": "1.0",
                "s5": "done", "s6": "done"}
    editing.update_cell(p, 2, visual_desc="新画面")
    assert p.status["pipeline"] == "partial: 已编辑,待重新生成"
    assert "s4" not in p.status and "s5" not in p.status and "s6" not in p.status
    assert "s4_elapsed_s" not in p.status                  # 计时键一并复位,不留陈旧耗时
    assert p.status["s3"] == "done"                        # s4 上游不受影响
    assert p.output == {}


def test_character_redraw_invalidates_from_s3(tmp_path: Path):
    # 角色改动影响一致性锚点,须从 s3 起失效(比页级编辑多回收一环)。
    p = _project(tmp_path, n=1)
    p.script = Script(title="t", theme="th", acts=[], characters=[
        CharacterCard(name="白素贞", role="r", personality="p", appearance="a",
                      turnaround_image="characters/白素贞.png", locked=True)])
    p.status = {"pipeline": "done", "s3": "done", "s4": "done", "s5": "done", "s6": "done"}
    editing.mark_character_redraw(p, "白素贞")
    assert p.status["pipeline"] == "partial: 已编辑,待重新生成"
    assert "s3" not in p.status and "s4" not in p.status   # 从 s3 起失效
    assert p.script.characters[0].locked is False


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


def test_update_visual_desc_voids_panels(tmp_path: Path):
    # 分格页改画面描述 → 作废分格,回退单图整页重生成(s4 单图分支)。
    p = _project(tmp_path, n=3)
    _add_panels(p, tmp_path, 2, 3)
    editing.update_cell(p, 2, visual_desc="新整页画面")
    cell = p.storyboard[1]
    assert cell.panels == []                                # 分格被清空
    assert cell.status == "draft" and cell.image == ""


def test_update_characters_voids_panels(tmp_path: Path):
    p = _project(tmp_path, n=3)
    _add_panels(p, tmp_path, 2, 2)
    editing.update_cell(p, 2, characters=["白素贞"])
    assert p.storyboard[1].panels == []


def test_update_caption_keeps_panels(tmp_path: Path):
    # caption 编辑只清音轨,不影响分格。
    p = _project(tmp_path, n=3)
    _add_panels(p, tmp_path, 2, 2)
    editing.update_cell(p, 2, caption="新文案")
    assert len(p.storyboard[1].panels) == 2


def test_reorder_swap_panel_files_follow_cells(tmp_path: Path):
    # 分格页互换:每格自己的图跟着 cell 走,两阶段改名防互相覆盖。
    p = _project(tmp_path, n=4)
    _add_panels(p, tmp_path, 3, 2)     # 第3页 2 格
    _add_panels(p, tmp_path, 4, 3)     # 第4页 3 格
    editing.reorder_cells(p, tmp_path, [1, 2, 4, 3])       # 互换 3↔4
    third, fourth = p.storyboard[2], p.storyboard[3]       # 原第4页 / 原第3页
    assert [pn.image for pn in third.panels] == [
        "pages/page_03_panel1.png", "pages/page_03_panel2.png", "pages/page_03_panel3.png"]
    assert [pn.image for pn in fourth.panels] == [
        "pages/page_04_panel1.png", "pages/page_04_panel2.png"]
    # 内容不串:原第4页(P4-*)现落到 page_03_panel*,原第3页(P3-*)落到 page_04_panel*
    assert (tmp_path / "pages" / "page_03_panel3.png").read_bytes() == b"P4-3"
    assert (tmp_path / "pages" / "page_04_panel2.png").read_bytes() == b"P3-2"
    assert not (tmp_path / "pages" / "page_04_panel3.png").exists()   # 原第4页第3格已挪走


def test_delete_removes_panel_files(tmp_path: Path):
    p = _project(tmp_path, n=3)
    _add_panels(p, tmp_path, 2, 3)
    editing.delete_cell(p, tmp_path, 2)
    assert [c.index for c in p.storyboard] == [1, 2]
    # 被删页的每格图都 unlink
    for i in range(1, 4):
        assert not (tmp_path / "pages" / f"page_02_panel{i}.png").exists()

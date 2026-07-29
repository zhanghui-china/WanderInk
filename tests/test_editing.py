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


def test_mark_redraw_clears_image_route_and_lora(tmp_path: Path):
    # image_route/image_lora 描述的是刚被清掉的那张图,图没了这两个字段就是孤儿信息,
    # 必须跟 image/image_gen_ms 同进同退。
    p = _project(tmp_path)
    p.storyboard[1].image_route = "edit"
    p.storyboard[1].image_lora = "wanderink_v2"
    editing.mark_redraw(p, 2)
    assert p.storyboard[1].image_route == ""
    assert p.storyboard[1].image_lora == ""


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
    # s4/s5/s6(含其三个计时键 started_at/finished_at/elapsed_s)复位,s4 之前的上游环节
    # (s3)保持不动,output 清空。
    p = _project(tmp_path)
    p.status = {"pipeline": "done", "s3": "done", "s4": "done", "s4_elapsed_s": "1.0",
                "s4_finished_at": "2020-01-01T00:00:00+00:00",
                "s5": "done", "s5_finished_at": "2020-01-01T00:00:00+00:00", "s6": "done"}
    editing.update_cell(p, 2, visual_desc="新画面")
    assert p.status["pipeline"] == "partial: 已编辑,待重新生成"
    assert "s4" not in p.status and "s5" not in p.status and "s6" not in p.status
    assert "s4_elapsed_s" not in p.status                  # 计时键一并复位,不留陈旧耗时
    assert "s4_finished_at" not in p.status and "s5_finished_at" not in p.status
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


def test_mark_character_redraw_keeps_reference_image(tmp_path: Path):
    # mark_* 只清产物不清输入:reference_image 是用户上传的输入,重绘标记不该把它清掉,
    # 否则上传参考图后触发的自动重绘反而会把刚上传的参考图丢了。
    p = _project(tmp_path, n=1)
    p.script = Script(title="t", theme="th", acts=[], characters=[
        CharacterCard(name="白素贞", role="r", personality="p", appearance="a",
                      reference_image="characters/refs/ref_x.png",
                      turnaround_image="characters/白素贞.png", locked=True)])
    editing.mark_character_redraw(p, "白素贞")
    assert p.script.characters[0].reference_image == "characters/refs/ref_x.png"


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


def test_update_track_caption_clears_track_finished_at(tmp_path: Path):
    # 语种轨计时键回归:旧代码手写键名时漏清 track_{lang}_finished_at,悬停会残留旧的
    # 结束时刻。校对译文使该语种成片过期,须连 track_{lang} 的三个计时键一并清掉。
    p = _project(tmp_path, n=1)
    p.status = {
        "track_en_started_at": "2020-01-01T00:00:00+00:00",
        "track_en_finished_at": "2020-01-01T00:01:00+00:00",
        "track_en_elapsed_s": "60.0",
        "s6_en": "done",
    }
    p.output["mp4_en"] = "output/final_en.mp4"
    editing.update_track_caption(p, 1, "en", "new caption")
    assert p.storyboard[0].tracks["en"].caption == "new caption"
    assert "mp4_en" not in p.output and "s6_en" not in p.status
    assert "track_en_started_at" not in p.status and "track_en_elapsed_s" not in p.status
    assert "track_en_finished_at" not in p.status   # 曾经漏清的那一个


def test_update_cell_clears_image_route_and_lora(tmp_path: Path):
    """三处清 cell.image 的地方(update_cell 两个分支 + mark_redraw)必须清同一组字段。
    此前只有 mark_redraw 补了新字段,于是"改画面描述"会留下一张已被删掉的图的路径标记,
    界面照着它渲染「LoRA 未生效」——描述一张不存在的图(审计实测复现)。
    现在三处共用 _invalidate_page_image,这条守着它。"""
    for field, value in (("visual_desc", "新画面"), ("characters", ["白素贞"])):
        p = _project(tmp_path / field)
        cell = p.storyboard[0]
        cell.image, cell.image_gen_ms = "pages/page_01.png", 4200
        cell.image_route, cell.image_lora = "text2img", "figurine_qwen"
        editing.update_cell(p, 1, **{field: value})
        assert cell.image == "" and cell.image_gen_ms == 0, field
        assert cell.image_route == "" and cell.image_lora == "", field


# ---------- 补画三视图后作废其出场页 ----------

def _two_character_project() -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.script = Script(title="t", theme="th", acts=[], characters=[
        CharacterCard(name="白素贞", role="r", personality="p", appearance="a"),
        CharacterCard(name="法海", role="r", personality="p", appearance="a")])
    p.storyboard = [
        StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v", characters=chars,
                       caption="c", emotion="宁静", status="confirmed",
                       image=f"pages/page_{i:02d}.png", image_route="edit", image_gen_ms=1234)
        for i, chars in enumerate([["白素贞"], ["法海"], ["白素贞", "法海"]], 1)]
    p.status["s4"] = "done"
    p.output["mp4"] = "output/final.mp4"
    return p


def test_invalidate_pages_of_characters_hits_only_appearances():
    # 这是 8f41283a 那条最隐蔽的路径:不作废的话,补画的三视图对已 confirmed 的页毫无作用——
    # s4_pages 的幂等条件(confirmed + image + 文件在)三条全占,旧页永远停在无锚点的版本。
    p = _two_character_project()
    hit = editing.invalidate_pages_of_characters(p, {"法海"})
    assert hit == [2, 3]
    assert p.storyboard[0].status == "confirmed" and p.storyboard[0].image  # 没他的页不动
    for c in (p.storyboard[1], p.storyboard[2]):
        assert c.status == "draft" and c.image == "" and c.image_route == ""
    assert p.output == {}                                   # 成片跟着作废
    assert "s4" not in p.status


def test_turnaround_stamps_detect_rewritten_file(tmp_path):
    """判据必须是"三视图文件变了",不能是"从无到有"。

    用户点重绘 / 换参考图时,mark_character_redraw 与上传端点都**刻意保留**
    turnaround_image(清了卡片会立刻变"未生成",空窗难看)。于是 S3 重画出全新的
    三视图后,"从无到有"的差集恒为空,一页都不作废 —— 界面 s3=done、s4=done、
    全部 confirmed,新形象却一页都没出现。这正是 8f41283a 那个事故的另一条入口。"""
    p = _two_character_project()
    (tmp_path / "characters").mkdir()
    for c in p.script.characters:
        c.turnaround_image = f"characters/{c.name}.png"
        (tmp_path / c.turnaround_image).write_bytes(b"old")

    before = editing.turnaround_stamps(p, tmp_path)
    (tmp_path / "characters/法海.png").write_bytes(b"brand new")   # 只有法海被重画
    after = editing.turnaround_stamps(p, tmp_path)

    assert editing.redrawn_characters(before, after) == {"法海"}


def test_turnaround_stamps_still_catch_from_missing_to_present(tmp_path):
    """原来的"从无到有"必须仍然算数——补画首张三视图是同一件事的特例。"""
    p = _two_character_project()
    (tmp_path / "characters").mkdir()
    before = editing.turnaround_stamps(p, tmp_path)      # 两个角色都还没有三视图
    p.script.characters[0].turnaround_image = "characters/白素贞.png"
    (tmp_path / "characters/白素贞.png").write_bytes(b"new")
    after = editing.turnaround_stamps(p, tmp_path)
    assert editing.redrawn_characters(before, after) == {"白素贞"}


def test_turnaround_stamps_unchanged_file_is_not_redrawn(tmp_path):
    """S3 幂等跳过(已定稿角色一次请求都不发)时不能动任何东西,
    否则每跑一次 S3 就白毁一次成片。"""
    p = _two_character_project()
    (tmp_path / "characters").mkdir()
    for c in p.script.characters:
        c.turnaround_image = f"characters/{c.name}.png"
        (tmp_path / c.turnaround_image).write_bytes(b"same")
    before = editing.turnaround_stamps(p, tmp_path)
    assert editing.redrawn_characters(before, editing.turnaround_stamps(p, tmp_path)) == set()


def test_invalidate_pages_of_characters_noop_when_nothing_gained():
    # S3 重跑但一个角色都没从"无图"变"有图"时不能动任何东西,否则每次跑 S3 都白毁一次成片。
    p = _two_character_project()
    assert editing.invalidate_pages_of_characters(p, set()) == []
    assert all(c.status == "confirmed" for c in p.storyboard)
    assert p.output["mp4"] == "output/final.mp4"

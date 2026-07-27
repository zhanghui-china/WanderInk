import io
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image
from shanhai.providers.image import ImageGenError
from shanhai.schema import CharacterCard, Panel, Project, Script, StoryboardCell
from shanhai.steps import s4_pages
from shanhai.steps.s4_pages import MAX_ATTEMPTS

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

def _multi_panel_project(tmp_path: Path, n_panels: int = 2) -> Project:
    p = _project(tmp_path)
    p.params.multi_panel = True   # 分格分支现在同时要求这个开关,只填 panels 会被当单图页
    p.storyboard[0].panels = [
        Panel(visual_desc=f"格{i}", shot_type="medium", characters=["白素贞"])
        for i in range(1, n_panels + 1)
    ]
    return p

def test_s4_generates_and_composes(tmp_path: Path):
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "confirmed"
    assert (tmp_path / "pages" / "page_01.png").exists()
    refs = image.generate.call_args.kwargs["references"]
    assert refs and refs[0].name == "白素贞.png"      # 三视图作为参考图传入
    prompt = image.generate.call_args.args[0]
    assert "白衣女子" in prompt and "不要出现任何文字" in prompt

def test_s4_retries_then_fails(tmp_path: Path):
    image = MagicMock(); image.timeout = 600; image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 3            # 1 + 重试 2(PRD F4)
    assert p.storyboard[0].status == "failed"

def test_s4_budget_exhausted_after_one_slow_attempt_stops_retrying(tmp_path: Path):
    # 单次尝试就耗光时间预算(0.06s 睡眠 > 0.05s 预算)→ 不再发起第二次尝试
    image = MagicMock()
    image.timeout = 0.05

    def side_effect(*a, **kw):
        time.sleep(0.06)
        raise ImageGenError("boom")

    image.generate.side_effect = side_effect
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 1             # 预算耗尽,不再重试
    assert p.storyboard[0].status == "failed"


def test_s4_fast_failures_retried_up_to_max_attempts_when_budget_remains(tmp_path: Path):
    # 每次失败都很快,时间预算充裕 → 仍按 MAX_ATTEMPTS 次全部重试完
    image = MagicMock()
    image.timeout = 600
    image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == MAX_ATTEMPTS
    assert p.storyboard[0].status == "failed"


def test_s4_multi_panel_each_panel_gets_independent_budget(tmp_path: Path):
    # 第 1 格拖到预算耗尽(仅 1 次尝试即失败),不应挤占第 2 格的计时——
    # 第 2 格应独享全新的预算,能正常快速成功。
    image = MagicMock()
    image.timeout = 0.05

    def side_effect(prompt, *a, **kw):
        if "格1" in prompt:
            time.sleep(0.06)
            raise ImageGenError("boom")
        return _png()

    image.generate.side_effect = side_effect
    p = s4_pages.run(_multi_panel_project(tmp_path, 2), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 2              # 格1 耗尽预算(1次)+ 格2 立即成功(1次)
    assert p.storyboard[0].status == "confirmed"        # 格2 顶上,整页仍成功合成
    assert not (tmp_path / "pages" / "page_01_panel1.png").exists()
    assert (tmp_path / "pages" / "page_01_panel2.png").exists()


def test_s4_skips_confirmed(tmp_path: Path):
    proj = _project(tmp_path)
    proj.storyboard[0].status = "confirmed"
    proj.storyboard[0].image = "pages/page_01.png"
    (tmp_path / "pages").mkdir(parents=True)
    (tmp_path / "pages" / "page_01.png").write_bytes(_png())
    image = MagicMock(); image.timeout = 600
    s4_pages.run(proj, image, tmp_path, "1536x1024")
    image.generate.assert_not_called()               # 断点续跑:已确认且文件在则跳过


def test_s4_regenerates_confirmed_when_file_missing(tmp_path: Path):
    proj = _project(tmp_path)
    proj.storyboard[0].status = "confirmed"
    proj.storyboard[0].image = "pages/page_01.png"   # 引用的文件并不存在
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
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
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
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
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    assert "一致性" in capsys.readouterr().out         # S3 未产出三视图时告警(M0 被绕过)


def test_s4_strict_raises_when_no_turnaround(tmp_path: Path):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    card = CharacterCard(name="白素贞", role="r", personality="p", appearance="a")  # 无三视图
    p.script = Script(title="t", theme="th", acts=[], characters=[card])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=["白素贞"], caption="c", emotion="宁静")]
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    with pytest.raises(ValueError):                    # strict=True 时无三视图直接失败,堵 M0 绕过
        s4_pages.run(p, image, tmp_path, "1536x1024", strict=True)


def test_s4_parallel_all_cells_confirmed(tmp_path: Path):
    p = _project(tmp_path)
    p.storyboard = [StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v",
                                   characters=["白素贞"], caption=f"第{i}页。", emotion="宁静")
                    for i in range(1, 7)]                # 6 页并发生成
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert all(c.status == "confirmed" for c in p.storyboard)   # 全部成功
    assert p.status["s4"] == "done"
    assert all((tmp_path / "pages" / f"page_{i:02d}.png").exists() for i in range(1, 7))


def test_s4_on_progress_called_once_per_completed_cell(tmp_path: Path):
    # 每页渲染完成后应回调一次 on_progress,供调用方(api.py)据此增量落盘,
    # 让前端轮询能看到"N/M 页"实时进度,而不是整个 S4 期间冻结不变。
    p = _project(tmp_path)
    p.storyboard = [StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v",
                                   characters=["白素贞"], caption=f"第{i}页。", emotion="宁静")
                    for i in range(1, 7)]                # 6 页并发生成
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    calls = []
    p = s4_pages.run(p, image, tmp_path, "1536x1024", on_progress=lambda: calls.append(1))
    assert len(calls) == 6                               # 每页完成各回调一次,不多不少
    assert all(c.status == "confirmed" for c in p.storyboard)


def test_s4_cancel_check_stops_early(tmp_path: Path):
    # cancel_check 每次都返回 True:首页渲染完成后应立即停止,后续页不再渲染。
    p = _project(tmp_path)
    p.storyboard = [StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v",
                                   characters=["白素贞"], caption=f"第{i}页。", emotion="宁静")
                    for i in range(1, 4)]                # 3 页,全部待确认
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024", concurrency=1,
                      cancel_check=lambda: True)
    assert not all(c.status == "confirmed" for c in p.storyboard)   # 未全部完成,提前停止
    assert p.status["s4"] == "partial"


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


def test_page_tmpl_uses_species_neutral_wording():
    """PAGE_TMPL 的一致性约束不应假定角色是人类(发型/面部特征对动物角色不适用)。"""
    assert "发型" not in s4_pages.PAGE_TMPL
    assert "面部特征" not in s4_pages.PAGE_TMPL


def test_s4_multi_panel_generates_one_call_per_panel_and_composes(tmp_path: Path):
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    p = s4_pages.run(_multi_panel_project(tmp_path, 3), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 3
    assert p.storyboard[0].status == "confirmed"
    assert (tmp_path / "pages" / "page_01.png").exists()
    assert (tmp_path / "pages" / "page_01_panel1.png").exists()
    assert (tmp_path / "pages" / "page_01_panel3.png").exists()


def test_s4_multi_panel_partial_failure_still_composes(tmp_path: Path):
    # 3 格,第 2 格全部 3 次尝试都失败,第 1/3 格各一次成功——整页仍应 confirmed,
    # 排版按实际拿到的 2 格算(不拿占位图硬凑)。
    image = MagicMock(); image.timeout = 600
    calls = {"n": 0}

    def side_effect(*a, **kw):
        calls["n"] += 1
        if calls["n"] in (2, 3, 4):
            raise ImageGenError("boom")
        return _png()

    image.generate.side_effect = side_effect
    p = s4_pages.run(_multi_panel_project(tmp_path, 3), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "confirmed"
    assert image.generate.call_count == 5  # 1(格1成功) + 3(格2三次失败) + 1(格3成功)
    assert (tmp_path / "pages" / "page_01.png").exists()
    assert not (tmp_path / "pages" / "page_01_panel2.png").exists()


def test_s4_multi_panel_all_fail_marks_cell_failed(tmp_path: Path):
    image = MagicMock(); image.timeout = 600; image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_multi_panel_project(tmp_path, 2), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"
    assert not (tmp_path / "pages" / "page_01.png").exists()


def test_s4_multi_panel_prompt_includes_shot_hint(tmp_path: Path):
    p = _multi_panel_project(tmp_path, 1)
    p.storyboard[0].panels[0].shot_type = "closeup"
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    prompt = image.generate.call_args.args[0]
    assert "特写" in prompt


def test_s4_single_page_mode_unaffected(tmp_path: Path):
    # 回归:panels 为空时必须走原有单图路径,字节级行为不变
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 1
    assert p.storyboard[0].status == "confirmed"
    assert not (tmp_path / "pages" / "page_01_panel1.png").exists()


def test_s4_retries_when_image_rejected_as_framed(tmp_path: Path):
    """边框拦截(providers.image._reject_if_framed)抛的也是 ImageGenError,
    必须能接上 S4 既有的重试链,而不是新开一条路径。"""
    image = MagicMock(); image.timeout = 600
    image.generate.side_effect = ImageGenError("生成图片左右两侧都有框线,疑似被画成了漫画分格页")
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == MAX_ATTEMPTS     # 走满重试,试图换一张不带框的
    assert p.storyboard[0].status == "failed"


def test_s4_page_prompt_forbids_panel_frame():
    """两个模板都不能再出现"连环画单页"/"漫画格"这类**诱导**模型画分格边框的措辞,
    且必须带上显式的禁止边框约束。注意 NO_FRAME 里"不是漫画分格页"是**否定句**,
    出现"漫画"二字是有意的,所以这里断言的是具体诱导词而非笼统地禁"漫画"。"""
    for tmpl in (s4_pages.PAGE_TMPL, s4_pages.PANEL_TMPL):
        assert "连环画单页" not in tmpl
        assert "漫画格画面" not in tmpl
        assert "边框" in tmpl and "满幅" in tmpl
    assert "不是漫画分格页" in s4_pages.NO_FRAME    # 禁止约束本身必须在


def test_s4_ignores_panels_when_multi_panel_off(tmp_path: Path):
    """用户没开分格时,即便 cell 上有 panels(模型自作主张填的、或历史数据)也走单图路径。
    以前判据只看 cell.panels,会静默分格——与用户预期相反。"""
    p = _multi_panel_project(tmp_path, 3)
    p.params.multi_panel = False          # 关掉开关,panels 保留
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 1                      # 整页一张图,不是逐格 3 张
    assert image.generate.call_args.kwargs["size"] == "1536x1024"   # 用整页尺寸,不是版位尺寸
    assert not (tmp_path / "pages" / "page_01_panel1.png").exists()
    assert p.storyboard[0].status == "confirmed"


def test_s4_panel_sizes_follow_slot_geometry(tmp_path: Path):
    """每格按它自己的版位比例出图(而不是所有格共用整页的 3:2)——这是人脸被裁的根治手段。"""
    from shanhai import paneling
    p = _multi_panel_project(tmp_path, 3)
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    sent = [c.kwargs["size"] for c in image.generate.call_args_list]
    expect = [f"{w}x{h}" for w, h in paneling.slot_sizes(p.storyboard[0].panels)]
    assert sent == expect
    assert len(set(sent)) > 1, "3 格版式的版位尺寸本就不同,不该全部一样"
    assert "1536x1024" not in sent, "不应再退回整页尺寸"


def test_s4_records_image_gen_ms_on_success(tmp_path: Path):
    image = MagicMock(); image.timeout = 600

    def side_effect(*a, **kw):
        time.sleep(0.02)
        return _png()

    image.generate.side_effect = side_effect
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_gen_ms >= 20            # 计时覆盖实际生成调用耗时


def test_s4_no_image_gen_ms_when_compose_fails(tmp_path: Path):
    # 排版失败时图没落地,耗时也不该留在 cell 上——否则失败页会挂着一个"生成 X.Xs"
    image = MagicMock(); image.timeout = 600; image.generate.return_value = _png()
    with patch("shanhai.steps.s4_pages.typeset.compose_page",
               side_effect=OSError("磁盘满")):
        p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"
    assert p.storyboard[0].image == ""
    assert p.storyboard[0].image_gen_ms == 0


def test_s4_multi_panel_image_gen_ms_sums_each_panel(tmp_path: Path):
    image = MagicMock(); image.timeout = 600

    def side_effect(*a, **kw):
        time.sleep(0.02)
        return _png()

    image.generate.side_effect = side_effect
    p = s4_pages.run(_multi_panel_project(tmp_path, 3), image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_gen_ms >= 60             # 3 格各自 >=20ms 之和


def test_s4_image_gen_ms_overwritten_not_accumulated_on_regenerate(tmp_path: Path):
    # 重绘场景:第二次生成耗时应直接覆盖第一次的值,不是两次相加。
    proj = _project(tmp_path)
    image = MagicMock(); image.timeout = 600

    image.generate.side_effect = lambda *a, **kw: (time.sleep(0.05), _png())[1]
    s4_pages.run(proj, image, tmp_path, "1536x1024")
    first_ms = proj.storyboard[0].image_gen_ms
    assert first_ms >= 50

    proj.storyboard[0].status = "draft"                    # 模拟 mark_redraw 后重新生成
    proj.storyboard[0].image = ""
    image.generate.side_effect = lambda *a, **kw: (time.sleep(0.01), _png())[1]
    s4_pages.run(proj, image, tmp_path, "1536x1024")
    second_ms = proj.storyboard[0].image_gen_ms
    assert second_ms < first_ms                             # 被更快的第二次覆盖,不是累加变大

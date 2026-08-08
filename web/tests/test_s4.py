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

def _mock_image(timeout: float = 600) -> MagicMock:
    # 生成成功后 s4_pages 会调 image.route_for()/读 image.lora_model 落 image_route/
    # image_lora;裸 MagicMock() 返回的属性不是 str,赋给 pydantic 的 str 字段会校验报错。
    # 与路由本身无关的测试都用这个默认值,只有专门测路由/LoRA 的测试才会另外配置。
    image = MagicMock()
    image.timeout = timeout
    image.route_for.return_value = "edit"
    image.lora_model = None
    return image

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
    image = _mock_image(); image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "confirmed"
    assert (tmp_path / "pages" / "page_01.png").exists()
    refs = image.generate.call_args.kwargs["references"]
    # 参考图是三视图裁出的正面像(文件名带缓存版本号,见 _downscaled_ref 的说明)
    assert refs and refs[0].name == f"白素贞.{s4_pages.REF_CACHE_VERSION}.png"
    prompt = image.generate.call_args.args[0]
    assert "白衣女子" in prompt and "不要出现任何文字" in prompt

def test_s4_retries_then_fails(tmp_path: Path):
    image = _mock_image(); image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 3            # 1 + 重试 2(PRD F4)
    assert p.storyboard[0].status == "failed"

def test_s4_budget_exhausted_after_one_slow_attempt_stops_retrying(tmp_path: Path):
    # 单页预算 = PAGE_BUDGET_FACTOR × 单次请求超时,两者刻意分开:一个是"这次 HTTP 等多久",
    # 一个是"这一格总共值得花多久"。睡够一整个预算(0.11s > 2×0.05s)→ 不再发起第二次尝试。
    image = _mock_image(timeout=0.05)

    def side_effect(*a, **kw):
        time.sleep(0.05 * s4_pages.PAGE_BUDGET_FACTOR + 0.01)
        raise ImageGenError("boom")

    image.generate.side_effect = side_effect
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 1             # 预算耗尽,不再重试
    assert p.storyboard[0].status == "failed"


def test_s4_fast_failures_retried_up_to_max_attempts_when_budget_remains(tmp_path: Path):
    # 每次失败都很快,时间预算充裕 → 仍按 MAX_ATTEMPTS 次全部重试完
    image = _mock_image()
    image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == MAX_ATTEMPTS
    assert p.storyboard[0].status == "failed"


def test_s4_multi_panel_each_panel_gets_independent_budget(tmp_path: Path):
    # 第 1 格拖到预算耗尽(仅 1 次尝试即失败),不应挤占第 2 格的计时——
    # 第 2 格应独享全新的预算,能正常快速成功。
    image = _mock_image(timeout=0.05)

    def side_effect(prompt, *a, **kw):
        if "格1" in prompt:
            time.sleep(0.05 * s4_pages.PAGE_BUDGET_FACTOR + 0.01)   # 睡满格1 自己的整份预算
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
    image = _mock_image()
    s4_pages.run(proj, image, tmp_path, "1536x1024")
    image.generate.assert_not_called()               # 断点续跑:已确认且文件在则跳过


def test_s4_regenerates_confirmed_when_file_missing(tmp_path: Path):
    proj = _project(tmp_path)
    proj.storyboard[0].status = "confirmed"
    proj.storyboard[0].image = "pages/page_01.png"   # 引用的文件并不存在
    image = _mock_image(); image.generate.return_value = _png()
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
    image = _mock_image(); image.generate.return_value = _png()
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
    image = _mock_image(); image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    assert "一致性" in capsys.readouterr().out         # S3 未产出三视图时告警(M0 被绕过)


def test_s4_strict_raises_when_no_turnaround(tmp_path: Path):
    p = Project(project_id="x", scenic_spot="雷峰塔")
    card = CharacterCard(name="白素贞", role="r", personality="p", appearance="a")  # 无三视图
    p.script = Script(title="t", theme="th", acts=[], characters=[card])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=["白素贞"], caption="c", emotion="宁静")]
    image = _mock_image(); image.generate.return_value = _png()
    with pytest.raises(ValueError):                    # strict=True 时无三视图直接失败,堵 M0 绕过
        s4_pages.run(p, image, tmp_path, "1536x1024", strict=True)


def test_s4_parallel_all_cells_confirmed(tmp_path: Path):
    p = _project(tmp_path)
    p.storyboard = [StoryboardCell(index=i, scene_ref=f"1-{i}", visual_desc="v",
                                   characters=["白素贞"], caption=f"第{i}页。", emotion="宁静")
                    for i in range(1, 7)]                # 6 页并发生成
    image = _mock_image(); image.generate.return_value = _png()
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
    image = _mock_image(); image.generate.return_value = _png()
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
    image = _mock_image(); image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024", concurrency=1,
                      cancel_check=lambda: True)
    assert not all(c.status == "confirmed" for c in p.storyboard)   # 未全部完成,提前停止
    assert p.status["s4"] == "partial"


def test_s4_downscaled_ref_rebuilds_on_newer_source(tmp_path: Path):
    src = tmp_path / "白素贞.png"
    Image.new("RGB", (100, 100), "red").save(src, "PNG")
    cache = tmp_path / "_refs"
    out = s4_pages._downscaled_ref(src, cache)
    old = out.read_bytes()
    out_mtime = out.stat().st_mtime
    Image.new("RGB", (100, 100), "blue").save(src, "PNG")   # S3 重绘该角色
    os.utime(src, (out_mtime + 100, out_mtime + 100))
    new = s4_pages._downscaled_ref(src, cache).read_bytes()
    assert new != old                                # 源图更新后缩略图重建


def test_page_tmpl_uses_species_neutral_wording():
    """PAGE_TMPL 的一致性约束不应假定角色是人类(发型/面部特征对动物角色不适用)。"""
    assert "发型" not in s4_pages.PAGE_TMPL
    assert "面部特征" not in s4_pages.PAGE_TMPL


def test_s4_multi_panel_generates_one_call_per_panel_and_composes(tmp_path: Path):
    image = _mock_image(); image.generate.return_value = _png()
    p = s4_pages.run(_multi_panel_project(tmp_path, 3), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 3
    assert p.storyboard[0].status == "confirmed"
    assert (tmp_path / "pages" / "page_01.png").exists()
    assert (tmp_path / "pages" / "page_01_panel1.png").exists()
    assert (tmp_path / "pages" / "page_01_panel3.png").exists()


def test_s4_multi_panel_partial_failure_still_composes(tmp_path: Path):
    # 3 格,第 2 格全部 3 次尝试都失败,第 1/3 格各一次成功——整页仍应 confirmed,
    # 排版按实际拿到的 2 格算(不拿占位图硬凑)。
    image = _mock_image()
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
    image = _mock_image(); image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_multi_panel_project(tmp_path, 2), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"
    assert not (tmp_path / "pages" / "page_01.png").exists()


def test_s4_multi_panel_prompt_includes_shot_hint(tmp_path: Path):
    p = _multi_panel_project(tmp_path, 1)
    p.storyboard[0].panels[0].shot_type = "closeup"
    image = _mock_image(); image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    prompt = image.generate.call_args.args[0]
    assert "特写" in prompt


def test_s4_single_page_mode_unaffected(tmp_path: Path):
    # 回归:panels 为空时必须走原有单图路径,字节级行为不变
    image = _mock_image(); image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == 1
    assert p.storyboard[0].status == "confirmed"
    assert not (tmp_path / "pages" / "page_01_panel1.png").exists()


def test_s4_retries_when_image_rejected_as_framed(tmp_path: Path):
    """边框拦截(providers.image._reject_if_framed)抛的也是 ImageGenError,
    必须能接上 S4 既有的重试链,而不是新开一条路径。"""
    image = _mock_image()
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
    image = _mock_image(); image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert image.generate.call_count == 1                      # 整页一张图,不是逐格 3 张
    assert image.generate.call_args.kwargs["size"] == "1536x1024"   # 用整页尺寸,不是版位尺寸
    assert not (tmp_path / "pages" / "page_01_panel1.png").exists()
    assert p.storyboard[0].status == "confirmed"


def test_s4_panel_sizes_follow_slot_geometry(tmp_path: Path):
    """每格按它自己的版位比例出图(而不是所有格共用整页的 3:2)——这是人脸被裁的根治手段。"""
    from shanhai import paneling
    p = _multi_panel_project(tmp_path, 3)
    image = _mock_image(); image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    sent = [c.kwargs["size"] for c in image.generate.call_args_list]
    expect = [f"{w}x{h}" for w, h in paneling.slot_sizes(p.storyboard[0].panels)]
    assert sent == expect
    assert len(set(sent)) > 1, "3 格版式的版位尺寸本就不同,不该全部一样"
    assert "1536x1024" not in sent, "不应再退回整页尺寸"


def test_s4_records_image_gen_ms_on_success(tmp_path: Path):
    image = _mock_image()

    def side_effect(*a, **kw):
        time.sleep(0.02)
        return _png()

    image.generate.side_effect = side_effect
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_gen_ms >= 20            # 计时覆盖实际生成调用耗时


def test_s4_no_image_gen_ms_when_compose_fails(tmp_path: Path):
    # 排版失败时图没落地,耗时也不该留在 cell 上——否则失败页会挂着一个"生成 X.Xs"
    image = _mock_image(); image.generate.return_value = _png()
    with patch("shanhai.steps.s4_pages.typeset.compose_page",
               side_effect=OSError("磁盘满")):
        p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"
    assert p.storyboard[0].image == ""
    assert p.storyboard[0].image_gen_ms == 0


def test_s4_multi_panel_image_gen_ms_sums_each_panel(tmp_path: Path):
    image = _mock_image()

    def side_effect(*a, **kw):
        time.sleep(0.02)
        return _png()

    image.generate.side_effect = side_effect
    p = s4_pages.run(_multi_panel_project(tmp_path, 3), image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_gen_ms >= 60             # 3 格各自 >=20ms 之和


def test_s4_image_gen_ms_overwritten_not_accumulated_on_regenerate(tmp_path: Path):
    # 重绘场景:第二次生成耗时应直接覆盖第一次的值,不是两次相加。
    proj = _project(tmp_path)
    image = _mock_image()

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


def test_s4_records_image_route_with_references(tmp_path: Path):
    # _project 里的角色有三视图,refs 非空,image.route_for 应该照实拿到 refs 并回 "edit"
    image = _mock_image()
    image.route_for.side_effect = lambda refs: "edit" if refs else "text2img"
    image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_route == "edit"


def test_s4_records_image_route_without_references(tmp_path: Path):
    # 角色没有三视图 → refs 为空 → 走 text2img,这正是"换了 LoRA 却没生效"的那批页
    p = Project(project_id="x", scenic_spot="雷峰塔")
    card = CharacterCard(name="白素贞", role="r", personality="p", appearance="a")  # 无三视图
    p.script = Script(title="t", theme="th", acts=[], characters=[card])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥",
                                   characters=["白素贞"], caption="c", emotion="宁静")]
    image = _mock_image()
    image.route_for.side_effect = lambda refs: "edit" if refs else "text2img"
    image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_route == "text2img"


def test_s4_records_image_lora_when_set(tmp_path: Path):
    image = _mock_image()
    image.lora_model = "wanderink_v2"
    image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_lora == "wanderink_v2"


def test_s4_image_lora_empty_when_unset(tmp_path: Path):
    # 未指定 LoRA 时记空串——注意空串不代表"没用 LoRA",只是"由后端默认值决定"
    image = _mock_image()   # lora_model 默认 None
    image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_lora == ""


def test_s4_multi_panel_records_image_route_and_lora(tmp_path: Path):
    image = _mock_image()
    image.lora_model = "wanderink_v2"
    image.generate.return_value = _png()
    p = s4_pages.run(_multi_panel_project(tmp_path, 2), image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_route == "edit"
    assert p.storyboard[0].image_lora == "wanderink_v2"


def test_s4_no_image_route_or_lora_when_generation_fails(tmp_path: Path):
    # 与 image_gen_ms 同语义:失败页不留任何描述"这次生成"的字段
    image = _mock_image()
    image.lora_model = "wanderink_v2"
    image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"
    assert p.storyboard[0].image_route == ""
    assert p.storyboard[0].image_lora == ""


def test_s4_multi_panel_no_image_route_or_lora_when_all_fail(tmp_path: Path):
    image = _mock_image()
    image.lora_model = "wanderink_v2"
    image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_multi_panel_project(tmp_path, 2), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"
    assert p.storyboard[0].image_route == ""
    assert p.storyboard[0].image_lora == ""


# ---------- 审计发现的三个缺口:混合分格 / 失败留旧值 / 编辑作废 ----------

def test_s4_multi_panel_mixed_routes_records_mixed(tmp_path: Path):
    """分格页各格的参考图是按 panel.characters **逐格**算的(空集合合法),一页里完全可能
    "有人物的格走 edit、空镜格走 text2img"。此前只记最后一格,在这个 feature 最该说真话的
    混合场景上会说反话:半页没吃到 LoRA 却显示"一切正常",或反过来。
    这条用真实的 route_for 语义(按传入 references 判断),而不是常量 mock——常量 mock
    在结构上就看不见混合路径,那正是它当初漏过去的原因。"""
    p = _multi_panel_project(tmp_path, n_panels=2)
    p.storyboard[0].panels[1].characters = []      # 第二格是空镜,没有参考图 → text2img
    image = _mock_image(); image.generate.return_value = _png()
    image.route_for.side_effect = lambda refs: "edit" if refs else "text2img"
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_route == "mixed"


def test_s4_multi_panel_uniform_routes_records_that_route(tmp_path: Path):
    # 各格路径一致时不该记成 mixed(否则"部分未生效"的提示会误报到每一个分格页上)
    p = _multi_panel_project(tmp_path, n_panels=2)
    for panel in p.storyboard[0].panels:
        panel.characters = []                       # 两格都没参考图 → 都是 text2img
    image = _mock_image(); image.generate.return_value = _png()
    image.route_for.side_effect = lambda refs: "edit" if refs else "text2img"
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert p.storyboard[0].image_route == "text2img"


def test_s4_failure_clears_stale_route_from_previous_success(tmp_path: Path):
    """关键:必须从"上一次成功过"的 cell 出发。既有的失败用例都从全新空 cell 开始,
    字段本来就是空串、断言恒真——把所有写入删掉它们照样绿(审计原话)。
    这一轮没产出新图,旧的路径/LoRA 描述的是另一次生成,挂在 failed 页上就是假信息。"""
    p = _project(tmp_path)
    cell = p.storyboard[0]
    cell.image_route, cell.image_lora = "text2img", "figurine_qwen"   # 上一次成功留下的
    image = _mock_image(); image.generate.side_effect = RuntimeError("boom")
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"
    assert p.storyboard[0].image_route == "" and p.storyboard[0].image_lora == ""


# ---------- 逐页三视图锚点校验(8f41283a 事故的回归) ----------
# 旧护栏是 `if not any(c.turnaround_image for c in characters)`——**所有**角色都没图才告警。
# 实测 DGX 上的 8f41283a:3 个角色里 2 个有图,第一主角的三视图比 7 页画面晚 18~33 分钟
# 才产出,那 7 页全程无锚点而护栏一声不吭。这一组用例锁住"部分缺失"这个真实场景。

def _mixed_refs_project(tmp_path: Path) -> Project:
    """两个角色:有图的「白素贞」与无图的「法海」;三页分别是 只有图的 / 只无图的 / 混合。"""
    p = Project(project_id="x", scenic_spot="雷峰塔")
    (tmp_path / "characters").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "red").save(tmp_path / "characters" / "白素贞.png")
    have = CharacterCard(name="白素贞", role="r", personality="p", appearance="a",
                         feature_prompt="白衣女子", turnaround_image="characters/白素贞.png")
    lack = CharacterCard(name="法海", role="r", personality="p", appearance="a",
                         feature_prompt="金衣僧人")   # 三视图生成失败 → turnaround_image 为空
    p.script = Script(title="t", theme="th", acts=[], characters=[have, lack])
    p.storyboard = [
        StoryboardCell(index=1, scene_ref="1-1", visual_desc="断桥", characters=["白素贞"],
                       caption="c", emotion="宁静"),
        StoryboardCell(index=2, scene_ref="1-2", visual_desc="金山", characters=["法海"],
                       caption="c", emotion="紧张"),
        StoryboardCell(index=3, scene_ref="1-3", visual_desc="对峙", characters=["白素贞", "法海"],
                       caption="c", emotion="紧张"),
    ]
    return p


def test_s4_records_missing_refs_per_page(tmp_path: Path, capsys):
    p = _mixed_refs_project(tmp_path)
    image = _mock_image(); image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert p.storyboard[0].missing_refs == []            # 该页唯一角色有三视图
    assert p.storyboard[1].missing_refs == ["法海"]
    assert p.storyboard[2].missing_refs == ["法海"]      # 混合页只报缺的那个
    out = capsys.readouterr().out
    assert "一致性" in out and "2、3" in out             # 告警点名到页,不再是笼统一句


def test_s4_strict_raises_on_partial_missing_refs(tmp_path: Path):
    # 旧护栏在这个场景下**不会**触发(白素贞有图,any() 为真),这正是事故能发生的原因。
    p = _mixed_refs_project(tmp_path)
    image = _mock_image(); image.generate.return_value = _png()
    with pytest.raises(ValueError):
        s4_pages.run(p, image, tmp_path, "1536x1024", strict=True)


def test_s4_clears_stale_missing_refs_after_turnaround_filled(tmp_path: Path):
    # 补出三视图后重跑,旧的缺失记录必须消失——否则界面会照着它一直报"缺参考",
    # 描述的却是上一轮的状态(同 image_route/image_lora 那类陈旧元数据的教训)。
    p = _mixed_refs_project(tmp_path)
    image = _mock_image(); image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert p.storyboard[2].missing_refs == ["法海"]
    Image.new("RGB", (64, 64), "green").save(tmp_path / "characters" / "法海.png")
    p.script.characters[1].turnaround_image = "characters/法海.png"
    for c in p.storyboard:            # 模拟 editing.invalidate_pages_of_characters 的效果
        c.status, c.image = "draft", ""
    p = s4_pages.run(p, image, tmp_path, "1536x1024")
    assert all(c.missing_refs == [] for c in p.storyboard)


# ---------- 参考图裁成单一正面像(同一角色画多次的回归) ----------
# S3 的三视图是"同一角色正面/侧面/背面并排"的设定图,整张喂进 image edit 工作流时
# 传递的是**结构**:实测泰山那部作品抽样 7 页有 5 页出现同一角色画两三次,
# 其中一页直接是三个同款冠袍男子并排、恰好一正面一侧面一背面。

def _sheet(tmp_path: Path, w: int = 1260, h: int = 840) -> Path:
    """仿三视图设定图:左中右三段涂不同颜色,便于断言裁到的是最左那段(正面)。"""
    img = Image.new("RGB", (w, h), "white")
    for i, color in enumerate(("red", "green", "blue")):
        img.paste(Image.new("RGB", (w // 3, h), color), (i * (w // 3), 0))
    p = tmp_path / "sheet.png"
    img.save(p)
    return p


def test_downscaled_ref_crops_to_front_view(tmp_path: Path):
    src = _sheet(tmp_path)
    out = s4_pages._downscaled_ref(src, tmp_path / "_refs")
    got = Image.open(out)
    src_ratio = 1260 / 840
    # 裁掉侧面/背面后宽高比应缩到约原图的 FRONT_VIEW_RATIO 倍
    assert got.width / got.height == pytest.approx(src_ratio * s4_pages.FRONT_VIEW_RATIO, rel=0.05)
    # 且内容是最左那段(正面像),不是中间的侧面或右边的背面
    assert got.convert("RGB").getpixel((got.width // 4, got.height // 2))[0] > 200   # 偏红


def test_downscaled_ref_cache_name_carries_version(tmp_path: Path):
    # 线上 _refs/ 里已有一批**旧的整张缩略图**,mtime 还比源文件新。缓存名不带版本的话
    # 新裁切逻辑会直接复用旧文件、改动完全不生效——是那种"跑完一切正常、就是没效果"的静默失效。
    src = _sheet(tmp_path)
    cache = tmp_path / "_refs"
    stale = cache / src.name          # 旧版命名:与源文件同名
    cache.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (900, 600), "black").save(stale)
    out = s4_pages._downscaled_ref(src, cache)
    assert out != stale
    assert s4_pages.REF_CACHE_VERSION in out.name
    assert Image.open(out).convert("RGB").getpixel((10, 10))[0] > 200   # 不是那张黑图


def test_downscaled_ref_reuses_cache(tmp_path: Path):
    src = _sheet(tmp_path)
    cache = tmp_path / "_refs"
    first = s4_pages._downscaled_ref(src, cache)
    mtime = first.stat().st_mtime_ns
    assert s4_pages._downscaled_ref(src, cache) == first
    assert first.stat().st_mtime_ns == mtime      # 命中缓存,没重裁


# ---------- 出图前去名化(中文名被当成画面内容的回归) ----------
# 「小虎」这类角色名原样进 prompt,文生图模型会照字面画出一只老虎。
# 名字对成图没有任何用处(身份锚是参考图),故 features 与 scene 两处一并换成中性代号。

def test_anonymize_handles_overlapping_name_prefixes():
    # 必须按名字长度降序替换,否则「小虎」会先把「小虎子」截成「角色甲子」
    cards = [
        CharacterCard(name="小虎", role="r", personality="p", appearance="a",
                      feature_prompt="十岁男童"),
        CharacterCard(name="小虎子", role="r", personality="p", appearance="a",
                      feature_prompt="八岁女童"),
    ]
    features, scene = s4_pages._anonymize(cards, "小虎子牵着小虎走过山门")
    # 断言必须锁住"谁被换成谁":乱序替换时 scene 会变成「角色甲子牵着角色甲走过山门」,
    # 只断言集合关系的话该用例照样绿,等于没有回归保护。
    assert scene == "角色乙牵着角色甲走过山门"
    assert features == "角色甲(十岁男童);角色乙(八岁女童)"


def test_anonymize_leaves_same_named_common_words_alone():
    """替换面收窄到"该页出场的角色":cast 里别页的角色名不参与 scene 替换。

    中文人名与普通名词重合率很高(石头/铁牛/大山/小雨)。按 cast 全表替换时,
    「石头」这个别页角色会把本页的「石头台阶」改写成「角色甲台阶」——台阶凭空消失,
    画面语义被静默篡改,不报错、只是"画得不对",比名字被画成实物更难归因。"""
    stone = CharacterCard(name="石头", role="r", personality="p", appearance="a",
                          feature_prompt="憨厚少年")
    tiger = CharacterCard(name="小虎", role="r", personality="p", appearance="a",
                          feature_prompt="十岁男童")
    _features, scene = s4_pages._anonymize([tiger], "小虎坐在长满青苔的石头台阶上",
                                           cast=[tiger, stone])
    assert scene == "角色甲坐在长满青苔的石头台阶上"


def test_anonymize_protects_longer_unreplaced_name_from_being_chopped():
    """cast 里未出场的名字不被替换,但必须"占住"文本不被出场角色的短名切开。

    present 有「小龙」时,若不把 cast 的「小龙女」一起纳入最长匹配,scene 里的
    「小龙女」会被截成「角色甲女」——半个名字加一个代号,比不换更糟。
    未出场的「小龙女」原样留在 prompt 里是**已知且接受的代价**:替换面收窄到出场角色
    是为了不误伤同名普通词(见 test_anonymize_leaves_same_named_common_words_alone),
    而 S2 本就被要求不在 visual_desc 里写角色名,写了属于违规。"""
    cards = [CharacterCard(name=n, role="r", personality="p", appearance="a",
                           feature_prompt="F" + n) for n in ("小龙", "小龙女")]
    features, scene = s4_pages._anonymize([cards[0]], "小龙女递给小龙一柄剑", cards)
    assert scene == "小龙女递给角色甲一柄剑"     # 出场的换掉;未出场的完整保留,没被截碎
    assert features == "角色甲(F小龙)"          # 未出场角色不进 features


def test_anonymize_ignores_empty_name():
    """空名字必须跳过:str.replace("") 会在每个字之间插一次代号,把整段 scene 炸成
    「角色甲少角色甲年角色甲推…」,该页 prompt 直接报废且不抛异常、不进 status。
    CharacterCard.name 没有 min_length,模型返回空名是可能的。"""
    cards = [
        CharacterCard(name="", role="r", personality="p", appearance="a", feature_prompt="无名"),
        CharacterCard(name="小虎", role="r", personality="p", appearance="a",
                      feature_prompt="十岁男童"),
    ]
    _features, scene = s4_pages._anonymize(cards, "小虎推开山门")
    # 空名不占代号,「小虎」仍拿第一个;关键是 scene 除了这个名字之外一字未动
    assert scene == "角色甲推开山门"


def test_anonymize_masks_names_without_cards():
    # storyboard 写了 cast 名单里没有的名字(模型改名/用别称),cards 查不到就被静默丢掉,
    # 该名字原样留在 visual_desc 里直接进 image prompt。
    features, scene = s4_pages._anonymize([], "小虎站在少林寺山门前", names=["小虎"])
    assert "小虎" not in scene
    assert features == "无固定角色"


def test_s4_masks_storyboard_name_missing_from_cast(tmp_path: Path):
    p = _tiger_project(tmp_path)
    p.storyboard[0].characters = ["虎娃"]
    p.storyboard[0].visual_desc = "虎娃站在少林寺山门前"
    image = _mock_image(); image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    assert "虎娃" not in image.generate.call_args.args[0]


def test_s4_panel_masks_other_character_in_same_frame(tmp_path: Path):
    # panel.characters 是页面角色的子集,但 panel.visual_desc 常提到同框的另一角色。
    p = _tiger_project(tmp_path)
    p.params.multi_panel = True
    p.script.characters.append(
        CharacterCard(name="阿虎", role="r", personality="p", appearance="a",
                      feature_prompt="老僧"))
    p.storyboard[0].characters = ["小虎", "阿虎"]
    p.storyboard[0].panels = [Panel(visual_desc="小虎与阿虎隔着山门对望",
                                    shot_type="medium", characters=["小虎"])]
    image = _mock_image(); image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    prompt = image.generate.call_args.args[0]
    assert "小虎" not in prompt and "阿虎" not in prompt


def test_anonymize_degrades_when_more_characters_than_aliases():
    cards = [CharacterCard(name=f"角色{i}号", role="r", personality="p", appearance="a",
                           feature_prompt=f"特征{i}") for i in range(12)]
    features, scene = s4_pages._anonymize(cards, "众人齐聚")   # 不该 IndexError
    assert features.count(";") == 11


def _tiger_project(tmp_path: Path) -> Project:
    """角色名「小虎」——原样进 prompt 就会被画成老虎。"""
    p = Project(project_id="x", scenic_spot="少林寺")
    (tmp_path / "characters").mkdir(parents=True, exist_ok=True)
    (tmp_path / "characters" / "小虎.png").write_bytes(_png())
    card = CharacterCard(name="小虎", role="r", personality="p", appearance="a",
                         feature_prompt="十岁男童,灰布僧衣",
                         turnaround_image="characters/小虎.png", locked=True)
    p.script = Script(title="t", theme="th", acts=[], characters=[card])
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="小虎站在少林寺山门前",
                                   characters=["小虎"], caption="c", emotion="宁静")]
    return p


def test_s4_single_page_prompt_has_no_character_name(tmp_path: Path):
    image = _mock_image(); image.generate.return_value = _png()
    s4_pages.run(_tiger_project(tmp_path), image, tmp_path, "1536x1024")
    prompt = image.generate.call_args.args[0]
    assert "小虎" not in prompt                 # scene 与 features 两处都不能漏
    assert "角色甲" in prompt
    assert "十岁男童,灰布僧衣" in prompt          # 外观特征仍要保留


def test_s4_panel_prompt_has_no_character_name(tmp_path: Path):
    p = _tiger_project(tmp_path)
    p.params.multi_panel = True
    p.storyboard[0].panels = [Panel(visual_desc="小虎推开山门", shot_type="medium",
                                    characters=["小虎"])]
    image = _mock_image(); image.generate.return_value = _png()
    s4_pages.run(p, image, tmp_path, "1536x1024")
    prompt = image.generate.call_args.args[0]
    assert "小虎" not in prompt
    assert "角色甲" in prompt


def test_page_prompts_forbid_duplicate_instances():
    # 与裁切互补的语义约束。两条都要锁:少了"只出现一次"压不住分身,
    # 少了"仅用于识别身份"模型会把"保持一致"理解成"贴近这张参考图(含它的排版)"。
    for tmpl in (s4_pages.PAGE_TMPL, s4_pages.PANEL_TMPL):
        assert "只出现一次" in tmpl
        assert "仅用于识别角色的外观身份" in tmpl
        assert "单一瞬间" in tmpl


def test_s4_still_rejects_framed_pages_at_its_own_call_site(tmp_path: Path):
    """边框判据从共享 provider 移到 S4 调用点后,S4 侧的拦截必须一字不差地保留——
    26% 的线上成图曾被画上分格边框,这道拦截是真在干活的。"""
    from tests.test_s3 import _framed_png_bytes
    image = _mock_image()
    image.generate.return_value = _framed_png_bytes()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert image.generate.call_count == MAX_ATTEMPTS      # 每次都被判不合格,重试到上限
    assert p.storyboard[0].status == "failed"


def test_s4_redrawn_page_still_renders_as_panels(tmp_path: Path):
    """端到端守住用户报的那件事:分格页重绘后 S4 仍走分格分支,不能悄悄变成单图整页。

    只断言 editing 不清 panels 是不够的——真正决定版式的是 s4_pages 的
    `if cell.panels and multi_panel`,得让这两半接上才算修好。"""
    from shanhai import editing
    p = _multi_panel_project(tmp_path, 3)
    p.storyboard[0].status = "confirmed"
    p.storyboard[0].image = "pages/page_01.png"

    editing.mark_redraw(p, 1)                      # 用户点「重绘」,什么内容都没改

    image = _mock_image(); image.generate.return_value = _png()
    p = s4_pages.run(p, image, tmp_path, "1536x1024")   # multi_panel 取自 project.params
    assert image.generate.call_count == 3          # 三格各生成一次,不是整页一次
    assert (tmp_path / "pages" / "page_01_panel3.png").exists()
    assert p.storyboard[0].status == "confirmed"


def test_s4_records_the_prompt_actually_sent(tmp_path: Path):
    """落盘的必须是**真正发出去的那串**,不是 visual_desc。

    两者之间隔着画风、匿名化后的角色代号、镜头提示与两百多字固定约束。2026-08-08 排查
    「躺在白娘子怀里画不出来」时,想知道到底发出去了什么只能靠读代码逐段反推——那次之后
    才把它存下来。所以这条断言的重点是"与 image.generate 收到的第一个参数逐字相同"。"""
    image = _mock_image()
    image.generate.return_value = _png()
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    sent = image.generate.call_args[0][0]
    assert p.storyboard[0].image_prompt == sent
    assert p.storyboard[0].image_prompt != p.storyboard[0].visual_desc   # 不是原文
    assert p.storyboard[0].visual_desc  # 前提:原文非空,否则上一条恒真


def test_s4_multi_panel_records_prompt_per_panel_not_on_the_page(tmp_path: Path):
    """分格页每格各记各的;整页那个字段恒空——一页多格根本没有"一条"提示词,
    写任何一格的都是说谎。界面按 panels[].image_prompt 逐格展示。"""
    image = _mock_image()
    image.generate.return_value = _png()
    p = s4_pages.run(_multi_panel_project(tmp_path, 2), image, tmp_path, "1536x1024")
    cell = p.storyboard[0]
    assert cell.image_prompt == ""
    assert len(cell.panels) == 2
    assert all(pn.image_prompt for pn in cell.panels)
    # 两格的提示词必须不同(各自的 visual_desc 与镜头提示都不一样),相同说明串了
    assert cell.panels[0].image_prompt != cell.panels[1].image_prompt


def test_s4_clears_prompt_when_generation_fails(tmp_path: Path):
    """与 image_route/image_lora 同生共死:失败页不留描述上一次生成的陈旧提示词。"""
    image = _mock_image()
    image.generate.side_effect = ImageGenError("boom")
    p = s4_pages.run(_project(tmp_path), image, tmp_path, "1536x1024")
    assert p.storyboard[0].status == "failed"
    assert p.storyboard[0].image_prompt == ""

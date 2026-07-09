from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from shanhai.cli import app
from shanhai.schema import Legend, Project, StoryboardCell

runner = CliRunner()

_STEP_MODS = ("s0_legend", "s1_script", "s2_storyboard", "s3_characters",
              "s4_pages", "s5_audio", "s6_compose")


def _stub_settings():
    return MagicMock(base_url="https://p/v1", api_key="sk", llm_model="m",
                     image_model="im", image_api_mode="chat_api", image_size="1536x1024",
                     tts_model="t", tts_voice="alloy",
                     image_endpoint=("https://p/v1", "sk"), tts_endpoint=("https://p/v1", "sk"))


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.s0_legend")
@patch("shanhai.cli.store")
def test_new_prints_candidates(store, s0):
    proj = Project(project_id="ab12cd34", scenic_spot="雷峰塔")
    proj.legend_candidates = [Legend(title="白蛇传", summary="s",
                                     source_type="民间传说", sources=["x"])]
    store.create_project.return_value = proj
    s0.run.return_value = proj
    result = runner.invoke(app, ["new", "雷峰塔"])
    assert result.exit_code == 0
    assert "白蛇传" in result.output and "ab12cd34" in result.output


def _proj_with_candidates(n=1):
    proj = Project(project_id="ab12cd34", scenic_spot="雷峰塔")
    proj.legend_candidates = [
        Legend(title=f"传说{i}", summary="s", source_type="民间传说", sources=["x"])
        for i in range(1, n + 1)
    ]
    return proj


@patch("shanhai.cli.store")
def test_pick_selects_candidate(store):
    proj = _proj_with_candidates(2)
    store.load.return_value = proj
    result = runner.invoke(app, ["pick", "ab12cd34", "2"])
    assert result.exit_code == 0
    assert proj.legend.title == "传说2"
    store.save.assert_called_once()


@patch("shanhai.cli.store")
def test_pick_index_zero_rejected(store):
    proj = _proj_with_candidates(1)
    store.load.return_value = proj
    result = runner.invoke(app, ["pick", "ab12cd34", "0"])
    assert result.exit_code != 0
    assert proj.legend is None
    store.save.assert_not_called()


@patch("shanhai.cli.store")
def test_pick_out_of_range_rejected(store):
    proj = _proj_with_candidates(1)
    store.load.return_value = proj
    result = runner.invoke(app, ["pick", "ab12cd34", "9"])
    assert result.exit_code != 0
    store.save.assert_not_called()


@patch("shanhai.cli.store")
def test_pick_empty_candidates_rejected(store):
    store.load.return_value = _proj_with_candidates(0)
    result = runner.invoke(app, ["pick", "ab12cd34", "1"])
    assert result.exit_code != 0
    store.save.assert_not_called()


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.store")
def test_new_missing_story_file_no_orphan(store):
    result = runner.invoke(app, ["new", "雷峰塔", "--story-file", "/no/such.txt"])
    assert result.exit_code != 0
    store.create_project.assert_not_called()


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.store")
def test_new_non_utf8_story_file_no_orphan(store, tmp_path):
    bad = tmp_path / "story.txt"
    bad.write_bytes("雷峰塔的传说".encode("gbk"))
    result = runner.invoke(app, ["new", "雷峰塔", "--story-file", str(bad)])
    assert result.exit_code != 0
    store.create_project.assert_not_called()


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.store")
def test_new_rejects_bad_minutes_no_orphan(store):
    result = runner.invoke(app, ["new", "雷峰塔", "--minutes", "2"])
    assert result.exit_code != 0
    store.create_project.assert_not_called()          # 非法值绝不落盘


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.store")
def test_run_rejects_bad_style_no_orphan(store):
    result = runner.invoke(app, ["run", "雷峰塔", "--style", "foo"])
    assert result.exit_code != 0
    store.create_project.assert_not_called()


def _run_project(s4_status="done", cell_status="confirmed"):
    p = _proj_with_candidates(1)
    p.status = {k: "done" for k in ("s0", "s1", "s2", "s3", "s4", "s5", "s6")}
    p.status["s4"] = s4_status
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v", characters=[],
                                   caption="c", emotion="宁静", image="pages/page_01.png",
                                   audio="audio/page_01.mp3", duration_ms=6800,
                                   status=cell_status)]
    p.output = {"mp4": "projects/x/output/final.mp4"}
    return p


def _patch_steps(stack, s0_return):
    for name in _STEP_MODS:
        stack.enter_context(patch(f"shanhai.cli.{name}")).run.return_value = s0_return


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.store")
def test_run_happy_path_reports_mp4(store):
    p = _run_project()
    store.create_project.return_value = p
    with ExitStack() as stack:
        _patch_steps(stack, p)                        # s0.run 返回 p(run 会 rebind)
        result = runner.invoke(app, ["run", "雷峰塔"])
    assert result.exit_code == 0
    assert "final.mp4" in result.output


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.store")
def test_run_all_failed_s4_reports_failure(store):
    p = _run_project(s4_status="partial", cell_status="failed")
    store.create_project.return_value = p
    with ExitStack() as stack:
        _patch_steps(stack, p)
        result = runner.invoke(app, ["run", "雷峰塔"])
    assert result.exit_code != 0                      # 零正文页不得报成功
    assert "未生成任何正文页" in result.output
    assert "partial" in result.output                 # 每步 status 如实告警


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.s1_script")
@patch("shanhai.cli.store")
def test_step_dispatches_named_stage(store, s1):
    p = _proj_with_candidates(1)
    p.status = {"s1": "done"}
    store.load.return_value = p
    s1.run.return_value = p
    result = runner.invoke(app, ["step", "ab12cd34", "s1"])
    assert result.exit_code == 0
    s1.run.assert_called_once()
    assert "s1 -> done" in result.output


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.store")
def test_status(store):
    proj = Project(project_id="ab12cd34", scenic_spot="雷峰塔")
    proj.status = {"s0": "done", "s1": "done"}
    store.load.return_value = proj
    result = runner.invoke(app, ["status", "ab12cd34"])
    assert result.exit_code == 0 and "s1" in result.output

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from shanhai.cli import app
from shanhai.schema import Legend, Project

runner = CliRunner()


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
def test_status(store):
    proj = Project(project_id="ab12cd34", scenic_spot="雷峰塔")
    proj.status = {"s0": "done", "s1": "done"}
    store.load.return_value = proj
    result = runner.invoke(app, ["status", "ab12cd34"])
    assert result.exit_code == 0 and "s1" in result.output

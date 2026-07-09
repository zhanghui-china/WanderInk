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


@patch("shanhai.cli.Settings", _stub_settings)
@patch("shanhai.cli.store")
def test_status(store):
    proj = Project(project_id="ab12cd34", scenic_spot="雷峰塔")
    proj.status = {"s0": "done", "s1": "done"}
    store.load.return_value = proj
    result = runner.invoke(app, ["status", "ab12cd34"])
    assert result.exit_code == 0 and "s1" in result.output

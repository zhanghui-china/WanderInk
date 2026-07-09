import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx, respx
from shanhai.providers.tts import TTSClient
from shanhai.schema import Project, StoryboardCell
from shanhai.steps import s5_audio

BASE = "https://p.example.com/v1"


@respx.mock
def test_tts_client(tmp_path: Path):
    respx.post(f"{BASE}/audio/speech").mock(
        return_value=httpx.Response(200, content=b"mp3bytes"))
    TTSClient(BASE, "sk", "tts-1").synthesize("你好", "alloy", tmp_path / "a.mp3")
    assert (tmp_path / "a.mp3").read_bytes() == b"mp3bytes"


def _project() -> Project:
    p = Project(project_id="x", scenic_spot="雷峰塔")
    p.storyboard = [StoryboardCell(index=1, scene_ref="1-1", visual_desc="v",
                                   characters=[], caption="西湖初遇。", emotion="宁静")]
    return p


@patch("shanhai.steps.s5_audio.probe_duration_ms", return_value=6800)
def test_s5_fills_duration_and_bgm(mock_probe, tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [
        {"file": "calm.mp3", "emotions": ["宁静"], "license": "CC0"}]}), encoding="utf-8")
    tts = MagicMock()
    p = s5_audio.run(_project(), tts, "alloy", tmp_path, manifest_path=manifest)
    assert p.storyboard[0].duration_ms == 6800
    assert p.bgm.endswith("calm.mp3")
    assert p.status["s5"] == "done"

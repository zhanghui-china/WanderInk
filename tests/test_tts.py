import json
from unittest.mock import patch

import httpx, respx, pytest

from shanhai.providers.tts import TTSClient, TTSError

BASE = "https://p.example.com/v1"


def _mp3() -> httpx.Response:
    return httpx.Response(200, content=b"\xff\xf3\x44shanhai-mp3",
                          headers={"content-type": "audio/mpeg"})


@respx.mock
@patch("shanhai.providers.tts.time.sleep")
def test_synthesize_retries_transient_then_succeeds(_sleep, tmp_path):
    route = respx.post(f"{BASE}/audio/speech")
    route.side_effect = [httpx.Response(503, text="资源不足"), _mp3()]
    out = tmp_path / "a.mp3"
    TTSClient(BASE, "sk", "m").synthesize("你好", "alloy", out)
    assert route.call_count == 2                       # 503 后重试成功
    assert out.read_bytes().startswith(b"\xff\xf3")


@respx.mock
def test_synthesize_does_not_retry_400(tmp_path):
    route = respx.post(f"{BASE}/audio/speech").mock(
        return_value=httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(httpx.HTTPStatusError):
        TTSClient(BASE, "sk", "m").synthesize("x", "alloy", tmp_path / "b.mp3")
    assert route.call_count == 1                       # 400 不可重试,立即抛


@respx.mock
def test_synthesize_rejects_non_audio_json(tmp_path):
    respx.post(f"{BASE}/audio/speech").mock(
        return_value=httpx.Response(200, json={"error": "model busy"}))
    with pytest.raises(TTSError):                      # 小模型返回 JSON 错误体 → TTSError
        TTSClient(BASE, "sk", "m").synthesize("x", "alloy", tmp_path / "c.mp3")


@respx.mock
def test_synthesize_sends_speed_in_body(tmp_path):
    route = respx.post(f"{BASE}/audio/speech").mock(return_value=_mp3())
    TTSClient(BASE, "sk", "m").synthesize("你好", "alloy", tmp_path / "d.mp3", speed=1.5)
    assert json.loads(route.calls[0].request.content)["speed"] == 1.5

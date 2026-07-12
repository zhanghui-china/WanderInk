import json
from unittest.mock import patch

import httpx, respx, pytest

from shanhai.providers.music import MusicClient, MusicError

BASE = "https://p.example.com/v1"


def _mp3() -> httpx.Response:
    return httpx.Response(200, content=b"\xff\xf3\x44shanhai-mp3",
                          headers={"content-type": "audio/mpeg"})


@respx.mock
@patch("shanhai.providers._http.time.sleep")
def test_generate_retries_transient_then_succeeds(_sleep, tmp_path):
    route = respx.post(f"{BASE}/audio/music")
    route.side_effect = [httpx.Response(503, text="资源不足"), _mp3()]
    out = tmp_path / "bgm.mp3"
    MusicClient(BASE, "sk", "m").generate("Style: Cinematic", 60.0, out)
    assert route.call_count == 2                       # 503 后重试成功
    assert out.read_bytes().startswith(b"\xff\xf3")


@respx.mock
@patch("shanhai.providers._http.time.sleep")
def test_generate_retries_on_transport_error(_sleep, tmp_path):
    route = respx.post(f"{BASE}/audio/music")
    route.side_effect = [
        httpx.RemoteProtocolError("Server disconnected without sending a response"), _mp3()]
    out = tmp_path / "e.mp3"
    MusicClient(BASE, "sk", "m").generate("Style: Cinematic", 60.0, out)
    assert route.call_count == 2
    assert out.read_bytes().startswith(b"\xff\xf3")


@respx.mock
def test_generate_does_not_retry_400(tmp_path):
    route = respx.post(f"{BASE}/audio/music").mock(
        return_value=httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(httpx.HTTPStatusError):
        MusicClient(BASE, "sk", "m").generate("x", 60.0, tmp_path / "b.mp3")
    assert route.call_count == 1                       # 400 不可重试,立即抛


@respx.mock
def test_generate_rejects_non_audio_json(tmp_path):
    respx.post(f"{BASE}/audio/music").mock(
        return_value=httpx.Response(200, json={"error": "gpu busy"}))
    with pytest.raises(MusicError):                     # shim 返回 JSON 错误体 → MusicError
        MusicClient(BASE, "sk", "m").generate("x", 60.0, tmp_path / "c.mp3")


@respx.mock
def test_generate_sends_prompt_lyrics_duration_in_body(tmp_path):
    route = respx.post(f"{BASE}/audio/music").mock(return_value=_mp3())
    MusicClient(BASE, "sk", "m").generate("Style: Cinematic", 60.0, tmp_path / "d.mp3")
    body = json.loads(route.calls[0].request.content)
    assert body["prompt"] == "Style: Cinematic"
    assert body["duration_s"] == 60.0
    assert body["lyrics"] == "[instrumental]"           # 默认纯器乐
    assert "bpm" not in body                            # 未传 bpm 时不进请求体


@respx.mock
def test_generate_includes_bpm_when_given(tmp_path):
    route = respx.post(f"{BASE}/audio/music").mock(return_value=_mp3())
    MusicClient(BASE, "sk", "m").generate("x", 60.0, tmp_path / "f.mp3", bpm=90)
    assert json.loads(route.calls[0].request.content)["bpm"] == 90

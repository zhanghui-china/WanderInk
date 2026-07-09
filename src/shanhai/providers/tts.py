# src/shanhai/providers/tts.py
from pathlib import Path

import httpx


class TTSError(Exception):
    pass


class TTSClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.model = model
        self._client = httpx.Client(base_url=base_url.rstrip("/"),
                                    headers={"Authorization": f"Bearer {api_key}"}, timeout=300)

    def synthesize(self, text: str, voice: str, out: Path) -> None:
        r = self._client.post("/audio/speech", json={
            "model": self.model, "voice": voice, "input": text, "response_format": "mp3"})
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        body = r.content
        if not ctype.startswith("audio") or not body or body[:1] == b"{":
            raise TTSError(f"TTS 返回非音频响应 (content-type={ctype!r}): {body[:200]!r}")
        out.write_bytes(body)

# src/shanhai/providers/tts.py
from pathlib import Path

import httpx

from shanhai.providers._http import local_backend_guard, request_with_retry


class TTSError(Exception):
    pass


class TTSClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.model = model
        self._base_url = base_url
        self._client = httpx.Client(base_url=base_url.rstrip("/"),
                                    headers={"Authorization": f"Bearer {api_key}"}, timeout=300)

    def synthesize(self, text: str, voice: str, out: Path, retries: int = 2,
                   speed: float = 1.0) -> None:
        with local_backend_guard(self._base_url):
            r = request_with_retry(lambda: self._client.post("/audio/speech", json={
                "model": self.model, "voice": voice, "input": text, "response_format": "mp3",
                "speed": speed}), retries)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "").lower()
        body = r.content
        if not body or "json" in ctype or ctype.startswith("text") or body[:1] == b"{":
            raise TTSError(f"TTS 返回非音频响应 (content-type={ctype!r}): {body[:200]!r}")
        out.write_bytes(body)

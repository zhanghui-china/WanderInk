# src/shanhai/providers/tts.py
import time
from pathlib import Path

import httpx

_TRANSIENT = {429, 500, 502, 503, 504}  # 代理瞬时过载可重试;400 不可重试(与 llm/image 一致)


class TTSError(Exception):
    pass


class TTSClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.model = model
        self._client = httpx.Client(base_url=base_url.rstrip("/"),
                                    headers={"Authorization": f"Bearer {api_key}"}, timeout=300)

    def synthesize(self, text: str, voice: str, out: Path, retries: int = 2) -> None:
        for attempt in range(retries + 1):
            r = self._client.post("/audio/speech", json={
                "model": self.model, "voice": voice, "input": text, "response_format": "mp3"})
            if r.status_code in _TRANSIENT and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower()
            body = r.content
            if not body or "json" in ctype or ctype.startswith("text") or body[:1] == b"{":
                raise TTSError(f"TTS 返回非音频响应 (content-type={ctype!r}): {body[:200]!r}")
            out.write_bytes(body)
            return
        raise TTSError("unreachable")  # pragma: no cover

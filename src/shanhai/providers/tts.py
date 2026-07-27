# src/shanhai/providers/tts.py
from pathlib import Path

import httpx

from shanhai.providers._http import request_with_retry


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
        r = request_with_retry(lambda: self._client.post("/audio/speech", json={
            "model": self.model, "voice": voice, "input": text, "response_format": "mp3",
            "speed": speed}), retries, base_url=self._base_url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "").lower()
        body = r.content
        if not body or "json" in ctype or ctype.startswith("text") or body[:1] == b"{":
            raise TTSError(f"TTS 返回非音频响应 (content-type={ctype!r}): {body[:200]!r}")
        out.write_bytes(body)

    def register_clone_voice(self, wav: bytes, retries: int = 1) -> str:
        """把参考录音注册成一个音色,返回可直接当 voice 用的字符串(形如 `clone:xxx.wav`)。

        为什么要有这一步、而不是每次合成都带上音频:后端把参考音频放进 ComfyUI 的 input/ 后
        只认文件名,注册一次拿到句柄之后,**synthesize 这条路一行都不用改**——voice 本来就是
        一个五层透传、没有任何一层解释它的裸字符串,`clone:` 前缀天然向后兼容。

        retries 默认 1(不是 2):注册会真的往上游写一个文件,重试多了只会堆垃圾。"""
        r = request_with_retry(
            lambda: self._client.post("/voices/clone",
                                      files={"file": ("sample.wav", wav, "audio/wav")}),
            retries, idempotent=False, base_url=self._base_url)
        r.raise_for_status()
        try:
            voice = r.json()["voice"]
        except Exception as e:  # noqa: BLE001 —— 上游返回什么都可能,统一包成 TTSError
            raise TTSError(f"音色注册返回异常: {r.content[:200]!r}") from e
        if not voice:
            raise TTSError("音色注册未返回 voice")
        return voice

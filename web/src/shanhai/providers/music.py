# src/shanhai/providers/music.py
from pathlib import Path

import httpx

from shanhai.providers._http import request_with_retry


class MusicError(Exception):
    pass


class MusicClient:
    """本机 ACE-Step 音乐生成客户端(经 DGX 侧 music-shim 转发到 ComfyUI)。
    "直接写文件"签名同 TTSClient.synthesize;非音频响应校验规则同款。
    timeout 默认远高于 TTS/Image(600s,而非硬编码 300):ACE-Step 扩散生成 + ComfyUI
    队列等待可达数分钟,且与 image-shim(另一用户的 ComfyUI)共享同一张 GPU,排队更慢。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 600):
        self.model = model
        self._base_url = base_url
        self._client = httpx.Client(base_url=base_url.rstrip("/"),
                                    headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)

    def generate(self, prompt: str, duration_s: float, out: Path, retries: int = 1,
                bpm: int | None = None, lyrics: str = "[instrumental]") -> None:
        """prompt:风格标签文本;duration_s:目标时长(秒);lyrics 默认纯器乐(是否真的让
        ACE-Step 产出无人声需部署阶段实测验证,此处只留了参数化的口子)。retries 默认=1
        (低于 TTS/Image 的 2):单次生成可能耗时数十秒到数分钟,盲目重试会成倍拖长调用方
        (S5)的耗时,失败应尽快向上抛,交给调用方的降级逻辑而不是在这层死磕。"""
        body = {"model": self.model, "prompt": prompt, "lyrics": lyrics, "duration_s": duration_s}
        if bpm is not None:
            body["bpm"] = bpm
        # idempotent=False:音乐合成非幂等,连接层断连可能已被上游受理并计费,不盲重试;
        # 仅明确的瞬时状态码(429/5xx)重试。
        r = request_with_retry(lambda: self._client.post("/audio/music", json=body), retries,
                               idempotent=False, base_url=self._base_url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "").lower()
        body_bytes = r.content
        if not body_bytes or "json" in ctype or ctype.startswith("text") or body_bytes[:1] == b"{":
            raise MusicError(f"音乐生成返回非音频响应 (content-type={ctype!r}): {body_bytes[:200]!r}")
        out.write_bytes(body_bytes)

# src/shanhai/providers/image.py
import base64
import re
from pathlib import Path

import httpx


class ImageGenError(Exception):
    pass


class ImageClient:
    """OpenAI 兼容图像客户端,双上游形态。未来本地 ComfyUI 实现同签名 generate() 即可整体替换。"""

    def __init__(self, base_url: str, api_key: str, model: str, mode: str = "images_api"):
        self.model = model
        self.mode = mode
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300,
        )

    def generate(self, prompt: str, size: str = "1536x1024",
                 references: list[Path] | None = None) -> bytes:
        if self.mode == "chat_api":
            return self._via_chat(prompt, references or [])
        if references:
            return self._via_edits(prompt, references, size)
        return self._via_generations(prompt, size)

    def _via_generations(self, prompt: str, size: str) -> bytes:
        r = self._client.post("/images/generations",
                              json={"model": self.model, "prompt": prompt, "size": size, "n": 1})
        r.raise_for_status()
        return _decode(r.json()["data"][0])

    def _via_edits(self, prompt: str, references: list[Path], size: str) -> bytes:
        files = [("image[]", (p.name, p.read_bytes(), "image/png")) for p in references]
        r = self._client.post("/images/edits",
                              data={"model": self.model, "prompt": prompt, "size": size},
                              files=files)
        r.raise_for_status()
        return _decode(r.json()["data"][0])

    def _via_chat(self, prompt: str, references: list[Path]) -> bytes:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for p in references:
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        r = self._client.post("/chat/completions", json={
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
        })
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        for img in msg.get("images") or []:
            url = img.get("image_url", {}).get("url", "")
            if url.startswith("data:image"):
                return base64.b64decode(url.split(",", 1)[1])
        m = re.search(r"data:image/\w+;base64,([A-Za-z0-9+/=]+)", msg.get("content") or "")
        if m:
            return base64.b64decode(m.group(1))
        raise ImageGenError(f"响应中未找到图像: {str(msg)[:200]}")


def _decode(item: dict) -> bytes:
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        r = httpx.get(item["url"], timeout=120)
        r.raise_for_status()
        return r.content
    raise ImageGenError(f"未知的响应格式: {list(item)}")

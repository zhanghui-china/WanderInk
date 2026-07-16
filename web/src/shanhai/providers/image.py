# src/shanhai/providers/image.py
import base64
import io
import re
from pathlib import Path

import httpx
from PIL import Image, ImageStat

from shanhai.providers._http import request_with_retry

# 低于此值判定为近似纯色/异常(NaN 解码静默转黑图,见 2026-07-13 DGX 实测:ComfyUI 的
# VAE 解码输出 NaN 时,np.clip(NaN,0,255).astype(uint8) 静默转 0,execution_success 照常上报)。
# 纯黑图 stddev 恰好是 0.0;真实插画哪怕大片留白,主体的色彩/线条变化也远高于这个值。
MIN_CHANNEL_STDDEV = 2.0


class ImageGenError(Exception):
    pass


def _reject_if_blank(data: bytes) -> bytes:
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        stddev = ImageStat.Stat(im).stddev
    except Exception as e:  # noqa: BLE001 无法解码本身也是一种生成异常,统一包成 ImageGenError
        raise ImageGenError(f"生成图片无法解码: {e}") from e
    if max(stddev) < MIN_CHANNEL_STDDEV:
        raise ImageGenError(f"生成图片近似纯色(stddev={[round(s, 2) for s in stddev]}),疑似解码异常")
    return data


class ImageClient:
    """OpenAI 兼容图像客户端,双上游形态。未来本地 ComfyUI 实现同签名 generate() 即可整体替换。"""

    def __init__(self, base_url: str, api_key: str, model: str, mode: str = "images_api",
                 timeout: float = 600):
        self.model = model
        self.mode = mode
        self._base_url = base_url
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,  # 本地 ComfyUI 扩散生成 + 队列可达数分钟(SHANHAI_IMAGE_TIMEOUT)
        )

    def generate(self, prompt: str, size: str = "1536x1024",
                 references: list[Path] | None = None, retries: int = 2) -> bytes:
        # 网络调用统一走 request_with_retry(idempotent=False:生成非幂等,连接层断连可能已被
        # 上游受理并计费,不盲重试,仅瞬时状态码 429/5xx 重试);非瞬时 HTTPStatusError(如内容
        # 审核 400)在各 _via_* 里 raise_for_status() 后直接抛出。
        # _reject_if_blank 做最后一道内容合理性检查:异常抛 ImageGenError,交给调用方
        # (S3/S4 现有的 except Exception 重试/降级逻辑)接管,不在此重试。
        return _reject_if_blank(self._dispatch(prompt, size, references, retries))

    def _dispatch(self, prompt: str, size: str, references: list[Path] | None,
                  retries: int) -> bytes:
        if self.mode == "chat_api":
            return self._via_chat(prompt, references or [], retries)
        if references:
            return self._via_edits(prompt, references, size, retries)
        return self._via_generations(prompt, size, retries)

    def _via_generations(self, prompt: str, size: str, retries: int) -> bytes:
        r = request_with_retry(lambda: self._client.post(
            "/images/generations",
            json={"model": self.model, "prompt": prompt, "size": size, "n": 1}),
            retries, idempotent=False, base_url=self._base_url)
        r.raise_for_status()
        return _decode(_first(r.json()))

    def _via_edits(self, prompt: str, references: list[Path], size: str, retries: int) -> bytes:
        files = [("image[]", (p.name, p.read_bytes(), "image/png")) for p in references]
        r = request_with_retry(lambda: self._client.post(
            "/images/edits",
            data={"model": self.model, "prompt": prompt, "size": size}, files=files),
            retries, idempotent=False, base_url=self._base_url)
        r.raise_for_status()
        return _decode(_first(r.json()))

    def _via_chat(self, prompt: str, references: list[Path], retries: int) -> bytes:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for p in references:
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}})
        r = request_with_retry(lambda: self._client.post("/chat/completions", json={
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
        }), retries, idempotent=False, base_url=self._base_url)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        for img in msg.get("images") or []:
            url = img.get("image_url", {}).get("url", "")
            if url.startswith("data:image"):
                return base64.b64decode(url.split(",", 1)[1])
            if url.startswith("http"):
                return _decode({"url": url})
        m = re.search(r"data:image/\w+;base64,([A-Za-z0-9+/=]+)", msg.get("content") or "")
        if m:
            return base64.b64decode(m.group(1))
        raise ImageGenError(f"响应中未找到图像: {str(msg)[:200]}")


def _first(resp: dict) -> dict:
    data = resp.get("data")
    if not data:
        raise ImageGenError(f"响应中无 data: {str(resp)[:200]}")
    return data[0]


def _decode(item: dict) -> bytes:
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        # 下载也走瞬时重试:免因单次抖断而回退到"重出整张图"(会重复计费)
        r = request_with_retry(lambda: httpx.get(item["url"], timeout=120), retries=2)
        r.raise_for_status()
        return r.content
    raise ImageGenError(f"未知的响应格式: {list(item)}")

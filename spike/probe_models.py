# spike/probe_models.py
"""探测代理对候选图像模型支持哪种端点。用法:
uv run python spike/probe_models.py gpt-image-1 gemini-2.5-flash-image seedream-4-0
"""
import sys
from pathlib import Path

from shanhai.config import Settings
from shanhai.providers.image import ImageClient, ImageGenError

OUT = Path("spike/out/probe"); OUT.mkdir(parents=True, exist_ok=True)

def main() -> None:
    s = Settings()
    base, key = s.image_endpoint
    for model in sys.argv[1:] or [s.image_model]:
        for mode in ("images_api", "chat_api"):
            try:
                png = ImageClient(base, key, model, mode).generate("一只红色的猫,简笔画")
                (OUT / f"{model}--{mode}.png").write_bytes(png)
                print(f"OK   {model} [{mode}] -> {len(png)} bytes")
            except Exception as e:  # noqa: BLE001 探测脚本要吞掉一切错误继续
                print(f"FAIL {model} [{mode}]: {type(e).__name__}: {str(e)[:120]}")

if __name__ == "__main__":
    main()

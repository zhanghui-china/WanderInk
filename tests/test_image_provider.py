# tests/test_image_provider.py
import base64
from pathlib import Path
import respx, httpx, pytest
from shanhai.providers.image import ImageClient, ImageGenError

PNG = base64.b64encode(b"fakepng").decode()
BASE = "https://p.example.com/v1"


@respx.mock
def test_generations_b64():
    respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat") == b"fakepng"


@respx.mock
def test_edits_with_reference(tmp_path: Path):
    ref = tmp_path / "ref.png"; ref.write_bytes(b"refpng")
    route = respx.post(f"{BASE}/images/edits").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat", references=[ref]) == b"fakepng"
    assert b"refpng" in route.calls[0].request.content


@respx.mock
def test_chat_mode_images_field():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "",
            "images": [{"image_url": {"url": f"data:image/png;base64,{PNG}"}}]}}]}))
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api")
    assert c.generate("a cat") == b"fakepng"


@respx.mock
def test_chat_mode_no_image_raises():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "我无法生成图片"}}]}))
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api")
    with pytest.raises(ImageGenError):
        c.generate("a cat")

# tests/test_image_provider.py
import base64
from pathlib import Path
from unittest.mock import patch
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
@patch("shanhai.providers.image.time.sleep")
def test_generate_retries_on_timeout(mock_sleep):
    route = respx.post(f"{BASE}/images/generations")
    route.side_effect = [httpx.ReadTimeout("slow"),
                         httpx.Response(200, json={"data": [{"b64_json": PNG}]})]
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat") == b"fakepng"           # 超时后重试成功
    assert route.call_count == 2


@respx.mock
@patch("shanhai.providers.image.time.sleep")
def test_generate_retries_on_remote_protocol_error(mock_sleep):
    # "Server disconnected without sending a response" 是 RemoteProtocolError,
    # 曾被窄写的 except (TimeoutException, ConnectError) 漏抓,单次瞬时故障直接杀死整条 S3
    route = respx.post(f"{BASE}/images/generations")
    route.side_effect = [
        httpx.RemoteProtocolError("Server disconnected without sending a response"),
        httpx.Response(200, json={"data": [{"b64_json": PNG}]})]
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat") == b"fakepng"
    assert route.call_count == 2


@respx.mock
def test_generate_does_not_retry_400():
    route = respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(400, json={"error": {"message": "not supported"}}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    with pytest.raises(httpx.HTTPStatusError):
        c.generate("a cat")
    assert route.call_count == 1                        # 400 不可重试


@respx.mock
def test_edits_with_reference(tmp_path: Path):
    ref = tmp_path / "ref.png"; ref.write_bytes(b"refpng")
    route = respx.post(f"{BASE}/images/edits").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat", references=[ref]) == b"fakepng"
    assert b"refpng" in route.calls[0].request.content


@respx.mock
def test_generations_empty_b64_falls_back_to_url():
    # 真实代理(tu-zi gpt-image-2)同时返回空 b64_json 和有效 url
    respx.post(f"{BASE}/images/generations").mock(return_value=httpx.Response(200, json={
        "data": [{"b64_json": "", "url": "https://img.example.com/x.png", "revised_prompt": "r"}]}))
    respx.get("https://img.example.com/x.png").mock(
        return_value=httpx.Response(200, content=b"realpng"))
    c = ImageClient(BASE, "sk", "gpt-image-2", mode="images_api")
    assert c.generate("a cat") == b"realpng"


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


@respx.mock
def test_generations_empty_data_raises():
    # 内容被拦截时代理常返回 200 + 空 data,应包成 ImageGenError 而非 IndexError
    respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": []}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    with pytest.raises(ImageGenError):
        c.generate("a cat")


@respx.mock
def test_chat_mode_http_url_downloaded():
    # 部分 chat_api 模型在 images[] 里回传普通 https 链接而非 data: base64
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "",
            "images": [{"image_url": {"url": "https://img.example.com/x.png"}}]}}]}))
    respx.get("https://img.example.com/x.png").mock(
        return_value=httpx.Response(200, content=b"realpng"))
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api")
    assert c.generate("a cat") == b"realpng"

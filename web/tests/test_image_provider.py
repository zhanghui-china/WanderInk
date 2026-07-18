# tests/test_image_provider.py
import base64
import io
import json
from pathlib import Path
from unittest.mock import patch
import respx, httpx, pytest
from PIL import Image
from shanhai.providers.image import ImageClient, ImageGenError


def _tiny_png() -> bytes:
    """4x4 四色小图,有实质像素方差,满足 generate() 内置的合理性检查。
    替代此前 b"fakepng"/b"realpng" 占位符(非法图片格式,会被新检查误判)。"""
    im = Image.new("RGB", (4, 4))
    for xy, color in zip([(0, 0), (1, 0), (0, 1), (1, 1)],
                         [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]):
        im.putpixel(xy, color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _solid_png(color=(0, 0, 0)) -> bytes:
    """近似纯色图,模拟 VAE 解码出 NaN 被静默转黑图的失败场景(见 2026-07-13 DGX 实测)。"""
    im = Image.new("RGB", (100, 100), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


REAL_PNG = _tiny_png()
PNG = base64.b64encode(REAL_PNG).decode()
BASE = "https://p.example.com/v1"


@respx.mock
def test_generations_b64():
    respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat") == REAL_PNG


@respx.mock
@patch("shanhai.providers._http.time.sleep")
def test_generate_retries_transient_status(mock_sleep):
    # 生成非幂等,但明确的瞬时状态码(请求未被成功受理、重试安全)仍应重试
    route = respx.post(f"{BASE}/images/generations")
    route.side_effect = [httpx.Response(503, text="busy"),
                         httpx.Response(200, json={"data": [{"b64_json": PNG}]})]
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat") == REAL_PNG           # 5xx 后重试成功
    assert route.call_count == 2


@respx.mock
@patch("shanhai.providers._http.time.sleep")
def test_generate_does_not_retry_transport_error(mock_sleep):
    # 生成非幂等(idempotent=False):连接层断连(含已发出请求后的 ReadTimeout / 服务端中途断连)
    # 可能已被上游受理并计费,不得盲重试——叠加 S4 外层 MAX_ATTEMPTS 会让单格最坏计费 9 次。
    route = respx.post(f"{BASE}/images/generations")
    route.side_effect = [
        httpx.RemoteProtocolError("Server disconnected without sending a response"),
        httpx.Response(200, json={"data": [{"b64_json": PNG}]})]
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    with pytest.raises(httpx.RemoteProtocolError):
        c.generate("a cat")
    assert route.call_count == 1                        # 连接层错误不重试


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
    assert c.generate("a cat", references=[ref]) == REAL_PNG
    assert b"refpng" in route.calls[0].request.content


@respx.mock
def test_generations_empty_b64_falls_back_to_url():
    # 真实代理(tu-zi gpt-image-2)同时返回空 b64_json 和有效 url
    respx.post(f"{BASE}/images/generations").mock(return_value=httpx.Response(200, json={
        "data": [{"b64_json": "", "url": "https://img.example.com/x.png", "revised_prompt": "r"}]}))
    respx.get("https://img.example.com/x.png").mock(
        return_value=httpx.Response(200, content=REAL_PNG))
    c = ImageClient(BASE, "sk", "gpt-image-2", mode="images_api")
    assert c.generate("a cat") == REAL_PNG


@respx.mock
def test_chat_mode_images_field():
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "",
            "images": [{"image_url": {"url": f"data:image/png;base64,{PNG}"}}]}}]}))
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api")
    assert c.generate("a cat") == REAL_PNG


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
        return_value=httpx.Response(200, content=REAL_PNG))
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api")
    assert c.generate("a cat") == REAL_PNG


def test_timeout_is_configurable():
    # 超时可配(方案A):默认 1200(20分钟,与 Settings.image_timeout 一致),
    # 构造传入的值应落到底层 httpx.Client
    default = ImageClient(BASE, "sk", "gpt-image-1")
    assert default._client.timeout.read == 1200
    custom = ImageClient(BASE, "sk", "gpt-image-1", timeout=120)
    assert custom._client.timeout.read == 120


@respx.mock
def test_generations_includes_lora_model_name_when_set():
    route = respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "comfyui-local", mode="images_api",
                    lora_model="Real_Ani-Qwen_000001250.safetensors")
    c.generate("a cat")
    assert json.loads(route.calls[0].request.content)["lora_model_name"] == \
        "Real_Ani-Qwen_000001250.safetensors"


@respx.mock
def test_generations_omits_lora_model_name_when_unset():
    route = respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    c.generate("a cat")
    assert "lora_model_name" not in json.loads(route.calls[0].request.content)


@respx.mock
def test_edits_includes_lora_model_name_when_set(tmp_path: Path):
    ref = tmp_path / "ref.png"; ref.write_bytes(b"refpng")
    route = respx.post(f"{BASE}/images/edits").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "comfyui-local", mode="images_api",
                    lora_model="figurine_qwen.safetensors")
    c.generate("a cat", references=[ref])
    body = route.calls[0].request.content.decode("utf-8", errors="ignore")
    assert "figurine_qwen.safetensors" in body


@respx.mock
def test_chat_mode_includes_lora_model_name_when_set():
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "",
            "images": [{"image_url": {"url": f"data:image/png;base64,{PNG}"}}]}}]}))
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api",
                    lora_model="Real_Ani-Qwen_000001250.safetensors")
    c.generate("a cat")
    assert json.loads(route.calls[0].request.content)["lora_model_name"] == \
        "Real_Ani-Qwen_000001250.safetensors"


def test_timeout_attribute_reflects_constructor_arg():
    # image.timeout 暴露给 S4 的重试预算计时逻辑读取,须原样落到实例属性上
    c = ImageClient(BASE, "sk", "gpt-image-1", timeout=123)
    assert c.timeout == 123


@respx.mock
def test_generate_rejects_near_solid_color_image():
    # 模拟 DGX 实测的 VAE 解码 NaN 静默转黑图场景:HTTP 200 + 结构合法,但像素近似纯色
    solid = base64.b64encode(_solid_png()).decode()
    respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": solid}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    with pytest.raises(ImageGenError):
        c.generate("a cat")

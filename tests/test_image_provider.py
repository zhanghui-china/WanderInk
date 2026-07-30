# tests/test_image_provider.py
import base64
import io
import json
import re
from pathlib import Path
from unittest.mock import patch
import respx, httpx, pytest
from PIL import Image, ImageDraw
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
def test_generations_includes_lora_when_set():
    route = respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "comfyui-local", mode="images_api",
                    lora_model="Real_ani_qwen")
    c.generate("a cat")
    assert json.loads(route.calls[0].request.content)["lora"] == "Real_ani_qwen"


@respx.mock
def test_generations_omits_lora_when_unset():
    route = respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    c.generate("a cat")
    assert "lora" not in json.loads(route.calls[0].request.content)


@respx.mock
def test_edits_includes_lora_when_set(tmp_path: Path):
    ref = tmp_path / "ref.png"; ref.write_bytes(b"refpng")
    route = respx.post(f"{BASE}/images/edits").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": PNG}]}))
    c = ImageClient(BASE, "sk", "comfyui-local", mode="images_api",
                    lora_model="figurine_qwen")
    c.generate("a cat", references=[ref])
    body = route.calls[0].request.content.decode("utf-8", errors="ignore")
    # multipart 里字段名和值要一起断言:只 grep 值的话字段名写错也照样绿
    assert re.search(r'name="lora"\r\n\r\nfigurine_qwen\r\n', body)


@respx.mock
def test_chat_mode_includes_lora_when_set():
    route = respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": "",
            "images": [{"image_url": {"url": f"data:image/png;base64,{PNG}"}}]}}]}))
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api",
                    lora_model="Real_ani_qwen")
    c.generate("a cat")
    assert json.loads(route.calls[0].request.content)["lora"] == "Real_ani_qwen"


def test_timeout_attribute_reflects_constructor_arg():
    # image.timeout 暴露给 S4 的重试预算计时逻辑读取,须原样落到实例属性上
    c = ImageClient(BASE, "sk", "gpt-image-1", timeout=123)
    assert c.timeout == 123


def _framed_png(w=1920, h=1080, margin=52, line=6) -> bytes:
    """模拟"模型自己画了分格边框"的图:浅色留白 + 左右各一条深色竖框线 + 框内有内容。
    上下边框故意不画——真实线上就是这样(3:2 生成图被裁成 16:9 时上下框线一起被裁掉了)。"""
    im = Image.new("RGB", (w, h), (250, 249, 246))
    d = ImageDraw.Draw(im)
    d.rectangle((margin, 0, margin + line, h), fill=(12, 12, 12))
    d.rectangle((w - margin - line, 0, w - margin, h), fill=(12, 12, 12))
    d.ellipse((w // 3, h // 3, w * 2 // 3, h * 2 // 3), fill=(180, 90, 40))  # 框内内容
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return buf.getvalue()


def _scene_png(w=1920, h=1080) -> bytes:
    """正常满幅场景图:横向渐变铺满,四周无留白无框线。"""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for x in range(w):
        c = (round(255 * x / (w - 1)), 90, 255 - round(255 * x / (w - 1)))
        for y in range(h):
            px[x, y] = c
    buf = io.BytesIO(); im.save(buf, format="PNG")
    return buf.getvalue()


def test_reject_if_framed_catches_self_drawn_panel_frame():
    # 模型常自作主张把画面画成漫画分格页(旧提示词"连环画单页/漫画格"是诱因,
    # 实测 26% 的线上成图中招)。提示词已改,这是第二道拦截。
    from shanhai.providers.image import reject_if_framed
    with pytest.raises(ImageGenError, match="分格"):
        reject_if_framed(_framed_png())


def test_reject_if_framed_accepts_normal_full_bleed_image():
    # 比"能拦住"更要紧的一条:正常满幅图**不能**被误拦——误报会白烧一次重生成。
    from shanhai.providers.image import reject_if_framed
    assert reject_if_framed(_scene_png())


@respx.mock
def test_generate_does_not_apply_frame_check():
    """边框判据是**S4 页面**的合格性标准(阈值取自 646 个成图页样本),不该挂在共享的
    generate() 上:S3 的三视图是"纯白背景 + 三个全身像并排",左右留白极易被判成竖框线。
    线上真的因此误杀过——用户传的参考图被静默丢弃、角色退化成纯文字特征。
    判据本身没问题,位置错了,故移到 s4_pages 的调用点。"""
    b64 = base64.b64encode(_framed_png()).decode()
    respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": b64}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    assert c.generate("a cat")           # 带框的图在 provider 层照常放行


def test_frame_check_needs_both_sides():
    # 只有一侧有暗带(如画面本身左边是深色物体)不该判为边框,否则误报率会很高
    from shanhai.providers.image import reject_if_framed
    im = Image.open(io.BytesIO(_framed_png())).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rectangle((im.width - 60, 0, im.width, im.height), fill=(250, 249, 246))  # 抹掉右框线
    buf = io.BytesIO(); im.save(buf, format="PNG")
    assert reject_if_framed(buf.getvalue())   # 单侧不拦


@respx.mock
def test_generate_rejects_near_solid_color_image():
    # 模拟 DGX 实测的 VAE 解码 NaN 静默转黑图场景:HTTP 200 + 结构合法,但像素近似纯色
    solid = base64.b64encode(_solid_png()).decode()
    respx.post(f"{BASE}/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": solid}]}))
    c = ImageClient(BASE, "sk", "gpt-image-1", mode="images_api")
    with pytest.raises(ImageGenError):
        c.generate("a cat")


# --- route_for 与 _dispatch 同源 ---
# 只测 route_for 返回什么是不够的:那样两边各自漂移也照样绿。这里断言"route_for 说走哪条,
# _dispatch 就真的只调了那条对应的私有方法",三种入参各测一遍,把两者钉在一起。
_ROUTE_TO_METHOD = {"chat": "_via_chat", "edit": "_via_edits", "text2img": "_via_generations"}


def _dispatch_calls(client: ImageClient, references):
    """返回 (route_for 的返回值, _dispatch 实际调用的那个私有方法名)。"""
    with patch.object(ImageClient, "_via_chat", return_value=REAL_PNG) as chat, \
            patch.object(ImageClient, "_via_edits", return_value=REAL_PNG) as edits, \
            patch.object(ImageClient, "_via_generations", return_value=REAL_PNG) as gens:
        client._dispatch("a cat", "1536x1024", references, 2)
        called = [name for name, m in (("_via_chat", chat), ("_via_edits", edits),
                                       ("_via_generations", gens)) if m.called]
    assert len(called) == 1, f"应恰好走一条路,实际调用了 {called}"
    return client.route_for(references), called[0]


def test_route_text2img_matches_dispatch():
    c = ImageClient(BASE, "sk", "comfyui-local", mode="images_api")
    route, method = _dispatch_calls(c, None)
    assert route == "text2img"
    assert method == _ROUTE_TO_METHOD[route]


def test_route_edit_matches_dispatch(tmp_path: Path):
    ref = tmp_path / "ref.png"; ref.write_bytes(b"refpng")
    c = ImageClient(BASE, "sk", "comfyui-local", mode="images_api")
    route, method = _dispatch_calls(c, [ref])
    assert route == "edit"
    assert method == _ROUTE_TO_METHOD[route]


def test_route_chat_matches_dispatch_regardless_of_references(tmp_path: Path):
    # chat_api 下有无参考图都走 chat,两种入参都要钉住
    ref = tmp_path / "ref.png"; ref.write_bytes(b"refpng")
    c = ImageClient(BASE, "sk", "nano-banana", mode="chat_api")
    for references in (None, [ref]):
        route, method = _dispatch_calls(c, references)
        assert route == "chat"
        assert method == _ROUTE_TO_METHOD[route]

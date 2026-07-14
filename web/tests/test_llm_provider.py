import json
from unittest.mock import patch
import httpx, respx, pytest
from pydantic import BaseModel
from shanhai.providers.llm import LLMClient, LLMError, _extract_json

BASE = "https://p.example.com/v1"

class Pet(BaseModel):
    name: str
    age: int

def _resp(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

@respx.mock
@patch("shanhai.providers._http.time.sleep")
def test_chat_retries_transient_then_succeeds(mock_sleep):
    route = respx.post(f"{BASE}/chat/completions")
    route.side_effect = [httpx.Response(503, text="资源不足"), _resp("好")]
    assert LLMClient(BASE, "sk", "m").chat("sys", "user") == "好"
    assert route.call_count == 2                      # 503 后重试成功

@respx.mock
@patch("shanhai.providers._http.time.sleep")
def test_chat_retries_on_transport_error(mock_sleep):
    # chat() 之前对 self._client.post() 无任何 try/except:连接被对端掐断
    # (RemoteProtocolError 等)会直接不重试地传播,DGX 经隧道链路更易触发
    route = respx.post(f"{BASE}/chat/completions")
    route.side_effect = [
        httpx.RemoteProtocolError("Server disconnected without sending a response"), _resp("好")]
    assert LLMClient(BASE, "sk", "m").chat("sys", "user") == "好"
    assert route.call_count == 2

@respx.mock
def test_chat_does_not_retry_400():
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "model not supported"}}))
    with pytest.raises(httpx.HTTPStatusError):
        LLMClient(BASE, "sk", "m").chat("sys", "user")
    assert route.call_count == 1                      # 400 不可重试,立即抛

@respx.mock
def test_structured_with_code_fence():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=_resp('好的:\n```json\n{"name": "咪咪", "age": 3}\n```'))
    pet = LLMClient(BASE, "sk", "m").structured("sys", "user", Pet)
    assert pet.name == "咪咪"

@respx.mock
def test_structured_retries_on_invalid():
    route = respx.post(f"{BASE}/chat/completions")
    route.side_effect = [_resp("不是 JSON"), _resp(json.dumps({"name": "咪咪", "age": 3}))]
    pet = LLMClient(BASE, "sk", "m").structured("sys", "user", Pet)
    assert pet.age == 3 and route.call_count == 2

@respx.mock
def test_structured_exhausts_retries():
    respx.post(f"{BASE}/chat/completions").mock(return_value=_resp("永远不是 JSON"))
    with pytest.raises(LLMError):
        LLMClient(BASE, "sk", "m").structured("sys", "user", Pet, retries=1)


@respx.mock
def test_structured_retries_on_http_error():
    route = respx.post(f"{BASE}/chat/completions")
    route.side_effect = [httpx.Response(500), _resp(json.dumps({"name": "咪咪", "age": 3}))]
    pet = LLMClient(BASE, "sk", "m").structured("sys", "user", Pet)
    assert pet.age == 3 and route.call_count == 2   # 5xx 被重试而非逃逸


@respx.mock
def test_structured_wraps_null_content():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
    with pytest.raises(LLMError):                     # null content 不以 TypeError 逃逸
        LLMClient(BASE, "sk", "m").structured("sys", "user", Pet, retries=1)


@respx.mock
def test_structured_wraps_empty_choices():
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []}))
    with pytest.raises(LLMError):                     # 空 choices 包成 LLMError 而非 IndexError
        LLMClient(BASE, "sk", "m").structured("sys", "user", Pet, retries=1)


@respx.mock
def test_structured_does_not_retry_400():
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "model not supported"}}))
    with pytest.raises(httpx.HTTPStatusError):         # 认证/请求错误不可重试,原样向上抛
        LLMClient(BASE, "sk", "m").structured("sys", "user", Pet)
    assert route.call_count == 1                       # 不当作"输出不合法"重发


def test_extract_json_ignores_trailing_text():
    text = '{"name": "咪咪", "age": 3} 说明:以上为结果'
    assert _extract_json(text) == '{"name": "咪咪", "age": 3}'


def test_extract_json_handles_braces_inside_strings():
    # 字段值里含 { / } 不应破坏抽取——raw_decode 原生理解字符串字面量;手搓深度计数器会误判丢弃。
    text = '{"note": "用 { 表示对象、} 收尾"} 尾随说明'
    assert _extract_json(text) == '{"note": "用 { 表示对象、} 收尾"}'

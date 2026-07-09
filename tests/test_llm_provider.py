import json
from unittest.mock import patch
import httpx, respx, pytest
from pydantic import BaseModel
from shanhai.providers.llm import LLMClient, LLMError

BASE = "https://p.example.com/v1"

class Pet(BaseModel):
    name: str
    age: int

def _resp(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

@respx.mock
@patch("shanhai.providers.llm.time.sleep")
def test_chat_retries_transient_then_succeeds(mock_sleep):
    route = respx.post(f"{BASE}/chat/completions")
    route.side_effect = [httpx.Response(503, text="资源不足"), _resp("好")]
    assert LLMClient(BASE, "sk", "m").chat("sys", "user") == "好"
    assert route.call_count == 2                      # 503 后重试成功

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

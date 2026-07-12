import json
from unittest.mock import patch

import httpx, respx, pytest
from pydantic import BaseModel

from shanhai.providers.llm import LLMError
from shanhai.providers.llm_ollama import OllamaLLMClient

BASE = "http://dgx.example.com:11434/v1"   # 配置常带 /v1,适配器应剥掉走原生 API


class Pet(BaseModel):
    name: str
    age: int


def _resp(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


@respx.mock
def test_chat_hits_native_api_with_think_false():
    route = respx.post("http://dgx.example.com:11434/api/chat").mock(return_value=_resp("好"))
    assert OllamaLLMClient(BASE, "ollama", "m").chat("sys", "user") == "好"
    body = json.loads(route.calls[0].request.content)
    assert body["think"] is False                     # 关思考:10× 提速的关键
    assert body["stream"] is False
    assert "format" not in body                       # 普通 chat 不约束


@respx.mock
def test_structured_sends_json_schema_as_format():
    route = respx.post("http://dgx.example.com:11434/api/chat").mock(
        return_value=_resp(json.dumps({"name": "咪咪", "age": 3})))
    pet = OllamaLLMClient(BASE, "ollama", "m").structured("sys", "user", Pet)
    assert pet.age == 3
    body = json.loads(route.calls[0].request.content)
    assert body["format"]["properties"].keys() == {"name", "age"}   # schema 约束解码


@respx.mock
@patch("shanhai.providers._http.time.sleep")
def test_chat_retries_transient(_sleep):
    route = respx.post("http://dgx.example.com:11434/api/chat")
    route.side_effect = [httpx.Response(503), _resp("好")]
    assert OllamaLLMClient(BASE, "ollama", "m").chat("s", "u") == "好"
    assert route.call_count == 2


@respx.mock
@patch("shanhai.providers._http.time.sleep")
def test_chat_retries_on_transport_error(_sleep):
    # 之前裸 self._client.post() 无 try/except:RemoteProtocolError 直接不重试地传播,
    # 瞬时故障(DGX 经隧道链路更易触发)会直接杀死 Ollama 路径
    route = respx.post("http://dgx.example.com:11434/api/chat")
    route.side_effect = [
        httpx.RemoteProtocolError("Server disconnected without sending a response"), _resp("好")]
    assert OllamaLLMClient(BASE, "ollama", "m").chat("s", "u") == "好"
    assert route.call_count == 2


@respx.mock
def test_structured_retries_invalid_then_fails():
    respx.post("http://dgx.example.com:11434/api/chat").mock(return_value=_resp("不是 JSON"))
    with pytest.raises(LLMError):
        OllamaLLMClient(BASE, "ollama", "m").structured("s", "u", Pet, retries=1)


@respx.mock
def test_structured_recovers_via_extract_json():
    # 带 markdown 代码块 + 尾随说明文字,应经 _extract_json 容错抽取,而非直接 model_validate_json
    content = '```json\n{"name": "咪咪", "age": 3}\n```\n补充说明'
    respx.post("http://dgx.example.com:11434/api/chat").mock(return_value=_resp(content))
    pet = OllamaLLMClient(BASE, "ollama", "m").structured("s", "u", Pet)
    assert pet.age == 3


@respx.mock
def test_structured_recovers_via_extract_json_trailing_text():
    content = '{"name": "咪咪", "age": 3} 以上是结果'
    respx.post("http://dgx.example.com:11434/api/chat").mock(return_value=_resp(content))
    pet = OllamaLLMClient(BASE, "ollama", "m").structured("s", "u", Pet)
    assert pet.age == 3


@respx.mock
def test_structured_does_not_retry_400():
    route = respx.post("http://dgx.example.com:11434/api/chat").mock(
        return_value=httpx.Response(400, json={"error": "bad request"}))
    with pytest.raises(httpx.HTTPStatusError):         # 认证/请求错误不可重试,原样向上抛
        OllamaLLMClient(BASE, "ollama", "m").structured("s", "u", Pet)
    assert route.call_count == 1                       # 不当作"输出不合法"重发

import json
import re
import time

import httpx
from pydantic import BaseModel, ValidationError

_TRANSIENT = {429, 500, 502, 503, 504}  # 代理瞬时过载(如"资源不足"503),可重试;400 不可重试


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 300):
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,  # 本地大模型(如带思考的 Qwen3.5)单次结构化输出可远超 300s
        )

    def chat(self, system: str, user: str, temperature: float = 0.7, retries: int = 2) -> str:
        for attempt in range(retries + 1):
            r = self._client.post("/chat/completions", json={
                "model": self.model,
                "temperature": temperature,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            })
            if r.status_code in _TRANSIENT and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        raise LLMError("unreachable")  # pragma: no cover

    def structured[T: BaseModel](self, system: str, user: str,
                                 schema: type[T], retries: int = 2) -> T:
        sys_prompt = (system + "\n\n只输出一个 JSON 对象,不要输出任何其他文字。必须符合此 JSON Schema:\n"
                      + json.dumps(schema.model_json_schema(), ensure_ascii=False))
        prompt = user
        last_err: Exception | None = None
        for _ in range(retries + 1):
            try:
                text = self.chat(sys_prompt, prompt, temperature=0.3)
                return schema.model_validate_json(_extract_json(text))
            except (httpx.HTTPError, ValidationError, ValueError,
                    TypeError, KeyError, IndexError) as e:
                last_err = e
                prompt = f"{user}\n\n上一次输出不合法:{e}\n请修正后重新只输出 JSON。"
        raise LLMError(f"结构化输出失败: {last_err}")


def _extract_json(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("响应中没有 JSON 对象")
    return text[start:end + 1]

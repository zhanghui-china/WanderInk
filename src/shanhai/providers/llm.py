import json
import re

import httpx
from pydantic import BaseModel, ValidationError


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=300,
        )

    def chat(self, system: str, user: str, temperature: float = 0.7) -> str:
        r = self._client.post("/chat/completions", json={
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

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

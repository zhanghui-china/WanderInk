# src/shanhai/providers/llm_ollama.py
"""Ollama 原生适配器:与 LLMClient 同签名,走 /api/chat 而非 OpenAI /v1。

动机(decisions/0006):思考型模型(如 qwen3.5)经 /v1 无法关闭思考,
S0–S2 每步 5–7 分钟;原生 API `think:false` 实测快 10×。结构化输出用
Ollama 的 `format=<JSON Schema>` 约束解码,产出天然合法 JSON。
"""
import httpx
from pydantic import BaseModel, ValidationError

from shanhai.providers._http import request_with_retry
from shanhai.providers.llm import LLMError, _extract_json


class OllamaLLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 300):
        # 配置里常写 http://host:11434/v1(与 OpenAI 形态共用),原生 API 在根路径
        root = base_url.rstrip("/").removesuffix("/v1")
        self.model = model
        self._base_url = base_url
        self._client = httpx.Client(
            base_url=root,
            headers={"Authorization": f"Bearer {api_key}"},  # ollama 忽略,保持形态一致
            timeout=timeout,
        )

    def _chat(self, system: str, user: str, temperature: float,
              fmt: dict | None, retries: int) -> str:
        body: dict = {
            "model": self.model, "stream": False, "think": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "options": {"temperature": temperature},
        }
        if fmt is not None:
            body["format"] = fmt
        r = request_with_retry(lambda: self._client.post("/api/chat", json=body), retries,
                               base_url=self._base_url)
        r.raise_for_status()
        return r.json()["message"]["content"]

    def chat(self, system: str, user: str, temperature: float = 0.7, retries: int = 2) -> str:
        return self._chat(system, user, temperature, fmt=None, retries=retries)

    def structured[T: BaseModel](self, system: str, user: str,
                                 schema: type[T], retries: int = 2) -> T:
        fmt = schema.model_json_schema()
        prompt = user
        last_err: Exception | None = None
        for _ in range(retries + 1):
            try:
                text = self._chat(system, prompt, temperature=0.3, fmt=fmt, retries=retries)
                return schema.model_validate_json(_extract_json(text))
            except (ValidationError, ValueError,
                    TypeError, KeyError, IndexError) as e:
                last_err = e
                prompt = f"{user}\n\n上一次输出不合法:{e}\n请修正后重新只输出 JSON。"
        raise LLMError(f"结构化输出失败: {last_err}")

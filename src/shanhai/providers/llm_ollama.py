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
              fmt: dict | None, retries: int, max_tokens: int | None = None) -> str:
        body: dict = {
            "model": self.model, "stream": False, "think": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "options": {"temperature": temperature},
        }
        if fmt is not None:
            body["format"] = fmt
        # Ollama 的输出长度上限叫 num_predict(不是 OpenAI 的 max_tokens),放在 options 里。
        # 只给探活用,理由见 LLMClient.chat 的说明。
        if max_tokens is not None:
            body["options"]["num_predict"] = max_tokens
        r = request_with_retry(lambda: self._client.post("/api/chat", json=body), retries,
                               base_url=self._base_url)
        # 404 在这条路上几乎必然是配置问题,而不是"服务出错":要么这个地址根本不是 Ollama
        # (2026-08-08 线上把 hermes-agent 的地址配了 ollama 协议),要么 llm_base_url 被填成了
        # 完整接口路径(如 .../api/generate,会被拼成 .../api/generate/api/chat)。httpx 原文是
        # 一段带 MDN 链接的英文,且会原样透到界面上(见 web/src/pipeline.ts 的"不翻译"约定),
        # 用户从中看不出该去改哪个字段——所以在这里换成能直接指向配置项的中文。
        if r.status_code == 404:
            raise LLMError(
                f"{r.request.url} 返回 404:该地址没有 Ollama 的 /api/chat 路由。"
                "若它其实是 OpenAI 兼容服务(如 hermes-agent),请把 llm_provider 改回 openai;"
                "若它确实是 Ollama,请把 llm_base_url 填到服务根地址"
                "(如 http://host:11434 或 http://host:11434/v1),不要带 /api/generate 这类完整接口路径。"
            )
        r.raise_for_status()
        # 与 LLMClient.chat 一致:截断后 content 可能为空/缺失,统一归一成 ""
        return r.json().get("message", {}).get("content") or ""

    def chat(self, system: str, user: str, temperature: float = 0.7, retries: int = 2,
             max_tokens: int | None = None) -> str:
        return self._chat(system, user, temperature, fmt=None, retries=retries,
                          max_tokens=max_tokens)

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

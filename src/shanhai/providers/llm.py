import json
import re

import httpx
from pydantic import BaseModel, ValidationError

from shanhai.providers._http import request_with_retry


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 300):
        self.model = model
        self._base_url = base_url
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,  # 本地大模型(如带思考的 Qwen3.5)单次结构化输出可远超 300s
        )

    def chat(self, system: str, user: str, temperature: float = 0.7, retries: int = 2,
             max_tokens: int | None = None) -> str:
        """max_tokens 只给**探活**用(见 api._probe_llm),生成路径一律不传。

        由来:思考型模型(线上 vllm 上的 Qwen3.5-9B)为了回一个字会先思考 185~1524 个 token,
        耗时因此在 8.8~74.3 秒之间长尾抖动——探活超时抬到多少都会有一条尾巴,抬阈值治不了。
        限制输出长度是对症的:掐掉思考就掐掉了长尾的来源。
        ⚠️ 代价:思考型模型被截断后 content 可能为空(甚至是 null),所以这里统一归一成 ""——
        探活关心的是"这个端点能不能正常应答我们这种请求",不是它说了什么。"""
        body: dict = {
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        r = request_with_retry(lambda: self._client.post("/chat/completions", json=body),
                               retries, base_url=self._base_url)
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""

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
            except (ValidationError, ValueError,
                    TypeError, KeyError, IndexError) as e:
                last_err = e
                prompt = f"{user}\n\n上一次输出不合法:{e}\n请修正后重新只输出 JSON。"
        raise LLMError(f"结构化输出失败: {last_err}")


def _extract_json(text: str) -> str:
    """从模型输出里抠出第一个 JSON 对象。用 stdlib json.raw_decode 做括号配平——它原生理解
    字符串字面量与转义,故字段值里出现 { / } 不会误判(手搓深度计数器会,曾致合法对象被丢弃)。"""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("响应中没有 JSON 对象")
    try:
        _, end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError as e:
        raise ValueError(f"响应中没有合法 JSON 对象: {e}") from e
    return text[start:end]

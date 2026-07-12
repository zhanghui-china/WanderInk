# src/shanhai/providers/_http.py
"""四个 provider(llm/image/tts/llm_ollama)共享的唯一重试策略。

httpx.TransportError 是 TimeoutException/ConnectError/RemoteProtocolError("Server
disconnected without sending a response")等连接层瞬时故障的公共基类;此前各 provider
各自抄一份重试逻辑且已漂移,tts.py/llm_ollama.py 甚至漏抓 TransportError,单次瞬时
故障(DGX 经隧道链路更易触发)会直接杀死 TTS(→静音页)与 Ollama 路径。收敛到这一处。
"""
import time
from collections.abc import Callable

import httpx

TRANSIENT_STATUS = {429, 500, 502, 503, 504}  # 代理瞬时过载/超时,可重试;400 等不可重试


def request_with_retry(do_request: Callable[[], httpx.Response], retries: int,
                       transient_status: set[int] = TRANSIENT_STATUS) -> httpx.Response:
    """执行 do_request(),对 TransportError 与瞬时状态码重试,退避 2*(attempt+1) 秒;
    最后一次仍失败则原样抛出。调用方拿到返回的 Response 后照旧自行 raise_for_status() 与解析。
    """
    for attempt in range(retries + 1):
        try:
            r = do_request()
        except httpx.TransportError:
            if attempt == retries:
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in transient_status and attempt < retries:
            time.sleep(2 * (attempt + 1))
            continue
        return r
    raise RuntimeError("unreachable")  # pragma: no cover

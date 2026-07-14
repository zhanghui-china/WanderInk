# src/shanhai/providers/_http.py
"""四个 provider(llm/image/tts/llm_ollama)共享的唯一重试策略。

httpx.TransportError 是 TimeoutException/ConnectError/RemoteProtocolError("Server
disconnected without sending a response")等连接层瞬时故障的公共基类;此前各 provider
各自抄一份重试逻辑且已漂移,tts.py/llm_ollama.py 甚至漏抓 TransportError,单次瞬时
故障(DGX 经隧道链路更易触发)会直接杀死 TTS(→静音页)与 Ollama 路径。收敛到这一处。
"""
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from urllib.parse import urlparse

import httpx

TRANSIENT_STATUS = {429, 500, 502, 503, 504}  # 代理瞬时过载/超时,可重试;400 等不可重试

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_local_lock = threading.Lock()


def is_local_endpoint(base_url: str) -> bool:
    return urlparse(base_url).hostname in _LOCAL_HOSTS


@contextmanager
def local_backend_guard(base_url: str):
    """本地 Spark 后端全局单并发:GPU 物理共享(Ollama/ComfyUI/CosyVoice2/ACE-Step 同卡),
    跨环节跨用户排队,避免争抢显存导致的推理拖慢(见 2026-07-13 DGX 实测:并发命中同卡时
    LLM 调用从数十秒拖到接近 900s 超时)。云端 base_url 不受影响,直接放行。"""
    if is_local_endpoint(base_url):
        with _local_lock:
            yield
    else:
        yield


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

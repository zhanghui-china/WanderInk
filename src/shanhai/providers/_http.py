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
from contextlib import contextmanager, nullcontext
from urllib.parse import urlparse

import httpx

TRANSIENT_STATUS = {429, 500, 502, 503, 504}  # 代理瞬时过载/超时,可重试;400 等不可重试

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# ⚠️ 必须是**可重入**锁。跨线程语义与 Lock 完全一样(仍是互斥),区别只在同一线程可以重进。
# 需要它是因为 try_local_backend_guard 会套在 request_with_retry 外面,而后者内部还会再进
# 一次 local_backend_guard——用不可重入的 Lock 时,同一线程第二次 acquire 永远等下去,
# 表现为配置测试请求整个挂死(2026-08-08 端到端实测踩到,见 test_..._does_not_deadlock)。
_local_lock = threading.RLock()


def is_local_endpoint(base_url: str) -> bool:
    return urlparse(base_url).hostname in _LOCAL_HOSTS


@contextmanager
def local_backend_guard(base_url: str):
    """本地 Spark 后端全局单并发:GPU 物理共享(Ollama/ComfyUI/CosyVoice2/ACE-Step 同卡),
    跨环节跨用户排队,避免争抢显存导致的推理拖慢(见 2026-07-13 DGX 实测:并发命中同卡时
    LLM 调用从数十秒拖到接近 900s 超时)。云端 base_url 不受影响,直接放行。

    ⚠️ 这把锁**没有超时**:等不到就一直等。后台生成线程该如此(排队总比抢显存好),但
    **请求线程不能用它**——挂住的是 FastAPI 的 worker,且无日志无 503。请求线程走
    try_local_backend_guard。"""
    if is_local_endpoint(base_url):
        with _local_lock:
            yield
    else:
        yield


class LocalBackendBusy(Exception):
    """本地后端正忙(锁被生成作业占着),在给定时限内没抢到。"""


@contextmanager
def try_local_backend_guard(base_url: str, wait_s: float):
    """同 local_backend_guard,但**等不到就抛 LocalBackendBusy**,给请求线程用。

    存在的理由:配置「测试」按钮跑在请求线程里。若直接用 local_backend_guard,一个正在跑
    S4 的作业能把这颗按钮挂住整整一个 image_timeout(线上 900s)——用户看到的是浏览器
    一直转圈,而真相"后端正忙"其实是条有用的信息,该如实说出来。
    不绕过锁:测试也是真调用,绕过就是在生成期间抢显存,那正是这把锁要防的事。"""
    if not is_local_endpoint(base_url):
        yield
        return
    if not _local_lock.acquire(timeout=wait_s):
        raise LocalBackendBusy(
            f"本地后端正忙({base_url}):有生成作业正占用 GPU,等待 {wait_s:g} 秒未获得。"
            "这不代表配置有问题,等当前作业跑完再测一次。")
    try:
        yield
    finally:
        _local_lock.release()


def request_with_retry(do_request: Callable[[], httpx.Response], retries: int, *,
                       idempotent: bool = True,
                       transient_status: set[int] = TRANSIENT_STATUS,
                       base_url: str | None = None) -> httpx.Response:
    """执行 do_request(),对 TransportError 与瞬时状态码重试,退避 2*(attempt+1) 秒;
    最后一次仍失败则原样抛出。调用方拿到返回的 Response 后照旧自行 raise_for_status() 与解析。

    idempotent=False(生成类非幂等 POST:images/generations、images/edits、audio/music):
    连接层 TransportError(含已发出请求后的 ReadTimeout / 服务端中途断连,请求可能已被上游受理
    并计费)不重试;但明确的瞬时状态码(429/5xx——请求未被成功受理、重试安全)仍照常重试。
    默认 True 保持 LLM/TTS 等文本类请求的既有行为。

    base_url 非 None 时,每次实际网络调用都在 local_backend_guard(base_url) 内进行,而退避 sleep
    落在锁外——本地后端一次抖动不再让持锁线程 sleep 数秒、期间拖住同进程其它跨环节请求空等。
    """
    for attempt in range(retries + 1):
        try:
            with local_backend_guard(base_url) if base_url is not None else nullcontext():
                r = do_request()
        except httpx.TransportError as e:
            # 连接建立阶段的错误(ConnectError/ConnectTimeout/PoolTimeout:请求根本没发出去)重试
            # 100% 安全、零重复计费,即便非幂等也重试——正是本模块抵御 DGX 隧道抖动的本职;
            # 其它 TransportError(ReadTimeout/中途断连,请求可能已发出并被上游受理计费)仅幂等请求重试。
            connect_phase = isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
            if attempt == retries or not (idempotent or connect_phase):
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in transient_status and attempt < retries:
            time.sleep(2 * (attempt + 1))
            continue
        return r
    raise RuntimeError("unreachable")  # pragma: no cover

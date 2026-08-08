import threading
import time

import pytest

from shanhai.providers import _http
from shanhai.providers._http import is_local_endpoint, local_backend_guard


def test_is_local_endpoint_recognizes_loopback():
    assert is_local_endpoint("http://127.0.0.1:11434/v1")
    assert is_local_endpoint("http://localhost:8091")


def test_is_local_endpoint_rejects_cloud():
    assert not is_local_endpoint("https://api.tu-zi.com/v1")


def _run_two_workers(base_url: str) -> list[tuple[str, str, float]]:
    events: list[tuple[str, str, float]] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        with local_backend_guard(base_url):
            with lock:
                events.append((name, "enter", time.monotonic()))
            time.sleep(0.1)
            with lock:
                events.append((name, "exit", time.monotonic()))

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events.sort(key=lambda e: e[2])
    return events


def test_local_backend_guard_serializes_local_calls():
    # 本地端点全局单并发:排序后必须是 enter/exit 严格配对(同一线程连续两条),
    # 不能出现"两个 enter 相邻"这种重叠进入临界区的情况。
    events = _run_two_workers("http://127.0.0.1:1/v1")
    assert [e[1] for e in events] == ["enter", "exit", "enter", "exit"]
    assert events[0][0] == events[1][0]      # 第一段 enter/exit 属于同一线程
    assert events[2][0] == events[3][0]      # 第二段属于另一线程
    assert events[2][2] >= events[1][2]      # 后者的 enter 不早于前者的 exit(无重叠)


def test_local_backend_guard_does_not_serialize_cloud_calls():
    # 云端 URL 不加锁:两个线程的临界区应能同时重叠,不必等对方退出。
    events = _run_two_workers("https://api.tu-zi.com/v1")
    enters = [e[2] for e in events if e[1] == "enter"]
    exits = [e[2] for e in events if e[1] == "exit"]
    assert max(enters) < min(exits)          # 两次 enter 都发生在任一 exit 之前 → 重叠


def test_try_guard_gives_up_instead_of_hanging_the_request_thread():
    """请求线程用的带时限版本:抢不到锁要抛 LocalBackendBusy,而**不是**无限期等。

    这条防的是"点一下配置测试,浏览器转圈 15 分钟":那把锁没有超时,一个正在跑 S4 的作业
    能占着它整整一个 image_timeout(线上 900s),而挂住的是 FastAPI 的 worker 线程,
    既无日志也不返回 503。"""
    import threading
    import time as _t

    from shanhai.providers._http import LocalBackendBusy, try_local_backend_guard

    held = threading.Event()
    release = threading.Event()

    def hog():
        with _http.local_backend_guard("http://127.0.0.1:11434/v1"):
            held.set()
            release.wait(5)

    t = threading.Thread(target=hog, daemon=True)
    t.start()
    assert held.wait(2), "占锁线程没起来"
    try:
        t0 = _t.monotonic()
        with pytest.raises(LocalBackendBusy) as ei:
            with try_local_backend_guard("http://127.0.0.1:11434/v1", 0.3):
                pass
        waited = _t.monotonic() - t0
        assert 0.25 <= waited < 2.0, f"应在时限附近放弃,实际等了 {waited:.2f}s"
        assert "正忙" in str(ei.value)          # 是"后端忙"而不是"配置错",文案不能误导
    finally:
        release.set()
        t.join(5)


def test_try_guard_is_transparent_for_remote_endpoints():
    """云端端点不受这把锁管:即使锁被占着也该直接放行,不能凭空多等一截。"""
    from shanhai.providers._http import try_local_backend_guard
    with _http._local_lock:
        with try_local_backend_guard("https://api.stepfun.com/v1", 0.01):
            pass          # 不抛即通过


def test_nested_guards_do_not_deadlock():
    """带时限的外层 + request_with_retry 内部那层,会在**同一线程**里嵌套。

    这正是配置测试的调用形状:_probe_llm 用 try_local_backend_guard 包住整次 chat,
    而 chat 内部的 request_with_retry 又会进一次 local_backend_guard。锁若不可重入,
    第二次 acquire 永远等下去——2026-08-08 端到端实测时整个请求挂死 300 秒。"""
    from shanhai.providers._http import try_local_backend_guard
    url = "http://127.0.0.1:11434/v1"
    with try_local_backend_guard(url, 1.0):
        with local_backend_guard(url):       # 同线程重进,不能卡住
            pass


def test_lock_still_excludes_other_threads():
    """可重入只对同线程放行:别的线程照旧要排队,单并发保护不能被这次改动削弱。"""
    import threading as _th
    entered = _th.Event()
    other_got = _th.Event()

    def other():
        with local_backend_guard("http://127.0.0.1:11434/v1"):
            other_got.set()

    with local_backend_guard("http://127.0.0.1:11434/v1"):
        entered.set()
        t = _th.Thread(target=other, daemon=True)
        t.start()
        assert not other_got.wait(0.3), "别的线程不该在锁被持有时进入"
    t.join(2)
    assert other_got.is_set(), "锁释放后别的线程应能进入"

import threading
import time

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

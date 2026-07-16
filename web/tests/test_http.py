# tests/test_http.py
from unittest.mock import patch

import httpx, pytest

from shanhai.providers._http import request_with_retry


@patch("shanhai.providers._http.time.sleep")
def test_idempotent_false_does_not_retry_transport_error(_sleep):
    # 非幂等:请求可能已发出并被上游受理计费的错误(ReadTimeout/中途断连)一次即抛,不重试
    calls = []

    def do():
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    with pytest.raises(httpx.ReadTimeout):
        request_with_retry(do, retries=2, idempotent=False)
    assert len(calls) == 1


@patch("shanhai.providers._http.time.sleep")
def test_idempotent_false_still_retries_connect_phase_error(_sleep):
    # 非幂等:连接建立阶段错误(请求根本没发出去)重试 100% 安全、零重复计费,仍重试
    calls = []

    def do():
        calls.append(1)
        raise httpx.ConnectError("tunnel blip")

    with pytest.raises(httpx.ConnectError):
        request_with_retry(do, retries=2, idempotent=False)
    assert len(calls) == 3   # 1 + 2 retries


@patch("shanhai.providers._http.time.sleep")
def test_idempotent_false_still_retries_transient_status(_sleep):
    # 非幂等仍对明确的瞬时状态码(429/5xx,请求未被成功受理、重试安全)重试
    calls = []

    def do():
        calls.append(1)
        return httpx.Response(503) if len(calls) < 2 else httpx.Response(200)

    r = request_with_retry(do, retries=2, idempotent=False)
    assert r.status_code == 200
    assert len(calls) == 2


@patch("shanhai.providers._http.time.sleep")
def test_idempotent_true_retries_transport_error(_sleep):
    # 默认幂等(LLM/TTS 文本类):连接层瞬时故障照旧重试
    calls = []

    def do():
        calls.append(1)
        if len(calls) < 2:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200)

    r = request_with_retry(do, retries=2)
    assert r.status_code == 200
    assert len(calls) == 2


@patch("shanhai.providers._http.time.sleep")
def test_backoff_sleep_runs_outside_local_lock(_sleep):
    # 退避 sleep 必须在 _local_lock 之外:sleep 期间锁应可被其它线程获取,
    # 否则本地后端一次抖动会让持锁线程 sleep 数秒、拖住同进程其它跨环节请求。
    from shanhai.providers import _http

    def assert_lock_free(_s):
        assert _http._local_lock.acquire(blocking=False)  # sleep 时锁未被持有
        _http._local_lock.release()

    _sleep.side_effect = assert_lock_free
    calls = []

    def do():
        calls.append(1)
        return httpx.Response(503) if len(calls) < 2 else httpx.Response(200)

    r = request_with_retry(do, retries=2, base_url="http://127.0.0.1:11434/v1")
    assert r.status_code == 200
    assert _sleep.called

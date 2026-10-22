"""腾讯首图慢：连接保活、断开重发一次、预热（吞掉异常、不和真请求赛跑）。全部用本机假服务器。"""
import logging
import pathlib
import re
import threading
import time

import httpx
import pytest

from shotrans.translate.base import Job, TranslateError
from shotrans.translate.tencent import TencentClient
from tests.fakes import closed_port_url, transmart_map, transmart_reply


def _client(srv, **kw):
    return TencentClient(timeout_connect=0.5, timeout_read=kw.pop("timeout_read", 2.0), url=srv.url, **kw)


def _collect(client, jobs):
    got = []
    client.translate_stream(jobs, "en2zh", [], lambda i, t: got.append((i, t)))
    return got


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("等待超时")
        time.sleep(0.01)


# ---------- 连接保活、系统代理 ----------
def test_client_keepalive_120s(monkeypatch):
    seen = []
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: (seen.append(k), real(*a, **k))[1])
    TencentClient(url="http://127.0.0.1:1/api/imt")._http()
    assert seen[0]["limits"].keepalive_expiry == 120.0


def test_client_has_no_custom_transport(monkeypatch):
    """传 transport= 会让 httpx 不再读系统代理（allow_env_proxies = trust_env and transport is None）。"""
    seen = []
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: (seen.append(k), real(*a, **k))[1])
    TencentClient(url="http://127.0.0.1:1/api/imt")._http()
    assert "transport" not in seen[0]


def test_client_mounts_env_proxies(monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    c = TencentClient(url="https://transmart.qq.com/api/imt")._http()
    assert len(c._mounts) >= 2  # https:// 和 http:// 各挂一个代理 transport


def test_request_goes_through_env_proxy(monkeypatch, make_transmart):
    """真的发一次：目标是个解析不了的域名，只有经本机假代理才收得到。"""
    for k in ("ALL_PROXY", "NO_PROXY", "all_proxy", "no_proxy", "https_proxy", "http_proxy"):
        monkeypatch.delenv(k, raising=False)
    proxy = make_transmart(transmart_map(lambda t: "好"))
    monkeypatch.setenv("HTTP_PROXY", proxy.base)
    monkeypatch.setenv("HTTPS_PROXY", proxy.base)
    c = TencentClient(timeout_connect=0.5, timeout_read=2.0, url="http://transmart.invalid/api/imt")
    assert _collect(c, [Job(1, "a")]) == [(1, "好")]
    assert proxy.requests[0]["path"] == "http://transmart.invalid/api/imt"  # 代理收到的是绝对地址


# ---------- TLS 证书校验失败 ----------
def test_tls_verification_failure_is_connect_error(monkeypatch):
    c = TencentClient(url="https://transmart.qq.com/api/imt")

    def boom(*a, **k):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired")

    monkeypatch.setattr(c._http(), "post", boom)
    with pytest.raises(TranslateError) as ei:
        _collect(c, [Job(1, "a")])
    assert ei.value.kind == "connect"
    assert "CERTIFICATE_VERIFY_FAILED" in str(ei.value)


def test_nothing_disables_tls_verification():
    root = pathlib.Path(__file__).resolve().parent.parent / "shotrans"
    bad = [str(p) for p in root.rglob("*.py")
           if re.search(r"verify\s*=\s*(False|0)|CERT_NONE|_create_unverified_context", p.read_text(encoding="utf-8"))]
    assert bad == []


# ---------- 旧连接被服务端断开：重发一次 ----------
def test_server_disconnect_without_response_is_retried_once(make_transmart):
    n = {"posts": 0}

    def handler(h, payload):
        n["posts"] += 1
        if n["posts"] == 1:
            h.close_connection = True  # 一个字节都不回就断开：客户端见到 RemoteProtocolError
            return
        transmart_reply(h, ["好"])

    srv = make_transmart(handler)
    assert _collect(_client(srv), [Job(1, "a")]) == [(1, "好")]
    assert n["posts"] == 2


def test_server_disconnect_twice_is_connect_error_no_loop(make_transmart):
    def handler(h, payload):
        h.close_connection = True

    srv = make_transmart(handler)
    with pytest.raises(TranslateError) as ei:
        _collect(_client(srv), [Job(1, "a")])
    assert ei.value.kind == "connect"
    assert len(srv.requests) == 2


# ---------- 预热 ----------
def test_warm_sends_head_to_site_root(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    _client(srv).warm()
    assert [h["path"] for h in srv.heads] == ["/"]
    assert srv.requests == []  # 不是翻译请求


def test_warm_swallows_connect_error(caplog):
    c = TencentClient(timeout_connect=0.5, timeout_read=0.5, url=closed_port_url().replace("/v1", "/api/imt"))
    with caplog.at_level(logging.DEBUG, logger="shotrans.translate.tencent"):
        assert c.warm() is None
    assert any("预热失败" in r.getMessage() and r.levelno == logging.DEBUG for r in caplog.records)


def test_warm_swallows_any_exception(monkeypatch, make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    c = _client(srv)

    def boom():
        raise RuntimeError("意外")

    monkeypatch.setattr(c, "_http", boom)
    c.warm()  # 不抛
    assert c._warming is False  # 标志复位，后面的真请求不会一直等


def test_warm_async_returns_immediately_on_daemon_thread(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    srv.head_delay = 1.0
    c = _client(srv)
    t0 = time.monotonic()
    th = c.warm_async()
    assert time.monotonic() - t0 < 0.2
    assert th.daemon and th is not threading.main_thread()
    th.join(3)
    assert len(srv.heads) == 1


def test_real_request_waits_for_warm_in_progress(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    srv.head_delay = 0.5
    c = _client(srv)
    c.warm_async()
    _wait(lambda: srv.heads)  # 预热已经在服务器那边了
    assert _collect(c, [Job(1, "a")]) == [(1, "好")]
    assert srv.requests[0]["at"] >= srv.heads[0]["end"]  # 真请求等预热结束才发


def test_warm_skipped_while_real_request_in_flight(make_transmart):
    def slow(h, payload):
        time.sleep(0.5)
        transmart_reply(h, ["好"])

    srv = make_transmart(slow)
    c = _client(srv)
    th = threading.Thread(target=_collect, args=(c, [Job(1, "a")]), daemon=True)
    th.start()
    _wait(lambda: srv.requests)
    t0 = time.monotonic()
    c.warm()
    assert time.monotonic() - t0 < 0.2
    th.join(3)
    assert srv.heads == []


def test_second_warm_skipped_while_first_in_progress(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    srv.head_delay = 0.5
    c = _client(srv)
    th = c.warm_async()
    _wait(lambda: srv.heads)
    t0 = time.monotonic()
    c.warm()
    assert time.monotonic() - t0 < 0.2
    th.join(3)
    assert len(srv.heads) == 1


# ---------- 过期的复用连接：读/写出错也重试一次，超时不重试 ----------
def _flaky_post(c, exc, fail_times):
    """让真实的 post 先抛 fail_times 次 exc，再放行；返回调用计数。"""
    http = c._http()
    real, n = http.post, {"n": 0}

    def post(*a, **k):
        n["n"] += 1
        if n["n"] <= fail_times:
            raise exc
        return real(*a, **k)

    http.post = post
    return n


def test_read_error_on_reused_connection_retried_once(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    c = _client(srv)
    n = _flaky_post(c, httpx.ReadError("reset"), 1)
    assert _collect(c, [Job(1, "a")]) == [(1, "好")]
    assert n["n"] == 2


def test_write_error_on_reused_connection_retried_once(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    c = _client(srv)
    n = _flaky_post(c, httpx.WriteError("broken pipe"), 1)
    assert _collect(c, [Job(1, "a")]) == [(1, "好")]
    assert n["n"] == 2


def test_read_error_twice_gives_connect_error(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    c = _client(srv)
    n = _flaky_post(c, httpx.ReadError("reset"), 5)
    with pytest.raises(TranslateError) as ei:
        _collect(c, [Job(1, "a")])
    assert ei.value.kind == "connect"
    assert n["n"] == 2


def test_read_timeout_not_retried(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    c = _client(srv)
    n = _flaky_post(c, httpx.ReadTimeout("slow"), 5)
    with pytest.raises(TranslateError) as ei:
        _collect(c, [Job(1, "a")])
    assert ei.value.kind == "connect"
    assert n["n"] == 1


def test_warm_retries_stale_connection_once(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    c = _client(srv)
    http = c._http()
    real, n = http.head, {"n": 0}

    def head(*a, **k):
        n["n"] += 1
        if n["n"] == 1:
            raise httpx.RemoteProtocolError("closed")
        return real(*a, **k)

    http.head = head
    c.warm()
    assert n["n"] == 2 and len(srv.heads) == 1


def test_warm_stale_twice_still_swallowed():
    c = TencentClient(url="http://127.0.0.1:1/api/imt")
    http = c._http()
    n = {"n": 0}

    def head(*a, **k):
        n["n"] += 1
        raise httpx.ReadError("reset")

    http.head = head
    assert c.warm() is None
    assert n["n"] == 2
    assert c._warming is False

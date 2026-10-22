import json as _json
import threading as _threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shotrans.translate.base import Job, TranslateError
from shotrans.translate.vllm import VllmClient
from tests.fakes import JOBS2, FakeVllm, _client, _collect, closed_port_url, send_delta, send_done, sse_chunks, start_sse

# ====================== 补充：守卫专属测试 ======================


def test_slow_but_steady_after_first_token_is_ok(make_server):
    # 首字 0.3 秒、断流 1.0 秒；服务端每 0.5 秒来一个字：超过首字阈值，但没超过断流阈值
    s = make_server(sse_chunks(["⟦1⟧ 一", "⟦2⟧ 二"], delay=0.5))
    c = VllmClient(s.url, "k", connect_timeout=0.5, first_token_timeout=0.3, stall_timeout=1.0)
    assert _collect(c, JOBS2) == [(1, "一"), (2, "二")]


def test_role_only_delta_is_not_first_token(make_server):
    def handler(h, payload):
        start_sse(h)
        h.wfile.write(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')
        h.wfile.flush()
        time.sleep(1.5)

    s = make_server(handler)
    with pytest.raises(TranslateError) as ei:
        _collect(_client(s.url), JOBS2)
    assert ei.value.kind == "first_token"


def test_null_and_empty_content_not_first_token(make_server):
    def handler(h, payload):
        start_sse(h)
        h.wfile.write(b'data: {"choices":[{"delta":{"content":null}}]}\n\n')
        h.wfile.write(b'data: {"choices":[{"delta":{"content":""}}]}\n\n')
        h.wfile.flush()
        time.sleep(1.5)

    s = make_server(handler)
    with pytest.raises(TranslateError) as ei:
        _collect(_client(s.url), JOBS2)
    assert ei.value.kind == "first_token"


def _raw_lines(lines):
    def handler(h, payload):
        start_sse(h)
        for ln in lines:
            h.wfile.write(ln.encode() + b"\n\n")
        h.wfile.flush()
    return handler


GOOD1 = 'data: {"choices":[{"delta":{"content":"⟦1⟧ 一 ⟦2⟧ 二"}}]}'


def test_garbage_json_line_skipped(make_server):
    s = make_server(_raw_lines(["data: {not json", GOOD1]))
    assert _collect(_client(s.url), JOBS2) == [(1, "一"), (2, "二")]


def test_line_without_choices_skipped(make_server):
    s = make_server(_raw_lines(['data: {"usage":{"total":3}}', GOOD1]))
    assert _collect(_client(s.url), JOBS2) == [(1, "一"), (2, "二")]


def test_empty_choices_skipped(make_server):
    s = make_server(_raw_lines(['data: {"choices":[]}', GOOD1]))
    assert _collect(_client(s.url), JOBS2) == [(1, "一"), (2, "二")]


def test_non_data_line_ignored(make_server):
    # 「event」正好 5 个字符：若不过滤，line[5:] 会变成一段合法 JSON
    bad = 'event{"choices":[{"delta":{"content":"⟦1⟧ 坏"}}]}'
    s = make_server(_raw_lines([bad, GOOD1]))
    assert _collect(_client(s.url), JOBS2) == [(1, "一"), (2, "二")]


def test_nothing_read_after_done(make_server):
    def handler(h, payload):
        start_sse(h)
        send_delta(h, "⟦1⟧ 一 ⟦2⟧ 二")
        h.wfile.write(b"data: [DONE]\n\n")
        send_delta(h, "多余")
        h.wfile.flush()

    s = make_server(handler)
    assert _collect(_client(s.url), JOBS2) == [(1, "一"), (2, "二")]


def test_duplicate_index_delivered_once(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 一 ⟦1⟧ 重复 ⟦2⟧ 二"]))
    assert _collect(_client(s.url), JOBS2) == [(1, "一"), (2, "二")]


def test_empty_segment_not_delivered(make_server):
    s = make_server(sse_chunks(["⟦1⟧ ⟦2⟧ 二"]))
    got = []
    with pytest.raises(TranslateError) as ei:
        _client(s.url).translate_stream(JOBS2, "en2zh", [], lambda i, t: got.append((i, t)))
    assert ei.value.kind == "format"
    assert got == [(2, "二")]
    assert ei.value.done == {2}


def test_connection_dropped_without_response_is_http_error(make_server):
    def handler(h, payload):
        h.close_connection = True  # 什么都不写就断开

    s = make_server(handler)
    with pytest.raises(TranslateError) as ei:
        _collect(_client(s.url), JOBS2)
    assert ei.value.kind == "http"


def test_connect_error_during_stream_is_connect_kind():
    # Windows 上拒绝连接要约 1~2 秒才返回，首字超时必须放大，否则先判成 first_token
    c = VllmClient(closed_port_url(), "k", connect_timeout=5.0, first_token_timeout=8.0, stall_timeout=8.0)
    c._model = "m"  # 跳过 /models，让连接失败发生在流式请求里
    with pytest.raises(TranslateError) as ei:
        _collect(c, JOBS2)
    assert ei.value.kind == "connect"


def test_http_error_detail_has_status(make_server):
    def handler(h, payload):
        h.send_error(503)

    s = make_server(handler)
    with pytest.raises(TranslateError) as ei:
        _collect(_client(s.url), JOBS2)
    assert ei.value.detail == "HTTP 503"


def test_connection_closed_after_cancel(make_server):
    closed = _threading.Event()

    def handler(h, payload):
        start_sse(h)
        send_delta(h, "⟦1⟧ 一 ⟦2⟧ 二")
        try:
            for _ in range(100):
                send_delta(h, "x")
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            closed.set()
            raise

    s = make_server(handler)
    cancel = _threading.Event()
    jobs = [Job(1, "a"), Job(2, "b"), Job(3, "c")]
    _client(s.url).translate_stream(jobs, "en2zh", [], lambda i, t: cancel.set(), cancel)
    assert closed.wait(3.0), "取消后客户端应主动断开连接"


# ---------- model_name ----------

def test_model_name_cached_and_first_model(make_server):
    s = FakeVllm(sse_chunks([]), models=("m-a", "m-b"))
    c = _client(s.url)
    assert c.model_name() == "m-a"
    s.close()
    assert c.model_name() == "m-a"  # 第二次不再访问服务器


def test_model_name_empty_list_is_http_error():
    s = FakeVllm(sse_chunks([]), models=())
    try:
        with pytest.raises(TranslateError) as ei:
            _client(s.url).model_name()
        assert ei.value.kind == "http"
        assert "没有模型" in ei.value.detail
    finally:
        s.close()


def _models_server(code):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_error(code)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    _threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_model_name_non_200_is_http_error():
    # 原先用 401；401/403 现在单独报 auth（见下），这里换成 500 继续守「非 200 → http」
    srv = _models_server(500)
    try:
        with pytest.raises(TranslateError) as ei:
            _client(f"http://127.0.0.1:{srv.server_port}/v1").model_name()
        assert ei.value.kind == "http"
        assert "500" in ei.value.detail
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("code", [401, 403])
def test_model_name_401_403_is_auth_error(code):
    srv = _models_server(code)
    try:
        with pytest.raises(TranslateError) as ei:
            _client(f"http://127.0.0.1:{srv.server_port}/v1").model_name()
        assert ei.value.kind == "auth"
        assert str(code) in ei.value.detail
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("code", [401, 403])
def test_chat_401_403_is_auth_error(make_server, code):
    s = make_server(lambda h, payload: h.send_error(code))
    with pytest.raises(TranslateError) as ei:
        _collect(_client(s.url), JOBS2)
    assert ei.value.kind == "auth"
    assert ei.value.detail == f"HTTP {code}"


def test_chat_other_4xx_still_http_error(make_server):
    s = make_server(lambda h, payload: h.send_error(400))
    with pytest.raises(TranslateError) as ei:
        _collect(_client(s.url), JOBS2)
    assert ei.value.kind == "http"


def test_model_name_connect_error():
    with pytest.raises(TranslateError) as ei:
        _client(closed_port_url()).model_name()
    assert ei.value.kind == "connect"


def test_models_request_sends_bearer_key():
    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen["auth"] = self.headers.get("Authorization")
            body = _json.dumps({"data": [{"id": "x"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    _threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        _client(f"http://127.0.0.1:{srv.server_port}/v1").model_name()
        assert seen["auth"] == "Bearer k"
        seen.clear()
        VllmClient(f"http://127.0.0.1:{srv.server_port}/v1", "").model_name()
        assert seen["auth"] is None
    finally:
        srv.shutdown()
        srv.server_close()


def test_first_token_timeout_is_not_extended_by_stall_timeout(make_server):
    # 首字 0.4、断流 1.5：首字超时必须在约 0.4 秒时触发，而不是等到 1.9 秒
    def handler(h, payload):
        start_sse(h)
        time.sleep(3.0)

    s = make_server(handler)
    c = VllmClient(s.url, "k", connect_timeout=0.5, first_token_timeout=0.4, stall_timeout=1.5)
    t0 = time.monotonic()
    with pytest.raises(TranslateError) as ei:
        _collect(c, JOBS2)
    assert ei.value.kind == "first_token"
    assert time.monotonic() - t0 < 1.0


def test_http_error_after_partial_output_keeps_done(make_server):
    import socket
    import struct

    def handler(h, payload):
        start_sse(h)
        send_delta(h, "⟦1⟧ 一 ⟦2⟧ 二")
        time.sleep(0.3)
        # RST 强制断开：让客户端读到的是传输错误而不是正常结束
        h.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        h.connection.close()

    s = make_server(handler)
    got = []
    with pytest.raises(TranslateError) as ei:
        c = VllmClient(s.url, "k", connect_timeout=0.5, first_token_timeout=2.0, stall_timeout=2.0)
        c.translate_stream(JOBS2, "en2zh", [], lambda i, t: got.append((i, t)))
    assert ei.value.kind == "http"
    assert got == [(1, "一")]
    assert ei.value.done == {1}


# ====================== 失败路径加固 ======================

def _worker_threads():
    return [t for t in _threading.enumerate() if t.name == "shotrans-vllm-worker" and t.is_alive()]


def test_silent_server_first_token_then_worker_exits(make_server):
    # 服务器收了请求却一个字节都不回：主线程按时报 first_token，worker 线程也要在有限时间内退出
    release = _threading.Event()

    def handler(h, payload):
        release.wait(10.0)

    s = make_server(handler)
    try:
        t0 = time.monotonic()
        with pytest.raises(TranslateError) as ei:
            _collect(_client(s.url), JOBS2)
        assert ei.value.kind == "first_token"
        assert time.monotonic() - t0 < 1.0
        deadline = time.monotonic() + 3.0
        while _worker_threads() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _worker_threads() == [], "worker 线程泄漏"
    finally:
        release.set()


def test_cancel_during_wait_returns_promptly(make_server):
    def handler(h, payload):
        start_sse(h)
        time.sleep(3.0)

    s = make_server(handler)
    c = VllmClient(s.url, "k", connect_timeout=0.5, first_token_timeout=5.0, stall_timeout=5.0)
    cancel = _threading.Event()
    timer = _threading.Timer(0.3, cancel.set)
    timer.start()
    t0 = time.monotonic()
    c.translate_stream(JOBS2, "en2zh", [], lambda i, t: None, cancel)  # 不抛异常
    elapsed = time.monotonic() - t0
    timer.join()
    assert elapsed < 0.8


def test_chunk_arriving_after_cancel_not_delivered(make_server):
    # 取消恰好发生在主线程等待块的时候：到达的块不能再交给 on_segment
    cancel = _threading.Event()

    def handler(h, payload):
        start_sse(h)
        time.sleep(0.4)
        cancel.set()
        send_delta(h, "⟦1⟧ 一 ⟦2⟧ 二")
        send_done(h)

    s = make_server(handler)
    c = VllmClient(s.url, "k", connect_timeout=0.5, first_token_timeout=5.0, stall_timeout=5.0)
    got = []
    c.translate_stream(JOBS2, "en2zh", [], lambda i, t: got.append((i, t)), cancel)
    time.sleep(0.3)
    assert got == []


def test_pre_cancelled_opens_no_request(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 一 ⟦2⟧ 二"]))
    cancel = _threading.Event()
    cancel.set()
    _client(s.url).translate_stream(JOBS2, "en2zh", [], lambda i, t: None, cancel)
    time.sleep(0.2)
    assert s.requests == []


# ---------- model_name 只抛 TranslateError ----------

class _ModelsServer:
    def __init__(self, body: bytes, status=200):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        _threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.mark.parametrize("body", [
    b"<html>not json</html>",   # 非 JSON
    b"[1, 2]",                  # 顶层不是对象
    b'{"data": "oops"}',        # data 不是列表
    b'{"data": [123]}',         # 元素不是对象
    b'{"data": [{}]}',          # 缺 id
    b'{"data": [{"id": null}]}',  # id 不是字符串
    b'{"data": {"id": "x"}}',   # data 是对象
])
def test_model_name_odd_body_is_http_error(body):
    srv = _ModelsServer(body)
    try:
        with pytest.raises(TranslateError) as ei:
            _client(srv.url).model_name()
        assert ei.value.kind == "http"
    finally:
        srv.close()


# ---------- 畸形事件一律跳过 ----------

@pytest.mark.parametrize("bad", [
    'data: {"choices":[{"delta":null}]}',
    'data: {"choices":[null]}',
    'data: 42',
    'data: "str"',
    'data: null',
    'data: {"choices":null}',
    'data: {"choices":{"a":1}}',
    'data: {"choices":[{"delta":{"content":123}}]}',
    'data: {"choices":[{"delta":{"content":["x"]}}]}',
    'data: {"choices":[{"delta":"text"}]}',
])
def test_malformed_event_skipped(make_server, bad):
    s = make_server(_raw_lines([bad, GOOD1]))
    assert _collect(_client(s.url), JOBS2) == [(1, "一"), (2, "二")]

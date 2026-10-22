"""腾讯交互翻译客户端：全部用本机假服务器，不连外网。"""
import json
import re
import threading
import time

import pytest

from shotrans.translate import tencent
from shotrans.translate.base import Job, TranslateError
from shotrans.translate.tencent import TencentClient
from tests.fakes import _json, closed_port_url, transmart_map, transmart_reply


def _client(srv, **kw):
    return TencentClient(timeout_connect=0.5, timeout_read=kw.pop("timeout_read", 1.0), url=srv.url, **kw)


def _collect(client, jobs, direction="en2zh", cancel=None):
    got = []
    client.translate_stream(jobs, direction, [], lambda i, t: got.append((i, t)), cancel)
    return got


# ---------- 成功路径 ----------
def test_batch_in_one_request_and_order(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "译" + t))
    got = _collect(_client(srv), [Job(3, "alpha"), Job(7, "beta"), Job(9, "gamma")])
    assert got == [(3, "译alpha"), (7, "译beta"), (9, "译gamma")]
    assert len(srv.requests) == 1
    assert srv.requests[0]["json"]["source"]["text_list"] == ["alpha", "beta", "gamma"]


def test_request_shape(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    _collect(_client(srv), [Job(1, "Hello")])
    req = srv.requests[0]
    assert req["path"] == "/api/imt"
    assert req["content_type"].startswith("application/json")
    body = req["json"]
    assert body["header"]["fn"] == "auto_translation"
    assert body["type"] == "plain" and body["model_category"] == "normal"
    assert re.fullmatch(r"browser-chrome-130\.0\.0-Windows_10-[0-9a-f-]{36}", body["header"]["client_key"])


def test_client_key_same_for_whole_process(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    _collect(_client(srv), [Job(1, "a")])
    _collect(_client(srv), [Job(1, "b")])  # 另一个客户端实例
    keys = {r["json"]["header"]["client_key"] for r in srv.requests}
    assert len(keys) == 1


def test_en2zh_language_codes(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    _collect(_client(srv), [Job(1, "Hello")], "en2zh")
    body = srv.requests[0]["json"]
    assert (body["source"]["lang"], body["target"]["lang"]) == ("en", "zh")


def test_zh2en_language_codes(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "Hello"))
    _collect(_client(srv), [Job(1, "你好")], "zh2en")
    body = srv.requests[0]["json"]
    assert (body["source"]["lang"], body["target"]["lang"]) == ("zh", "en")


def test_placeholders_sent_as_braces(make_transmart):
    srv = make_transmart(transmart_map(lambda t: t))
    _collect(_client(srv), [Job(1, "see ⟨1⟩ and ⟨2⟩")])
    assert srv.requests[0]["json"]["source"]["text_list"] == ["see {1} and {2}"]


def test_braces_restored_to_placeholders(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["见 {1} 和 { 2 }"]))
    got = _collect(_client(srv), [Job(1, "see ⟨1⟩ and ⟨2⟩")])
    assert got == [(1, "见 ⟨1⟩ 和 ⟨2⟩")]


def test_literal_braces_in_source_not_turned_into_placeholders(make_transmart):
    # 改动说明：原文有 {0} 的段现在改用 __Pn__ 发（见 test_tencent_alt_placeholder.py），
    # 所以假服务器回的是 __P1__ 而不是 {1}；要守的仍是「原文的 {0} 不被换成占位符」。
    srv = make_transmart(lambda h, p: transmart_reply(h, ["格式 {0} 与 ⟨x⟩ __P1__"]))
    got = _collect(_client(srv), [Job(1, "format {0} and ⟨1⟩")])
    assert got == [(1, "格式 {0} 与 ⟨x⟩ ⟨1⟩")]  # {0} 原文就有，不是我们换的，保留


def test_html_entities_unescaped(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["p &lt; 0.05 &amp; x &gt; 1"]))
    assert _collect(_client(srv), [Job(1, "p < 0.05 & x > 1")]) == [(1, "p < 0.05 & x > 1")]


def test_translation_whitespace_stripped(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["  你好 \n"]))
    assert _collect(_client(srv), [Job(1, "Hello")]) == [(1, "你好")]


def test_mappings_ignored_not_sent(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "抑郁"))
    got = []
    _client(srv).translate_stream([Job(1, "depression")], "en2zh", [("depression", "抑郁症")],
                                  lambda i, t: got.append((i, t)))
    assert got == [(1, "抑郁")]
    assert "抑郁症" not in json.dumps(srv.requests[0]["json"], ensure_ascii=False)


def test_empty_jobs_sends_nothing(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    assert _collect(_client(srv), []) == []
    assert srv.requests == []


def test_connection_reused_across_calls(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    c = _client(srv)
    _collect(c, [Job(1, "a")])
    first = c._http()
    _collect(c, [Job(1, "b")])
    assert c._http() is first


def test_default_url_is_module_constant(monkeypatch):
    monkeypatch.setattr(tencent, "TRANSMART_URL", "http://127.0.0.1:1/api/imt")
    assert TencentClient().url == "http://127.0.0.1:1/api/imt"


def test_real_url_constant():
    from pathlib import Path

    # conftest 把模块属性换成了本机地址；这里看源码里写的真地址
    src = Path(tencent.__file__).read_text(encoding="utf-8")
    assert 'TRANSMART_URL = "https://transmart.qq.com/api/imt"' in src


def test_default_timeouts():
    c = TencentClient()
    assert (c.timeout_connect, c.timeout_read) == (2.0, 8.0)
    assert c.name == "tencent"


# ---------- 取消 ----------
def test_cancel_before_start_sends_nothing(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "好"))
    cancel = threading.Event()
    cancel.set()
    assert _collect(_client(srv), [Job(1, "a")], cancel=cancel) == []
    assert srv.requests == []


def test_cancel_between_segments_stops_delivery(make_transmart):
    srv = make_transmart(transmart_map(lambda t: "译" + t))
    cancel = threading.Event()
    got = []

    def on(i, t):
        got.append((i, t))
        cancel.set()  # 交出第一段后用户关了浮窗

    _client(srv).translate_stream([Job(1, "a"), Job(2, "b")], "en2zh", [], on, cancel)
    assert got == [(1, "译a")]


# ---------- 失败一律 TranslateError ----------
def _kind(client, jobs=(Job(1, "a"),)):
    with pytest.raises(TranslateError) as ei:
        _collect(client, list(jobs))
    return ei.value


def test_connect_refused_is_connect():
    c = TencentClient(timeout_connect=0.5, timeout_read=0.5, url=closed_port_url())
    assert _kind(c).kind == "connect"


def test_read_timeout_is_connect(make_transmart):
    def slow(h, p):
        time.sleep(1.0)
        transmart_reply(h, ["好"])

    srv = make_transmart(slow)
    t0 = time.perf_counter()
    assert _kind(_client(srv, timeout_read=0.3)).kind == "connect"
    assert time.perf_counter() - t0 < 0.9  # 按读超时退出，没等服务器


def test_non_200_is_http(make_transmart):
    srv = make_transmart(lambda h, p: _json(h, 503, {"header": {"ret_code": "succ"}, "auto_translation": ["好"]}))
    e = _kind(_client(srv))
    assert e.kind == "http" and "503" in e.detail


def test_not_json_is_format(make_transmart):
    def handler(h, p):
        body = b"<html>blocked</html>"
        h.send_response(200)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    assert _kind(_client(make_transmart(handler))).kind == "format"


def test_json_not_object_is_format(make_transmart):
    assert _kind(_client(make_transmart(lambda h, p: _json(h, 200, ["好"])))).kind == "format"


def test_ret_code_not_succ_is_format(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["好"], ret_code="error"))
    e = _kind(_client(srv))
    assert e.kind == "format" and "error" in e.detail


def test_header_missing_is_format(make_transmart):
    srv = make_transmart(lambda h, p: _json(h, 200, {"auto_translation": ["好"]}))
    assert _kind(_client(srv)).kind == "format"


def test_auto_translation_missing_is_format(make_transmart):
    srv = make_transmart(lambda h, p: _json(h, 200, {"header": {"ret_code": "succ"}}))
    assert _kind(_client(srv)).kind == "format"


def test_auto_translation_not_strings_is_format(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["好", 3]))
    got = []
    with pytest.raises(TranslateError) as ei:
        _client(srv).translate_stream([Job(1, "a"), Job(2, "b")], "en2zh", [], lambda i, t: got.append(i))
    assert ei.value.kind == "format" and got == []  # 整批不可信，一段也不交


def test_count_mismatch_is_format(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["好"]))
    got = []
    with pytest.raises(TranslateError) as ei:
        _client(srv).translate_stream([Job(1, "a"), Job(2, "b")], "en2zh", [], lambda i, t: got.append(i))
    assert ei.value.kind == "format" and got == []


def test_empty_translation_is_format_with_done(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["好", "  "]))
    got = []
    with pytest.raises(TranslateError) as ei:
        _client(srv).translate_stream([Job(1, "a"), Job(2, "b")], "en2zh", [], lambda i, t: got.append(i))
    assert ei.value.kind == "format" and got == [1] and ei.value.done == frozenset({1})

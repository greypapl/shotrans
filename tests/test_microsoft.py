"""微软翻译客户端：全部用本机假服务器，不连外网。"""
import threading

import pytest

from shotrans.translate import microsoft
from shotrans.translate.base import Job, TranslateError
from shotrans.translate.microsoft import MicrosoftClient, apply_mappings, batches, restore_mappings
from tests.fakes import _json, closed_port_url, edge_map, edge_reply


def _client(srv, **kw):
    return MicrosoftClient(timeout_connect=0.5, timeout_read=kw.pop("timeout_read", 1.0), url=srv.url, **kw)


def _collect(client, jobs, direction="en2zh", mappings=(), cancel=None):
    got = []
    client.translate_stream(jobs, direction, list(mappings), lambda i, t: got.append((i, t)), cancel)
    return got


# ---------- 成功路径 ----------
def test_batch_in_one_request_and_order(make_edge):
    srv = make_edge(edge_map(lambda t: "译" + t))
    got = _collect(_client(srv), [Job(3, "alpha"), Job(7, "beta"), Job(9, "gamma")])
    assert got == [(3, "译alpha"), (7, "译beta"), (9, "译gamma")]
    assert len(srv.requests) == 1
    assert srv.requests[0]["json"] == ["alpha", "beta", "gamma"]  # 请求体是字符串数组，不是 [{"Text": …}]


def test_request_shape(make_edge):
    srv = make_edge(edge_map(lambda t: "好"))
    _collect(_client(srv), [Job(1, "Hello")])
    req = srv.requests[0]
    assert req["path"] == "/translate/translatetext"
    assert req["content_type"].startswith("application/json")
    assert req["query"]["isEnterpriseClient"] == "false"
    assert "Edg/" in req["user_agent"]  # 冒充 Edge 浏览器


@pytest.mark.parametrize("direction,src,dst", [("en2zh", "en", "zh-Hans"), ("zh2en", "zh-Hans", "en")])
def test_language_codes(make_edge, direction, src, dst):
    srv = make_edge(edge_map(lambda t: "x"))
    _collect(_client(srv), [Job(1, "a")], direction)
    q = srv.requests[0]["query"]
    assert (q["from"], q["to"]) == (src, dst)


def test_placeholders_sent_as_braces_and_restored(make_edge):
    # 实测微软会把 ⟨n⟩ 当引号、把词挪进去；{n} 原样保留
    srv = make_edge(lambda h, p: edge_reply(h, ["见 {1} 和 { 2 }"]))
    got = _collect(_client(srv), [Job(1, "see ⟨1⟩ and ⟨2⟩")])
    assert srv.requests[0]["json"] == ["see {1} and {2}"]
    assert got == [(1, "见 ⟨1⟩ 和 ⟨2⟩")]


def test_literal_braces_in_source_use_alt_form(make_edge):
    srv = make_edge(lambda h, p: edge_reply(h, ["格式 {0} 见 __ P1 __"]))
    got = _collect(_client(srv), [Job(1, "format {0} see ⟨1⟩")])
    assert srv.requests[0]["json"] == ["format {0} see __P1__"]
    assert got == [(1, "格式 {0} 见 ⟨1⟩")]  # 原文的 {0} 不当成占位符


def test_alt_form_only_for_that_segment(make_edge):
    srv = make_edge(edge_map(lambda t: t))
    _collect(_client(srv), [Job(1, "a {0} ⟨1⟩"), Job(2, "b ⟨1⟩")])
    assert srv.requests[0]["json"] == ["a {0} __P1__", "b {1}"]


def test_only_our_numbers_restored(make_edge):
    srv = make_edge(lambda h, p: edge_reply(h, ["{1} {7}"]))
    assert _collect(_client(srv), [Job(1, "⟨1⟩")]) == [(1, "⟨1⟩ {7}")]


@pytest.mark.parametrize("src", ["1", "P1"])
def test_mapping_never_eats_placeholders(make_edge, src):
    """词条 "1"、"P1"（ERP 成分名）不能把占位符 ⟨1⟩ / __P1__ 里的数字吃掉，也不能吃掉已换好的记号 ⟦1⟧。"""
    srv = make_edge(edge_map(lambda t: t))
    text = f"format {{0}} see ⟨1⟩, {src} and ⟨2⟩"  # 原文有 {0}：占位符走 __Pn__
    got = _collect(_client(srv), [Job(1, text)], mappings=[(src, "成分"), ("see", "见")])
    # "see" 更长先换成 ⟦1⟧；短词条随后只在占位符、记号之外换
    assert srv.requests[0]["json"] == ["format {0} ⟦1⟧ __P1__, ⟦2⟧ and __P2__"]
    assert got == [(1, "format {0} 见 ⟨1⟩, 成分 and ⟨2⟩")]


def test_apply_mappings_skips_placeholders_and_markers():
    text, slots = apply_mappings("⟨1⟩ 1 ⟨12⟩", [("1", "一"), ("12", "十二")])
    assert (text, slots) == ("⟨1⟩ ⟦1⟧ ⟨12⟩", {"1": "一"})  # "12" 只出现在占位符里：不算命中


def test_mapping_and_placeholder_together(make_edge):
    srv = make_edge(edge_map(lambda t: t.replace("in", "中的")))
    got = _collect(_client(srv), [Job(1, "anhedonia in ⟨1⟩")], mappings=[("anhedonia", "快感缺失")])
    assert srv.requests[0]["json"] == ["⟦1⟧ in {1}"]
    assert got == [(1, "快感缺失中的 ⟨1⟩")]


def test_reply_not_html_unescaped(make_edge):
    # 接口回来的 < & 本来就不转义；原文里字面的 &amp; 不能被我们改成 &
    srv = make_edge(lambda h, p: edge_reply(h, ["a &amp; b < c"]))
    assert _collect(_client(srv), [Job(1, "x")]) == [(1, "a &amp; b < c")]


def test_translation_whitespace_stripped(make_edge):
    srv = make_edge(lambda h, p: edge_reply(h, ["  好 \n"]))
    assert _collect(_client(srv), [Job(1, "x")]) == [(1, "好")]


def test_empty_jobs_sends_nothing(make_edge):
    srv = make_edge(edge_map(lambda t: "好"))
    _collect(_client(srv), [])
    assert srv.requests == []


def test_connection_reused_across_calls(make_edge):
    srv = make_edge(edge_map(lambda t: "好"))
    c = _client(srv)
    _collect(c, [Job(1, "a")])
    first = c._http()
    _collect(c, [Job(1, "b")])
    assert c._http() is first and len(srv.requests) == 2


def test_default_url_is_module_constant(monkeypatch):
    monkeypatch.setattr(microsoft, "EDGE_URL", "http://example.invalid/x")
    assert MicrosoftClient().url == "http://example.invalid/x"


def test_real_url():
    # conftest 把模块常量换成了本机地址；这里看源码里写的真地址
    import inspect
    assert 'EDGE_URL = "https://edge.microsoft.com/translate/translatetext"' in inspect.getsource(microsoft)


def test_default_timeouts():
    c = MicrosoftClient()
    assert (c.timeout_connect, c.timeout_read) == (6.0, 8.0)


# ---------- 分批（接口上限 1000 条、5 万字符） ----------
def test_batches_by_count():
    assert [len(b) for b in batches(["a"] * 2500)] == [1000, 1000, 500]


def test_batches_by_chars():
    assert batches(["x" * 20000, "y" * 20000, "z" * 20000]) == [[0, 1], [2]]


def test_batches_oversized_segment_alone():
    assert batches(["s", "x" * 60000, "t"]) == [[0], [1], [2]]


def test_many_segments_sent_in_several_requests_in_order(make_edge):
    srv = make_edge(edge_map(lambda t: "译" + t))
    jobs = [Job(i, f"s{i}") for i in range(1500)]
    got = _collect(_client(srv, timeout_read=5.0), jobs)
    assert [len(r["json"]) for r in srv.requests] == [1000, 500]
    assert got == [(i, f"译s{i}") for i in range(1500)]


def test_second_batch_failure_reports_done(make_edge):
    calls = []

    def handler(h, payload):
        calls.append(len(payload))
        if len(calls) == 2:
            _json(h, 500, {"error": "x"})
        else:
            edge_map(lambda t: "好")(h, payload)

    srv = make_edge(handler)
    with pytest.raises(TranslateError) as ei:
        _collect(_client(srv, timeout_read=5.0), [Job(i, "a") for i in range(1200)])
    assert ei.value.kind == "http" and ei.value.done == frozenset(range(1000))


# ---------- 指定译法 ----------
def test_apply_mappings_longest_first_and_case_rule():
    text, slots = apply_mappings("Major depressive disorder and depression; DEPRESSION.",
                                 [("depression", "抑郁"), ("major depressive disorder", "重度抑郁障碍")])
    # 全小写词条不分大小写；长词条先换，短词条不会抢走它里面的一截
    assert text == "⟦1⟧ and ⟦2⟧; ⟦2⟧."
    assert slots == {"1": "重度抑郁障碍", "2": "抑郁"}


def test_apply_mappings_capitalized_term_exact():
    text, slots = apply_mappings("GPU and Gpu", [("GPU", "图形处理器")])
    assert text == "⟦1⟧ and Gpu" and slots == {"1": "图形处理器"}


def test_apply_mappings_skips_absent_and_blank():
    assert apply_mappings("hello", [("world", "世界"), ("", "空"), ("hello", " ")]) == ("hello", {})


def test_restore_mappings_tolerates_spaces():
    assert restore_mappings("患者有 ⟦ 1 ⟧ 和⟦2⟧", {"1": "快感缺失", "2": "抑郁"}) == "患者有 快感缺失 和抑郁"


def test_restore_to_chinese_drops_spaces_next_to_cjk_only():
    slots = {"1": "快感缺失", "2": "重性抑郁障碍"}
    assert restore_mappings("⟦1⟧ 是 ⟦2⟧ 的核心症状", slots, True) == "快感缺失是重性抑郁障碍的核心症状"
    assert restore_mappings("通过 PHQ-9 测 ⟦1⟧ ，", slots, True) is None  # ⟦2⟧ 丢了
    assert restore_mappings("用 PHQ ⟦1⟧ ⟦2⟧", slots, True) == "用 PHQ 快感缺失 重性抑郁障碍"  # 挨着英文、两个记号之间：不动
    assert restore_mappings("⟦1⟧ is ⟦2⟧", slots) == "快感缺失 is 重性抑郁障碍"  # 译成英文：不动


@pytest.mark.parametrize("out", ["患者有 ⟦1⟧", "患者有 ⟦1⟧ ⟦2⟧ ⟦3⟧"])
def test_restore_mappings_lost_or_extra_is_none(out):
    assert restore_mappings(out, {"1": "a", "2": "b"}) is None


def test_mapping_sent_as_marker_and_replaced(make_edge):
    srv = make_edge(edge_map(lambda t: t.replace("Patients with", "患者有")))
    got = _collect(_client(srv), [Job(1, "Patients with anhedonia")], mappings=[("anhedonia", "快感缺失")])
    assert srv.requests[0]["json"] == ["Patients with ⟦1⟧"]
    assert got == [(1, "患者有快感缺失")]  # 挨着汉字的空格去掉


def test_mapping_marker_lost_retried_without_mapping(make_edge):
    def handler(h, payload):
        edge_reply(h, ["丢了记号" if "⟦" in payload[0] else "不带替换：" + payload[0]])

    srv = make_edge(handler)
    got = _collect(_client(srv), [Job(1, "Patients with anhedonia")], mappings=[("anhedonia", "快感缺失")])
    assert [r["json"] for r in srv.requests] == [["Patients with ⟦1⟧"], ["Patients with anhedonia"]]
    assert got == [(1, "不带替换：Patients with anhedonia")]


@pytest.mark.parametrize("dst,expected", [
    ("anhedonia", "Anhedonia is a core symptom."),   # 全小写译法落在句首：句首大写
    ("fMRI", "fMRI is a core symptom."),             # 带大写的译法原样
])
def test_zh2en_lowercase_mapping_at_sentence_start_capitalized(make_edge, dst, expected):
    srv = make_edge(edge_map(lambda t: t.replace("是核心症状。", " is a core symptom.")))
    got = _collect(_client(srv), [Job(1, "快感缺失是核心症状。")], "zh2en", mappings=[("快感缺失", dst)])
    assert got == [(1, expected)]


def test_zh2en_mapping_mid_sentence_not_capitalized(make_edge):
    srv = make_edge(edge_map(lambda t: "We measured " + t.replace("。", ".")))
    got = _collect(_client(srv), [Job(1, "快感缺失。")], "zh2en", mappings=[("快感缺失", "anhedonia")])
    assert got == [(1, "We measured anhedonia.")]


def test_en2zh_mapping_at_start_untouched(make_edge):
    srv = make_edge(edge_map(lambda t: t.replace(" matters", "很重要")))
    got = _collect(_client(srv), [Job(1, "anhedonia matters")], mappings=[("anhedonia", "abc")])
    assert got == [(1, "abc很重要")]  # 只管译成英文的方向


def test_mapping_only_where_term_appears(make_edge):
    srv = make_edge(edge_map(lambda t: t))
    _collect(_client(srv), [Job(1, "anhedonia"), Job(2, "nothing here")], mappings=[("anhedonia", "快感缺失")])
    assert srv.requests[0]["json"] == ["⟦1⟧", "nothing here"]


# ---------- 取消 ----------
def test_cancel_before_start_sends_nothing(make_edge):
    srv = make_edge(edge_map(lambda t: "好"))
    ev = threading.Event()
    ev.set()
    assert _collect(_client(srv), [Job(1, "a")], cancel=ev) == []
    assert srv.requests == []


def test_cancel_between_segments_stops_delivery(make_edge):
    srv = make_edge(edge_map(lambda t: "好"))
    ev = threading.Event()
    got = []

    def on(i, t):
        got.append(i)
        ev.set()

    _client(srv).translate_stream([Job(1, "a"), Job(2, "b"), Job(3, "c")], "en2zh", [], on, ev)
    assert got == [1]


# ---------- 失败一律 TranslateError ----------
def _err(srv, jobs=None):
    with pytest.raises(TranslateError) as ei:
        _collect(_client(srv), jobs or [Job(1, "a")])
    return ei.value


def test_connect_refused_is_connect():
    c = MicrosoftClient(timeout_connect=0.5, timeout_read=0.5, url=closed_port_url())
    with pytest.raises(TranslateError) as ei:
        _collect(c, [Job(1, "a")])
    assert ei.value.kind == "connect"


def test_connect_error_retried_once_then_succeeds(make_edge, monkeypatch):
    """实测偶尔首次 TLS 握手被断（SSL UNEXPECTED_EOF）：马上重来一次就好。"""
    import httpx

    srv = make_edge(edge_map(lambda t: "好"))
    c = _client(srv)
    http = c._http()
    real_post, calls = http.post, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")
        return real_post(*a, **k)

    monkeypatch.setattr(http, "post", flaky)
    assert _collect(c, [Job(1, "a")]) == [(1, "好")] and len(calls) == 2


def test_connect_timeout_not_retried(monkeypatch):
    import httpx

    c = MicrosoftClient(url="http://127.0.0.1:1/x")
    calls = []

    def slow(*a, **k):
        calls.append(1)
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(c._http(), "post", slow)
    with pytest.raises(TranslateError) as ei:
        _collect(c, [Job(1, "a")])
    assert ei.value.kind == "connect" and calls == [1]  # 连不上时不白等两遍


def test_read_timeout_is_connect(make_edge):
    import time

    def slow(h, p):
        time.sleep(1.5)
        edge_reply(h, ["好"])

    srv = make_edge(slow)
    c = MicrosoftClient(timeout_connect=0.5, timeout_read=0.2, url=srv.url)
    with pytest.raises(TranslateError) as ei:
        _collect(c, [Job(1, "a")])
    assert ei.value.kind == "connect"


def test_non_200_is_http(make_edge):
    srv = make_edge(lambda h, p: _json(h, 400, {"error": "Request exceeds the maximum allowed translation size."}))
    e = _err(srv)
    assert e.kind == "http" and "400" in e.detail


def test_not_json_is_format(make_edge):
    def handler(h, p):
        h.send_response(200)
        h.send_header("Content-Length", "5")
        h.end_headers()
        h.wfile.write(b"hello")

    assert _err(make_edge(handler)).kind == "format"


@pytest.mark.parametrize("body", [{"a": 1}, [{"x": 1}], [{"translations": []}], [{"translations": [{}]}], [3]])
def test_unknown_shape_is_format(make_edge, body):
    assert _err(make_edge(lambda h, p: _json(h, 200, body))).kind == "format"


def test_translation_not_string_is_format(make_edge):
    srv = make_edge(lambda h, p: _json(h, 200, [{"translations": [{"text": 3}]}]))
    assert _err(srv).kind == "format"


def test_count_mismatch_is_format(make_edge):
    srv = make_edge(lambda h, p: edge_reply(h, ["只有一条"]))
    e = _err(srv, [Job(1, "a"), Job(2, "b")])
    assert e.kind == "format" and "1 条" in e.detail


def test_empty_translation_is_format_with_done(make_edge):
    srv = make_edge(lambda h, p: edge_reply(h, ["好", "  "]))
    got = []
    with pytest.raises(TranslateError) as ei:
        _client(srv).translate_stream([Job(1, "a"), Job(2, "b")], "en2zh", [], lambda i, t: got.append(i))
    assert got == [1] and ei.value.kind == "format" and ei.value.done == frozenset({1})

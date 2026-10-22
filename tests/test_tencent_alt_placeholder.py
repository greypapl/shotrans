"""原文本来就有 {数字} 的段：占位符改用 __Pn__ 发，译文里的 {n} 一律当原文、不换成占位符。"""
from shotrans.translate.base import Job
from shotrans.translate.tencent import TencentClient
from tests.fakes import transmart_reply


def _client(srv):
    return TencentClient(timeout_connect=0.5, timeout_read=1.0, url=srv.url)


def _collect(client, jobs):
    got = []
    client.translate_stream(jobs, "en2zh", [], lambda i, t: got.append((i, t)))
    return got


def test_source_with_literal_brace_sends_alt_form(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["好"]))
    _collect(_client(srv), [Job(1, "call f({0}) see ⟨1⟩")])
    assert srv.requests[0]["json"]["source"]["text_list"] == ["call f({0}) see __P1__"]


def test_alt_form_restored_and_reply_braces_left_alone(make_transmart):
    # 译文里的 {1} 是原文就有的那种（我们这段没用 {n}），不能被当成占位符
    srv = make_transmart(lambda h, p: transmart_reply(h, ["调用 f({0}) 和 {1}，见 __P1__"]))
    got = _collect(_client(srv), [Job(1, "call f({0}) and {1}, see ⟨1⟩")])
    assert got == [(1, "调用 f({0}) 和 {1}，见 ⟨1⟩")]


def test_alt_form_tolerates_spaces(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["{0} 见 __ P1 __"]))
    assert _collect(_client(srv), [Job(1, "{0} see ⟨1⟩")]) == [(1, "{0} 见 ⟨1⟩")]


def test_alt_form_only_for_that_segment(make_transmart):
    srv = make_transmart(lambda h, p: transmart_reply(h, ["甲 __P1__", "乙 {2}"]))
    got = _collect(_client(srv), [Job(1, "a {0} ⟨1⟩"), Job(2, "b ⟨2⟩")])
    assert srv.requests[0]["json"]["source"]["text_list"] == ["a {0} __P1__", "b {2}"]
    assert got == [(1, "甲 ⟨1⟩"), (2, "乙 ⟨2⟩")]


def test_alt_form_only_restores_our_numbers(make_transmart):
    # 原文本身就写着 __P7__（极少见）：不是我们换的编号，原样保留
    srv = make_transmart(lambda h, p: transmart_reply(h, ["甲 {0} __P7__ __P1__"]))
    assert _collect(_client(srv), [Job(1, "x {0} __P7__ ⟨1⟩")]) == [(1, "甲 {0} __P7__ ⟨1⟩")]


def test_brace_form_only_restores_our_numbers(make_transmart):
    # 译文里冒出一个我们没发过的 {5}：不当占位符
    srv = make_transmart(lambda h, p: transmart_reply(h, ["见 {1} 和 {5}"]))
    assert _collect(_client(srv), [Job(1, "see ⟨1⟩")]) == [(1, "见 ⟨1⟩ 和 {5}")]

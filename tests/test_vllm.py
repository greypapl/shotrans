import time

import pytest

from shotrans.translate.base import Job, TranslateError
from shotrans.translate.vllm import SegmentStreamParser, VllmClient, build_messages
from tests.fakes import JOBS2, _client, _collect, closed_port_url, send_delta, sse_chunks, start_sse


# ---------- 提示词 ----------

def test_messages_numbered_user_content():
    msgs = build_messages(JOBS2, "en2zh", [])
    assert msgs[1] == {"role": "user", "content": "⟦1⟧ Hello\n⟦2⟧ World"}
    assert "把用户给出的英文逐段翻译成简体中文" in msgs[0]["content"]


def test_messages_zh2en_names():
    assert "把用户给出的简体中文逐段翻译成英文" in build_messages(JOBS2, "zh2en", [])[0]["content"]


def test_messages_no_domain_by_default():
    assert "领域" not in build_messages(JOBS2, "en2zh", [])[0]["content"]


def test_messages_domain_hint_when_set():
    assert "领域：心理学" in build_messages(JOBS2, "en2zh", [], "心理学")[0]["content"]


@pytest.mark.parametrize("direction", ["en2zh", "zh2en"])
def test_messages_keep_names_and_code_rule(direction):
    # Task 20：人名、机构名、产品名、代码、命令、按键组合、路径、网址不翻——两个方向都要有
    sys_prompt = build_messages(JOBS2, direction, [])[0]["content"]
    assert "4. 人名、机构名、产品/软件名、代码、命令、按键组合、文件路径、网址保持原文，不翻译、不音译。" in sys_prompt
    # 只有上半句时论文标题会被当成专名原样留下：要明说其余文字照常翻
    assert "其余文字（包括论文标题、栏目名、按钮文字）都要翻译。" in sys_prompt
    assert sys_prompt.index("3. ⟨数字⟩") < sys_prompt.index("4. 人名")


@pytest.mark.parametrize("direction", ["en2zh", "zh2en"])
def test_messages_capitalized_common_words_still_translated(direction):
    # Task 21：模型把「Blood project」「Cascade journal」里大写开头的普通词也当专名留下——明说只有专名才保留
    sys_prompt = build_messages(JOBS2, direction, [])[0]["content"]
    assert "只有确实是专名的才保持原文；句首或标题、标签里首字母大写的普通词照常翻译。" in sys_prompt
    assert sys_prompt.index("都要翻译。") < sys_prompt.index("只有确实是专名的")


def test_messages_glossary_lines():
    sys_prompt = build_messages(JOBS2, "en2zh", [("depression", "抑郁")])[0]["content"]
    assert "- depression → 抑郁" in sys_prompt


# ---------- 解析器 ----------

def test_parser_marker_split_across_chunks():
    p = SegmentStreamParser()
    assert p.feed("⟦1⟧ 你") == []
    assert p.feed("好\n⟦") == []
    assert p.feed("2⟧ 世界") == [(1, "你好")]
    assert p.finish() == [(2, "世界")]


def test_parser_ignores_preamble():
    p = SegmentStreamParser()
    p.feed("好的，译文如下：\n⟦1⟧ 一 ⟦2⟧ 二")
    assert p.finish() == [(2, "二")]


# ---------- 真流式（假服务器） ----------

def test_stream_delivers_in_order_and_payload(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 你", "好\n⟦", "2⟧ 世界"]))
    assert _collect(_client(s.url), JOBS2) == [(1, "你好"), (2, "世界")]
    req = s.requests[0]
    assert req["auth"] == "Bearer k"
    assert req["json"]["model"] == "fake-model"
    assert req["json"]["stream"] is True
    assert req["json"]["temperature"] == 0.2
    assert req["json"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_no_api_key_sends_no_auth_header(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 一"]))
    VllmClient(s.url, "").translate_stream([Job(1, "a")], "en2zh", [], lambda i, t: None)
    assert s.requests[0]["auth"] is None


def test_first_token_timeout(make_server):
    def handler(h, payload):
        start_sse(h)
        time.sleep(1.5)

    s = make_server(handler)
    t0 = time.monotonic()
    with pytest.raises(TranslateError) as ei:
        _collect(_client(s.url), JOBS2)
    assert ei.value.kind == "first_token"
    assert time.monotonic() - t0 < 1.2


def test_stall_keeps_finished_segments(make_server):
    def handler(h, payload):
        start_sse(h)
        send_delta(h, "⟦1⟧ 一")
        send_delta(h, "⟦2⟧ 二")
        time.sleep(1.5)

    s = make_server(handler)
    got = []
    with pytest.raises(TranslateError) as ei:
        _client(s.url).translate_stream(JOBS2, "en2zh", [], lambda i, t: got.append((i, t)))
    assert ei.value.kind == "stall"
    assert got == [(1, "一")]
    assert ei.value.done == {1}


def test_long_generation_is_not_a_failure(make_server):
    # 总时长超过 first_token + stall 也没关系，只要字一直在来
    # 12 块 × 0.25s ≈ 3s，远超 first_token(0.4)+stall(0.8)=1.2s；相邻两块间隔 0.25s 远小于 stall
    chunks = ["⟦1⟧ 一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "⟦2⟧ 甲", "乙"]
    s = make_server(sse_chunks(chunks, delay=0.25))
    c = VllmClient(s.url, "k", connect_timeout=0.5, first_token_timeout=0.4, stall_timeout=0.8)
    assert _collect(c, JOBS2) == [(1, "一二三四五六七八九十"), (2, "甲乙")]


def test_connection_refused():
    with pytest.raises(TranslateError) as ei:
        _collect(_client(closed_port_url()), JOBS2)
    assert ei.value.kind == "connect"


def test_http_error(make_server):
    def handler(h, payload):
        h.send_error(500)

    s = make_server(handler)
    with pytest.raises(TranslateError) as ei:
        _collect(_client(s.url), JOBS2)
    assert ei.value.kind == "http"


def test_missing_segment_is_format_error(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 一"]))
    with pytest.raises(TranslateError) as ei:
        _collect(_client(s.url), JOBS2)
    assert ei.value.kind == "format"
    assert ei.value.done == {1}


def test_single_job_without_marker_accepted(make_server):
    s = make_server(sse_chunks(["你好"]))
    assert _collect(_client(s.url), [Job(7, "Hello")]) == [(7, "你好")]


def test_unknown_index_ignored(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 一 ⟦9⟧ 九 ⟦2⟧ 二"]))
    assert _collect(_client(s.url), JOBS2) == [(1, "一"), (2, "二")]


def test_cancel_stops_quietly(make_server):
    import threading

    s = make_server(sse_chunks(["⟦1⟧ 一", "⟦2⟧ 二"], delay=0.2))
    cancel = threading.Event()
    cancel.set()
    got = []
    _client(s.url).translate_stream(JOBS2, "en2zh", [], lambda i, t: got.append(i), cancel)
    assert got == []

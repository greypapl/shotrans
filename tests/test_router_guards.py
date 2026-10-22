"""Router 的守卫专属测试：每个分支各有只触发它的数据，期望值写死。"""
import threading

import pytest

from shotrans.glossary import Glossary
from shotrans.translate.base import TranslateError
from tests.router_fakes import Clock, echo, fail, make, make_no_fb, run, seg


# ---------- _merge_maps ----------
def test_same_glossary_pair_sent_once_across_segments():
    g = Glossary(mappings=(("depression", "抑郁"),))
    r, v, m = make(echo("V"), glossary=g)
    run(r, [seg(1, "depression a"), seg(2, "depression b")])
    assert v.calls[0]["mappings"] == [("depression", "抑郁")]


# ---------- handle 里的三个守卫 ----------
def test_output_for_unknown_idx_is_ignored():
    def script(svc, jobs, on):
        on(99, "幽灵")
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, v, m = make(script)
    res, out = run(r, [seg(1, "a")])
    assert set(res) == {1} and len(out) == 1


def test_lost_placeholder_reported_twice_is_retried_once():
    state = {"n": 0}

    def script(svc, jobs, on):
        state["n"] += 1
        for j in jobs:
            if state["n"] == 1:
                on(j.idx, "丢了")
                on(j.idx, "又丢了")
            else:
                on(j.idx, "V" + j.text)

    r, v, m = make(script)
    res, out = run(r, [seg(1, "see [12]")])
    assert len(v.calls) == 2
    assert len(out) == 1 and out[0].text == "Vsee [12]"


# ---------- _single 的两个出口 ----------
def test_lost_retry_that_errors_shows_original_and_stays_on_service():
    state = {"n": 0}

    def script(svc, jobs, on):
        state["n"] += 1
        if state["n"] == 1:
            for j in jobs:
                on(j.idx, "丢了")
        else:
            raise TranslateError("connect")

    r, v, m = make(script)
    res, out = run(r, [seg(1, "see [12]")])
    assert [(x.text, x.service, x.note) for x in out] == [("see [12]", "vllm", "占位符丢失，显示原文")]
    assert not r.vllm_down()
    assert m.calls == []


def test_lost_retry_with_no_output_shows_original():
    state = {"n": 0}

    def script(svc, jobs, on):
        state["n"] += 1
        if state["n"] == 1:
            for j in jobs:
                on(j.idx, "丢了")

    r, v, m = make(script)
    res, out = run(r, [seg(1, "see [12]")])
    assert [(x.text, x.service, x.note) for x in out] == [("see [12]", "vllm", "占位符丢失，显示原文")]


def test_lost_twice_is_not_forwarded_to_fallback():
    r, v, m = make(lambda svc, jobs, on: [on(j.idx, "丢了") for j in jobs])
    res, out = run(r, [seg(1, "see [12]")])
    assert len(out) == 1
    assert m.calls == []


# ---------- _attempt 失败时 done 由路由自己记 ----------
def test_router_records_done_even_if_service_reports_none():
    def script(svc, jobs, on):
        on(jobs[0].idx, "V" + jobs[0].text)
        raise TranslateError("stall")  # 不带 done

    r, v, m = make(script)
    res, out = run(r, [seg(1, "a"), seg(2, "b")])
    assert [j.idx for j in m.calls[0]["jobs"]] == [2]
    assert sorted(x.idx for x in out) == [1, 2]
    assert (res[1].text, res[1].service) == ("Va", "vllm")


# ---------- _per_segment ----------
def test_per_segment_stops_and_trips_breaker_on_non_format_error():
    def script(svc, jobs, on):
        if len(jobs) > 1:
            raise TranslateError("format")
        raise TranslateError("connect")

    clock = Clock()
    r, v, m = make(script, clock=clock)
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert [len(c["jobs"]) for c in v.calls] == [2, 1]   # 第二段没再试
    assert r.vllm_down()
    assert {x.service for x in res.values()} == {"fallback"}


def test_per_segment_continues_after_single_format_error():
    def script(svc, jobs, on):
        if len(jobs) > 1 or jobs[0].idx == 1:
            raise TranslateError("format")
        on(jobs[0].idx, "V" + jobs[0].text)

    r, v, m = make(script)
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert [len(c["jobs"]) for c in v.calls] == [2, 1, 1]
    assert (res[1].text, res[1].service) == ("Ma", "fallback")
    assert (res[2].text, res[2].service) == ("Vb", "vllm")
    assert not r.vllm_down()


def test_per_segment_successes_are_not_resent_to_fallback():
    def script(svc, jobs, on):
        if len(jobs) > 1:
            raise TranslateError("format")
        on(jobs[0].idx, "V" + jobs[0].text)

    r, v, m = make(script)
    run(r, [seg(1, "a"), seg(2, "b")])
    assert m.calls == []


def test_per_segment_stops_when_cancelled():
    cancel = threading.Event()

    def script(svc, jobs, on):
        if len(jobs) > 1:
            raise TranslateError("format")
        on(jobs[0].idx, "V" + jobs[0].text)
        cancel.set()

    r, v, m = make(script)
    res, out = run(r, [seg(1, "a"), seg(2, "b")], cancel=cancel)
    assert [len(c["jobs"]) for c in v.calls] == [2, 1]
    assert [x.idx for x in out] == [1]
    assert m.calls == []


# ---------- 链中取消 ----------
def test_cancel_during_vllm_failure_skips_fallback_and_emits_nothing():
    cancel = threading.Event()

    def script(svc, jobs, on):
        cancel.set()
        raise TranslateError("connect")

    r, v, m = make(script)
    res, out = run(r, [seg(1, "a")], cancel=cancel)
    assert m.calls == []
    assert out == []


def test_error_while_cancelled_does_not_trip_breaker():
    cancel = threading.Event()

    def script(svc, jobs, on):
        cancel.set()
        raise TranslateError("connect")

    r, v, m = make(script)
    run(r, [seg(1, "a")], cancel=cancel)
    assert not r.vllm_down()


def test_per_segment_error_while_cancelled_does_not_trip_breaker():
    cancel = threading.Event()

    def script(svc, jobs, on):
        if len(jobs) > 1:
            raise TranslateError("format")
        cancel.set()
        raise TranslateError("connect")

    r, v, m = make(script)
    _, out = run(r, [seg(1, "a"), seg(2, "b")], cancel=cancel)
    assert [len(c["jobs"]) for c in v.calls] == [2, 1]
    assert not r.vllm_down()
    assert out == []


def test_no_unavailable_note_after_cancel():
    cancel = threading.Event()

    def script(svc, jobs, on):
        cancel.set()
        raise TranslateError("connect")

    r, v = make_no_fb(script)
    _, out = run(r, [seg(1, "a"), seg(2, "b")], cancel=cancel)
    assert out == []


def test_no_lost_note_when_cancelled_before_retry():
    cancel = threading.Event()

    def script(svc, jobs, on):
        for j in jobs:
            on(j.idx, "丢了")
        cancel.set()

    r, v, m = make(script)
    _, out = run(r, [seg(1, "see [12]")], cancel=cancel)
    assert len(v.calls) == 1               # 取消后不再重试
    assert out == []


def test_no_lost_note_when_cancelled_during_retry():
    cancel = threading.Event()
    state = {"n": 0}

    def script(svc, jobs, on):
        state["n"] += 1
        for j in jobs:
            on(j.idx, "丢了")
        if state["n"] == 2:
            cancel.set()

    r, v, m = make(script)
    _, out = run(r, [seg(1, "see [12]")], cancel=cancel)
    assert len(v.calls) == 2
    assert out == []


def test_cancel_object_is_forwarded_to_service():
    cancel = threading.Event()
    r, v, m = make(echo("V"))
    run(r, [seg(1, "a")], cancel=cancel)
    assert v.calls[0]["cancel"] is cancel


# ---------- 备用为 None ----------
def test_no_fallback_vllm_ok():
    r, v = make_no_fb(echo("V"))
    res, _ = run(r, [seg(1, "a")])
    assert (res[1].text, res[1].service) == ("Va", "vllm")


def test_no_fallback_vllm_fails_shows_original_and_trips_breaker():
    r, v = make_no_fb(fail("connect"))
    res, _ = run(r, [seg(1, "a")])
    assert (res[1].text, res[1].service, res[1].note) == ("a", "none", "翻译服务不可用")
    assert r.vllm_down()


def test_no_fallback_breaker_open_skips_vllm_and_marks_unavailable():
    r, v = make_no_fb(fail("connect"))
    run(r, [seg(1, "a")])
    res, _ = run(r, [seg(2, "b")])
    assert len(v.calls) == 1
    assert (res[2].text, res[2].service, res[2].note) == ("b", "none", "翻译服务不可用")


def test_no_fallback_format_error_leftovers_marked_unavailable():
    def script(svc, jobs, on):
        if len(jobs) > 1 or jobs[0].idx == 1:
            raise TranslateError("format")
        on(jobs[0].idx, "V" + jobs[0].text)

    r, v = make_no_fb(script)
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert (res[1].service, res[1].note) == ("none", "翻译服务不可用")
    assert (res[2].text, res[2].service) == ("Vb", "vllm")


def test_no_fallback_retranslate_from_vllm_is_none_and_untouched():
    r, v = make_no_fb(echo("V"))
    assert r.retranslate_one(seg(1, "a"), "en2zh", "vllm") is None
    assert v.calls == []
    assert not r.vllm_down()


# ---------- 缓存（含备用） ----------
def test_fallback_result_is_cached_while_breaker_open():
    r, v, m = make(fail("connect"))
    run(r, [seg(1, "a")])
    res, _ = run(r, [seg(2, "a")])
    assert len(m.calls) == 1
    assert (res[2].text, res[2].service) == ("Ma", "fallback")


def test_retranslate_result_is_cached():
    r, v, m = make(echo("V"))
    r.retranslate_one(seg(1, "a"), "en2zh", "vllm")      # 备用译成 Ma，写缓存
    res, _ = run(r, [seg(2, "a")])
    assert v.calls == [] and len(m.calls) == 1
    assert (res[2].text, res[2].service) == ("Ma", "fallback")


# ---------- 缓存键含术语表 ----------
def test_glossary_change_is_not_masked_by_cache():
    r, v, m = make(echo("V"), glossary=Glossary(mappings=(("depression", "抑郁"),)))
    run(r, [seg(1, "depression a")])
    r.glossary = Glossary(mappings=(("depression", "忧郁症"),))
    run(r, [seg(1, "depression a")])
    assert len(v.calls) == 2
    assert v.calls[1]["mappings"] == [("depression", "忧郁症")]


def test_keep_term_change_is_not_masked_by_cache():
    r, v, m = make(echo("V"), glossary=Glossary())
    run(r, [seg(1, "PHQ-8 a")])
    r.glossary = Glossary(keep=("PHQ-8",))
    res, _ = run(r, [seg(1, "PHQ-8 a")])
    assert len(v.calls) == 2
    assert res[1].text == "VPHQ-8 a"


def test_unchanged_glossary_still_hits_cache():
    g = Glossary(mappings=(("depression", "抑郁"),), keep=("PHQ-8",))
    r, v, m = make(echo("V"), glossary=g)
    run(r, [seg(1, "depression PHQ-8")])
    r.glossary = Glossary(mappings=(("depression", "抑郁"),), keep=("PHQ-8",))  # 内容相同的新对象
    run(r, [seg(2, "depression PHQ-8")])
    assert len(v.calls) == 1


def test_unrelated_glossary_entry_does_not_invalidate_cache():
    r, v, m = make(echo("V"), glossary=Glossary())
    run(r, [seg(1, "a")])
    r.glossary = Glossary(mappings=(("depression", "抑郁"),))   # 与「a」无关
    run(r, [seg(1, "a")])
    assert len(v.calls) == 1


# ---------- 熔断边界 ----------
def test_breaker_closed_at_exactly_sixty_seconds():
    clock = Clock()
    r, v, m = make(fail("connect"), clock=clock)
    run(r, [seg(1, "a")])                  # t=0 熔断
    clock.t = 59.9
    assert r.vllm_down()
    clock.t = 60
    assert not r.vllm_down()


# ---------- retranslate_one 的细节 ----------
def test_retranslate_with_lost_placeholder_returns_none_without_tripping():
    r, v, m = make(lambda svc, jobs, on: [on(j.idx, "丢了") for j in jobs])
    assert r.retranslate_one(seg(1, "see [12]"), "en2zh", "fallback") is None
    assert not r.vllm_down()


def test_retranslate_format_error_does_not_trip_breaker():
    r, v, m = make(fail("format"))
    assert r.retranslate_one(seg(1, "a"), "en2zh", "fallback") is None
    assert not r.vllm_down()


def test_retranslate_empty_output_does_not_trip_breaker():
    r, v, m = make(lambda svc, jobs, on: None)
    assert r.retranslate_one(seg(1, "a"), "en2zh", "fallback") is None
    assert len(v.calls) == 1
    assert not r.vllm_down()


@pytest.mark.parametrize("kind", ["connect", "first_token", "stall", "http"])
def test_retranslate_connectivity_errors_trip_breaker(kind):
    r, v, m = make(fail(kind))
    assert r.retranslate_one(seg(1, "a"), "en2zh", "fallback") is None
    assert r.vllm_down()


def test_fallback_retranslate_failure_does_not_trip_vllm_breaker():
    r, v, m = make(echo("V"), fail("connect"))
    assert r.retranslate_one(seg(1, "a"), "en2zh", "vllm") is None
    assert not r.vllm_down()


# ---------- 备用失败不能连累 vLLM ----------
@pytest.mark.parametrize("kind", ["connect", "format"])
def test_fallback_failure_does_not_touch_vllm_state(kind):
    clock = Clock()
    r, v, m = make(fail("connect"), clock=clock)
    run(r, [seg(1, "a")])                  # t=0 vLLM 熔断
    m.script = fail(kind)
    clock.t = 30
    res, _ = run(r, [seg(2, "b")])         # 备用也失败
    assert res[2].service == "none"
    assert len(v.calls) == 1               # 备用的 format 错误不会触发 vLLM 逐段
    clock.t = 61
    assert not r.vllm_down()               # 备用失败没有重置 vLLM 的熔断计时


# ---------- 补强：开头取消守卫、熔断期间不重翻 ----------
def test_cancelled_run_does_not_even_report_cache_hits():
    r, v, m = make(echo("V"))
    run(r, [seg(1, "a")])                  # 写入缓存
    cancel = threading.Event()
    cancel.set()
    res, out = run(r, [seg(2, "a")], cancel=cancel)
    assert out == []


def test_retranslate_to_vllm_refused_while_breaker_open_even_if_vllm_recovered():
    r, v, m = make(fail("connect"))
    run(r, [seg(1, "a")])                  # 熔断
    v.script = echo("V")                   # 服务已恢复，但熔断窗口内不打扰它
    calls_before = len(v.calls)
    assert r.retranslate_one(seg(1, "a"), "en2zh", "fallback") is None
    assert len(v.calls) == calls_before

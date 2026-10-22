import threading

from shotrans.glossary import Glossary
from shotrans.translate.base import TranslateError
from tests.router_fakes import Clock, echo, fail, make, run, seg


def test_vllm_ok_fallback_untouched():
    r, v, m = make(echo("V"))
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert {i: (x.text, x.service) for i, x in res.items()} == {1: ("Va", "vllm"), 2: ("Vb", "vllm")}
    assert m.calls == []


def test_vllm_connect_fail_uses_fallback_and_trips_breaker():
    r, v, m = make(fail("connect"))
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert {x.service for x in res.values()} == {"fallback"}
    assert r.vllm_down()


def test_breaker_skips_vllm_then_recovers():
    clock = Clock()
    r, v, m = make(fail("connect"), clock=clock)
    run(r, [seg(1, "a")])
    run(r, [seg(1, "b")])
    assert len(v.calls) == 1          # 60 秒内不再白等 vLLM
    clock.t = 61
    run(r, [seg(1, "c")])
    assert len(v.calls) == 2


def test_midstream_failure_keeps_done_sends_rest_to_fallback():
    def script(svc, jobs, on):
        on(jobs[0].idx, "V" + jobs[0].text)
        raise TranslateError("stall", done={jobs[0].idx})

    r, v, m = make(script)
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert (res[1].text, res[1].service) == ("Va", "vllm")
    assert (res[2].text, res[2].service) == ("Mb", "fallback")
    assert [j.idx for j in m.calls[0]["jobs"]] == [2]


def test_both_fail_shows_original_with_note():
    r, v, m = make(fail("connect"), fail("connect"))
    res, _ = run(r, [seg(1, "a")])
    assert (res[1].text, res[1].service, res[1].note) == ("a", "none", "翻译服务不可用")


def test_placeholder_lost_once_retried_alone():
    state = {"n": 0}

    def script(svc, jobs, on):
        state["n"] += 1
        for j in jobs:
            on(j.idx, "丢了" if state["n"] == 1 else "V" + j.text)

    r, v, m = make(script)
    res, _ = run(r, [seg(1, "see [12]")])
    assert (res[1].text, res[1].service, res[1].note) == ("Vsee [12]", "vllm", None)
    assert len(v.calls) == 2 and len(v.calls[1]["jobs"]) == 1


def test_placeholder_lost_twice_shows_original():
    r, v, m = make(lambda svc, jobs, on: [on(j.idx, "丢了") for j in jobs])
    res, _ = run(r, [seg(1, "see [12]")])
    assert (res[1].text, res[1].service, res[1].note) == ("see [12]", "vllm", "占位符丢失，显示原文")


def test_format_error_falls_back_to_one_request_per_segment():
    def script(svc, jobs, on):
        if len(jobs) > 1:
            raise TranslateError("format")
        on(jobs[0].idx, "V" + jobs[0].text)

    r, v, m = make(script)
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert {x.service for x in res.values()} == {"vllm"}
    assert [len(c["jobs"]) for c in v.calls] == [2, 1, 1]
    assert not r.vllm_down()


def test_cache_avoids_second_request():
    r, v, m = make(echo("V"))
    run(r, [seg(1, "a")])
    res, _ = run(r, [seg(5, "a")])
    assert len(v.calls) == 1
    assert (res[5].text, res[5].service) == ("Va", "vllm")


def test_cache_is_per_direction():
    r, v, m = make(echo("V"))
    run(r, [seg(1, "a")], "en2zh")
    run(r, [seg(1, "a")], "zh2en")
    assert len(v.calls) == 2


def test_skip_segments_not_sent_nor_reported():
    r, v, m = make(echo("V"))
    res, _ = run(r, [seg(1, "a"), seg(2, "x = 1", skip=True)])
    assert set(res) == {1}
    assert [j.idx for j in v.calls[0]["jobs"]] == [1]


def test_glossary_keep_protected_and_mapping_passed():
    g = Glossary(mappings=(("depression", "抑郁"),), keep=("PHQ-8",))
    r, v, m = make(echo("V"), glossary=g)
    res, _ = run(r, [seg(1, "PHQ-8 and depression")])
    assert v.calls[0]["jobs"][0].text == "⟨1⟩ and depression"
    assert v.calls[0]["mappings"] == [("depression", "抑郁")]
    assert res[1].text == "VPHQ-8 and depression"


def test_each_segment_reported_exactly_once():
    def script(svc, jobs, on):
        for j in jobs:
            on(j.idx, "V" + j.text)
            on(j.idx, "again")

    r, v, m = make(script)
    _, out = run(r, [seg(1, "a"), seg(2, "b")])
    assert sorted(x.idx for x in out) == [1, 2]


def test_cancel_before_start_does_nothing():
    r, v, m = make(echo("V"))
    cancel = threading.Event()
    cancel.set()
    res, _ = run(r, [seg(1, "a")], cancel=cancel)
    assert res == {} and v.calls == [] and m.calls == []


def test_retranslate_vllm_to_fallback():
    r, v, m = make(echo("V"))
    res = r.retranslate_one(seg(3, "a"), "en2zh", "vllm")
    assert (res.idx, res.text, res.service) == (3, "Ma", "fallback")


def test_retranslate_fallback_to_vllm():
    r, v, m = make(echo("V"))
    res = r.retranslate_one(seg(3, "a"), "en2zh", "fallback")
    assert (res.text, res.service) == ("Va", "vllm")


def test_retranslate_none_when_vllm_down():
    r, v, m = make(fail("connect"))
    run(r, [seg(1, "a")])
    assert r.retranslate_one(seg(1, "a"), "en2zh", "fallback") is None


def test_retranslate_failure_trips_breaker():
    r, v, m = make(fail("connect"))
    assert r.retranslate_one(seg(1, "a"), "en2zh", "fallback") is None
    assert r.vllm_down()

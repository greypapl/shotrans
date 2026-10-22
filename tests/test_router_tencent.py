"""第三层（腾讯）和「vLLM 可为 None」的路由行为。"""
import threading

from shotrans.glossary import Glossary
from shotrans.translate.base import TranslateError
from shotrans.translate.router import NOTE_DOWN
from tests.router_fakes import Clock, echo, fail, make3, run, seg


# ---------- 三层逐层切换 ----------
def test_vllm_ok_lower_layers_untouched():
    r, v, m, t = make3(echo("V"), echo("M"), echo("T"))
    res, _ = run(r, [seg(1, "a")])
    assert (res[1].text, res[1].service) == ("Va", "vllm")
    assert m.calls == [] and t.calls == []


def test_vllm_and_fallback_fail_then_tencent():
    r, v, m, t = make3(fail("connect"), fail("http"), echo("T"))
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert {i: (x.text, x.service) for i, x in res.items()} == {1: ("Ta", "tencent"), 2: ("Tb", "tencent")}
    assert len(v.calls) == 1 and len(m.calls) == 1 and len(t.calls) == 1
    assert r.vllm_down()


def test_fallback_midstream_failure_rest_go_to_tencent():
    def half(svc, jobs, on):
        on(jobs[0].idx, "M" + jobs[0].text)
        raise TranslateError("stall")

    r, v, m, t = make3(fail("connect"), half, echo("T"))
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert (res[1].text, res[1].service) == ("Ma", "fallback")
    assert (res[2].text, res[2].service) == ("Tb", "tencent")
    assert [j.idx for j in t.calls[0]["jobs"]] == [2]  # 已完成的段不重发


def test_all_three_fail_shows_original():
    r, v, m, t = make3(fail("connect"), fail("connect"), fail("connect"))
    res, _ = run(r, [seg(1, "a")])
    assert (res[1].text, res[1].service, res[1].note) == ("a", "none", NOTE_DOWN)


def test_breaker_open_skips_vllm_goes_fallback_not_tencent():
    clock = Clock()
    r, v, m, t = make3(fail("connect"), echo("M"), echo("T"), clock=clock)
    run(r, [seg(1, "a")])
    clock.t = 10
    res, _ = run(r, [seg(2, "b")])
    assert (res[2].text, res[2].service) == ("Mb", "fallback")
    assert len(v.calls) == 1 and t.calls == []


def test_tencent_format_error_no_per_segment_retry():
    r, v, m, t = make3(None, None, fail("format"))
    res, _ = run(r, [seg(1, "a"), seg(2, "b")])
    assert len(t.calls) == 1  # 逐段重试只对 vLLM
    assert {x.service for x in res.values()} == {"none"}


def test_tencent_failure_does_not_trip_vllm_breaker():
    r, v, m, t = make3(fail("format"), None, fail("connect"))
    run(r, [seg(1, "a")])
    assert not r.vllm_down()  # vLLM 的 format 不熔断；腾讯连不上也不连累 vLLM


def test_cancel_during_fallback_failure_skips_tencent():
    cancel = threading.Event()

    def fb(svc, jobs, on):
        cancel.set()
        raise TranslateError("connect")

    r, v, m, t = make3(None, fb, echo("T"))
    res, out = run(r, [seg(1, "a")], cancel=cancel)
    assert t.calls == [] and out == []


# ---------- vLLM 为 None ----------
def test_vllm_none_chain_is_fallback_then_tencent():
    r, v, m, t = make3(None, fail("connect"), echo("T"))
    res, _ = run(r, [seg(1, "a")])
    assert res[1].service == "tencent"
    assert not r.vllm_down() and r.vllm_last_error_kind() is None


def test_vllm_none_fallback_ok():
    r, v, m, t = make3(None, echo("M"), echo("T"))
    res, _ = run(r, [seg(1, "a")])
    assert res[1].service == "fallback" and t.calls == []


def test_only_tencent():
    r, v, m, t = make3(None, None, echo("T"))
    res, _ = run(r, [seg(1, "a"), seg(2, "b", skip=True)])
    assert (res[1].text, res[1].service) == ("Ta", "tencent")
    assert 2 not in res  # 跳过的段不送
    assert [j.idx for j in t.calls[0]["jobs"]] == [1]


def test_nothing_configured_shows_original():
    r, v, m, t = make3(None, None, None)
    res, _ = run(r, [seg(1, "a")])
    assert (res[1].service, res[1].note) == ("none", NOTE_DOWN)
    assert r.retranslate_one(seg(1, "a"), "en2zh", "none") is None


def test_tencent_keep_terms_go_through_placeholders():
    g = Glossary(mappings=(("depression", "抑郁"),), keep=("PHQ-8",))
    r, v, m, t = make3(None, None, lambda svc, jobs, on: [on(j.idx, j.text.replace("score", "得分")) for j in jobs],
                       glossary=g)
    res, _ = run(r, [seg(1, "PHQ-8 score depression")])
    assert t.calls[0]["jobs"][0].text == "⟨1⟩ score depression"
    assert res[1].text == "PHQ-8 得分 depression"


# ---------- 重翻的选择规则 ----------
def _retr(r, current):
    res = r.retranslate_one(seg(5, "x"), "en2zh", current)
    return None if res is None else (res.text, res.service)


def test_retranslate_from_vllm_prefers_fallback():
    r, v, m, t = make3(echo("V"), echo("M"), echo("T"))
    assert _retr(r, "vllm") == ("Mx", "fallback")


def test_retranslate_from_vllm_without_fallback_goes_tencent():
    r, v, m, t = make3(echo("V"), None, echo("T"))
    assert _retr(r, "vllm") == ("Tx", "tencent")


def test_retranslate_from_fallback_prefers_vllm():
    r, v, m, t = make3(echo("V"), echo("M"), echo("T"))
    assert _retr(r, "fallback") == ("Vx", "vllm")


def test_retranslate_from_fallback_vllm_down_goes_tencent():
    r, v, m, t = make3(fail("connect"), echo("M"), echo("T"))
    run(r, [seg(1, "a")])  # 熔断
    assert _retr(r, "fallback") == ("Tx", "tencent")


def test_retranslate_from_tencent_prefers_vllm():
    r, v, m, t = make3(echo("V"), echo("M"), echo("T"))
    assert _retr(r, "tencent") == ("Vx", "vllm")


def test_retranslate_from_tencent_vllm_none_goes_fallback():
    r, v, m, t = make3(None, echo("M"), echo("T"))
    assert _retr(r, "tencent") == ("Mx", "fallback")


def test_retranslate_from_tencent_when_only_tencent_is_none():
    r, v, m, t = make3(None, None, echo("T"))
    assert _retr(r, "tencent") is None
    assert t.calls == []


def test_retranslate_from_none_vllm_down_no_fallback_goes_tencent():
    r, v, m, t = make3(fail("connect"), None, echo("T"))
    run(r, [seg(1, "a")])
    assert _retr(r, "none") == ("Tx", "tencent")


def test_retranslate_from_none_vllm_none_goes_fallback():
    r, v, m, t = make3(None, echo("M"), echo("T"))
    assert _retr(r, "none") == ("Mx", "fallback")


def test_retranslate_tencent_failure_returns_none_without_tripping():
    r, v, m, t = make3(echo("V"), None, fail("connect"))
    assert _retr(r, "vllm") is None
    assert not r.vllm_down()


# ---------- 缓存键按服务名区分 ----------
def test_tencent_result_cached_under_tencent_name():
    r, v, m, t = make3(None, None, echo("T"))
    run(r, [seg(1, "a")])
    assert [k[2] for k in r._cache] == ["tencent"]
    res, _ = run(r, [seg(2, "a")])
    assert (res[2].text, res[2].service) == ("Ta", "tencent")
    assert len(t.calls) == 1  # 第二次走缓存


def test_tencent_cache_not_reported_as_other_service():
    clock = Clock()
    r, v, m, t = make3(fail("connect"), None, echo("T"), clock=clock)
    run(r, [seg(1, "a")])  # vLLM 熔断，腾讯译成 Ta 写缓存
    v.script = echo("V")
    clock.t = 61           # 熔断结束
    r.retranslate_one(seg(1, "a"), "en2zh", "tencent")  # vLLM 译成 Va 写缓存
    keys = sorted(k[2] for k in r._cache)
    assert keys == ["tencent", "vllm"]
    res, _ = run(r, [seg(2, "a")])
    assert (res[2].text, res[2].service) == ("Va", "vllm")  # 链首 vLLM 的缓存优先

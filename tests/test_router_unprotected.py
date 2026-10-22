"""腾讯两次丢占位符 → 用不带占位符的原文再翻一次，照常显示译文、注明可能没保持原样、不进缓存。
LLM 层（vLLM、备用）仍然显示原文 + 「占位符丢失」。"""
import threading

from shotrans.glossary import Glossary
from shotrans.translate.base import TranslateError
from shotrans.translate.router import NOTE_LOST
from tests.router_fakes import make3, run, seg

UNPROT = "引用/术语可能未保持原样"


def drop_placeholders(prefix):
    """带 ⟨n⟩ 的请求一律把占位符弄丢；不带占位符的原文正常翻。"""
    def script(svc, jobs, on):
        for j in jobs:
            on(j.idx, "丢了" if "⟨" in j.text else prefix + j.text)
    return script


def test_tencent_lost_twice_retries_unprotected_and_shows_translation():
    r, v, m, t = make3(None, None, drop_placeholders("T"))
    res, _ = run(r, [seg(1, "see [12]")])
    assert (res[1].text, res[1].service, res[1].note) == ("Tsee [12]", "tencent", UNPROT)
    assert len(t.calls) == 3
    assert [j.text for j in t.calls[2]["jobs"]] == ["see [12]"]  # 第三次发的是原文，没有占位符


def test_tencent_unprotected_keeps_keep_terms_unprotected_too():
    r, v, m, t = make3(None, None, drop_placeholders("T"), glossary=Glossary(keep=("DAIC-WOZ",)))
    res, _ = run(r, [seg(1, "data from DAIC-WOZ")])
    assert "⟨" in t.calls[0]["jobs"][0].text  # 前两次是保护过的
    assert [j.text for j in t.calls[2]["jobs"]] == ["data from DAIC-WOZ"]
    assert res[1].note == UNPROT


def test_tencent_unprotected_result_not_cached():
    r, v, m, t = make3(None, None, drop_placeholders("T"))
    run(r, [seg(1, "see [12]")])
    res, _ = run(r, [seg(1, "see [12]")])
    assert len(t.calls) == 6  # 第二次照样去翻，没有读缓存
    assert res[1].note == UNPROT
    assert r._cache == {}


def test_vllm_lost_twice_still_shows_original():
    r, v, m, t = make3(drop_placeholders("V"), None, None)
    res, _ = run(r, [seg(1, "see [12]")])
    assert (res[1].text, res[1].service, res[1].note) == ("see [12]", "vllm", NOTE_LOST)
    assert len(v.calls) == 2


def test_fallback_lost_twice_still_shows_original():
    r, v, m, t = make3(None, drop_placeholders("M"), drop_placeholders("T"))
    res, _ = run(r, [seg(1, "see [12]")])
    assert (res[1].text, res[1].service, res[1].note) == ("see [12]", "fallback", NOTE_LOST)
    assert len(m.calls) == 2 and t.calls == []


def test_tencent_unprotected_failure_shows_original_lost():
    def script(svc, jobs, on):
        if len(svc.calls) == 3:
            raise TranslateError("connect")
        for j in jobs:
            on(j.idx, "丢了")

    r, v, m, t = make3(None, None, script)
    res, _ = run(r, [seg(1, "see [12]")])
    assert (res[1].text, res[1].service, res[1].note) == ("see [12]", "tencent", NOTE_LOST)


def test_tencent_unprotected_empty_shows_original_lost():
    def script(svc, jobs, on):
        if len(svc.calls) == 3:
            return  # 一段都没交
        for j in jobs:
            on(j.idx, "丢了")

    r, v, m, t = make3(None, None, script)
    res, _ = run(r, [seg(1, "see [12]")])
    assert (res[1].text, res[1].service, res[1].note) == ("see [12]", "tencent", NOTE_LOST)


def test_cancel_during_unprotected_retry_sends_nothing():
    cancel = threading.Event()

    def script(svc, jobs, on):
        if len(svc.calls) == 3:
            cancel.set()  # 用户在第三次请求进行中关了浮窗
            on(jobs[0].idx, "T" + jobs[0].text)
            return
        for j in jobs:
            on(j.idx, "丢了")

    r, v, m, t = make3(None, None, script)
    res, out = run(r, [seg(1, "see [12]")], cancel=cancel)
    assert out == []


def test_retranslate_to_tencent_lost_uses_unprotected():
    r, v, m, t = make3(None, drop_placeholders("M"), drop_placeholders("T"))
    out = r.retranslate_one(seg(1, "see [12]"), "en2zh", "fallback")
    assert (out.text, out.service, out.note) == ("Tsee [12]", "tencent", UNPROT)
    assert r._cache == {}


def test_retranslate_to_fallback_lost_still_none():
    r, v, m, t = make3(None, drop_placeholders("M"), drop_placeholders("T"))
    assert r.retranslate_one(seg(1, "see [12]"), "en2zh", "tencent") is None

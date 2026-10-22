"""vLLM key 无效（auth）：照样切备用、照样熔断，但要记下熔断原因，界面才能说「key 无效」而不是「掉线」。"""
from shotrans.translate.base import TranslateError
from tests.router_fakes import Clock, fail, make, make_no_fb, run, seg


def test_auth_failure_fails_over_to_fallback():
    r, v, m = make(fail("auth"))
    by_idx, _ = run(r, [seg(1, "a")])
    assert by_idx[1].service == "fallback" and by_idx[1].text == "Ma"


def test_auth_failure_trips_breaker_and_records_kind():
    r, v, m = make(fail("auth"))
    run(r, [seg(1, "a")])
    assert r.vllm_down()
    assert r.vllm_last_error_kind() == "auth"


def test_auth_breaker_skips_vllm_next_time():
    r, v, m = make(fail("auth"))
    run(r, [seg(1, "a")])
    run(r, [seg(2, "b")])
    assert len(v.calls) == 1  # 第二张直接走备用，不再撞 401


def test_connect_failure_records_connect_kind():
    r, v, m = make(fail("connect"))
    run(r, [seg(1, "a")])
    assert r.vllm_last_error_kind() == "connect"


def test_last_error_kind_none_before_any_failure():
    r, v, m = make(fail("auth"))
    assert r.vllm_last_error_kind() is None


def test_last_error_kind_none_after_breaker_expires():
    clock = Clock()
    r, v, m = make(fail("auth"), clock=clock)
    run(r, [seg(1, "a")])
    clock.t = 61
    assert r.vllm_last_error_kind() is None


def test_auth_without_fallback_shows_original_and_records_kind():
    r, v = make_no_fb(fail("auth"))
    by_idx, _ = run(r, [seg(1, "a")])
    assert by_idx[1].service == "none"
    assert r.vllm_last_error_kind() == "auth"


def test_auth_during_per_segment_retry_records_kind():
    """先没按格式输出（转成一段一请求），再撞上 401。"""
    calls = []

    def script(svc, jobs, on):
        calls.append(1)
        raise TranslateError("format" if len(calls) == 1 else "auth")

    r, v, m = make(script)
    run(r, [seg(1, "a"), seg(2, "b")])
    assert r.vllm_down()
    assert r.vllm_last_error_kind() == "auth"


def test_retranslate_auth_trips_breaker_and_records_kind():
    r, v, m = make(fail("auth"))
    assert r.retranslate_one(seg(1, "a"), "en2zh", "fallback") is None
    assert r.vllm_down()
    assert r.vllm_last_error_kind() == "auth"

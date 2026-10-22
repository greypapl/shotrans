"""Task 18：大模型层分块并发的守卫测试。每个守卫有自己的数据，期望值写死（不读 Router 的默认值）。"""
import threading
import time

import pytest

from shotrans.glossary import Glossary
from shotrans.translate.base import TranslateError
from shotrans.translate.router import Router
from tests.router_fakes import Clock, FakeService, echo, run, seg


def mk(v_script=None, fb_script=None, tc_script=None, **kw):
    """默认不让第一块先走一步（head_start=0），各并发测试只测自己的守卫；先走一步有专门的测试。"""
    kw.setdefault("head_start", 0.0)
    v = FakeService("vllm", v_script) if v_script else None
    m = FakeService("fallback", fb_script) if fb_script else None
    t = FakeService("tencent", tc_script) if tc_script else None
    return Router(v, m, Glossary(), breaker_seconds=60, clock=Clock(), tencent=t, **kw), v, m, t


def segs(n, width=10, start=1):
    """n 段，每段 width 个字母（不含会被保护的字符）。"""
    return [seg(i, chr(ord("a") + (i - 1) % 26) * width) for i in range(start, start + n)]


def job_sets(svc):
    return [sorted(j.idx for j in c["jobs"]) for c in svc.calls]


def test_router_defaults_backward_compatible():
    """老的构造方式（不传分块参数）照样能用，默认值写死在这里。"""
    r = Router(None, None, Glossary())
    assert (r.chunk_chars, r.chunk_max_segments, r.max_concurrency, r.head_start) == (600, 8, 4, 0.5)


# ---------- 切块 ----------
def test_split_by_chars():
    """chunk_chars=25、每段 10 字：两段一块（20），第三段会到 30 > 25，另起一块。"""
    r, v, *_ = mk(echo("V"), chunk_chars=25, chunk_max_segments=99, max_concurrency=1)
    run(r, segs(5))
    assert job_sets(v) == [[1, 2], [3, 4], [5]]


def test_split_by_max_segments():
    """字数远没到上限，但每块至多 3 段。"""
    r, v, *_ = mk(echo("V"), chunk_chars=10_000, chunk_max_segments=3, max_concurrency=1)
    run(r, segs(7, width=2))
    assert job_sets(v) == [[1, 2, 3], [4, 5, 6], [7]]


def test_long_segment_gets_its_own_chunk_and_no_empty_chunk():
    """开头就是超长段也不会切出空块；中间的超长段同样独占。"""
    r, v, *_ = mk(echo("V"), chunk_chars=30, chunk_max_segments=99, max_concurrency=1)
    run(r, [seg(1, "a" * 80), seg(2, "b" * 5), seg(3, "c" * 80), seg(4, "d" * 5)])
    assert job_sets(v) == [[1], [2], [3], [4]]


def test_few_short_segments_stay_one_request():
    r, v, *_ = mk(echo("V"), chunk_chars=600, chunk_max_segments=8, max_concurrency=4)
    res, _ = run(r, segs(3))
    assert job_sets(v) == [[1, 2, 3]]
    assert {i: x.service for i, x in res.items()} == {1: "vllm", 2: "vllm", 3: "vllm"}


# ---------- 并发 ----------
def test_chunks_really_run_concurrently():
    """4 块各自在 barrier 上等另外 3 块：顺序执行的话 barrier 超时、整层失败。"""
    barrier = threading.Barrier(4, timeout=2)

    def script(svc, jobs, on):
        barrier.wait()
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, v, m, _ = mk(script, echo("M"), chunk_chars=10, max_concurrency=4)
    res, _ = run(r, segs(4))
    assert {x.service for x in res.values()} == {"vllm"}
    assert m.calls == []


def test_max_concurrency_caps_in_flight():
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def script(svc, jobs, on):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, v, *_ = mk(script, chunk_chars=10, max_concurrency=2)
    res, _ = run(r, segs(6))
    assert len(res) == 6 and len(v.calls) == 6
    assert state["peak"] == 2


def test_on_result_only_called_on_caller_thread():
    seen = []

    def script(svc, jobs, on):
        time.sleep(0.01)
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, *_ = mk(script, chunk_chars=10, max_concurrency=3)
    r.translate(segs(5), "en2zh", lambda res: seen.append((res.idx, threading.get_ident())))
    assert sorted(i for i, _ in seen) == [1, 2, 3, 4, 5]
    assert {t for _, t in seen} == {threading.get_ident()}


def test_chunks_dispatched_in_reading_order():
    """并发 2、4 块：最先发出的两个请求是第 1、2 块（首段不因分块变慢）。"""
    order = []
    lock = threading.Lock()

    def script(svc, jobs, on):
        with lock:
            order.append(jobs[0].idx)
        time.sleep(0.05)
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, *_ = mk(script, chunk_chars=10, max_concurrency=2)
    run(r, segs(4))
    assert sorted(order[:2]) == [1, 2]


def test_max_concurrency_one_runs_on_caller_thread():
    threads = set()

    def script(svc, jobs, on):
        threads.add(threading.get_ident())
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, v, *_ = mk(script, chunk_chars=10, max_concurrency=1)
    run(r, segs(3))
    assert len(v.calls) == 3
    assert threads == {threading.get_ident()}


# ---------- 第一块先走一步 ----------
def test_other_chunks_wait_for_first_segment():
    """head_start 很长：其余块要等第一块交出首段才发；第一块自己不等。"""
    stamps = {}
    lock = threading.Lock()

    def script(svc, jobs, on):
        with lock:
            stamps.setdefault("start", {})[jobs[0].idx] = time.monotonic()
        if jobs[0].idx == 1:
            time.sleep(0.2)
            stamps["first_out"] = time.monotonic()
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, *_ = mk(script, chunk_chars=10, max_concurrency=3, head_start=5.0)
    t0 = time.monotonic()
    res, _ = run(r, segs(3))
    assert len(res) == 3
    assert time.monotonic() - t0 < 2.0
    assert min(stamps["start"][2], stamps["start"][3]) >= stamps["first_out"]


def test_head_start_is_capped():
    """第一块迟迟不出字：其余块最多等 head_start 秒就发。"""
    stamps = {}

    def script(svc, jobs, on):
        stamps[jobs[0].idx] = time.monotonic()
        if jobs[0].idx == 1:
            time.sleep(0.8)
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, *_ = mk(script, chunk_chars=10, max_concurrency=2, head_start=0.2)
    t0 = time.monotonic()
    run(r, segs(2))
    assert stamps[2] - t0 < 0.6


def test_head_start_released_when_first_chunk_fails():
    """第一块没交出任何段就失败（备用 format）：其余工作线程立即放行，块 3 不用排在慢块 2 后面。"""
    stamps = {}

    def fb_script(svc, jobs, on):
        stamps[jobs[0].idx] = time.monotonic()
        if jobs[0].idx == 1:
            raise TranslateError("format")
        if jobs[0].idx == 2:
            time.sleep(0.6)
        for j in jobs:
            on(j.idx, "M" + j.text)

    r, *_ = mk(None, fb_script, echo("T"), chunk_chars=10, max_concurrency=3, head_start=3.0)
    t0 = time.monotonic()
    res, _ = run(r, segs(3))
    assert stamps[3] - t0 < 0.4
    assert {i: x.service for i, x in res.items()} == {1: "tencent", 2: "fallback", 3: "fallback"}


# ---------- 失败与切换 ----------
def test_failed_chunk_rest_goes_to_next_layer_after_all_chunks_end():
    """块 A（1、2）交了 1 后断流；块 B（3、4）慢但成功。2 要等 B 结束后才交给备用。"""
    stamps = {}
    b_started = threading.Event()

    def v_script(svc, jobs, on):
        if jobs[0].idx == 1:
            on(1, "V1")
            b_started.wait(2)  # 等块 B 已经发出再失败
            raise TranslateError("stall", done={1})
        b_started.set()
        time.sleep(0.2)
        for j in jobs:
            on(j.idx, "V" + j.text)
        stamps["b_end"] = time.monotonic()

    def fb_script(svc, jobs, on):
        stamps["fb_start"] = time.monotonic()
        for j in jobs:
            on(j.idx, "M" + j.text)

    r, v, m, _ = mk(v_script, fb_script, chunk_chars=20, max_concurrency=2)
    res, out = run(r, segs(4))
    assert sorted(r_.idx for r_ in out) == [1, 2, 3, 4]  # 每段恰好一次
    assert {i: x.service for i, x in res.items()} == {1: "vllm", 2: "fallback", 3: "vllm", 4: "vllm"}
    assert job_sets(m) == [[2]]
    assert stamps["fb_start"] >= stamps["b_end"]
    assert r.vllm_down()


def test_failed_chunk_stops_new_chunks_of_same_service():
    """并发 2、3 块：块 1 立刻连不上，块 2 慢；块 3 不该再发给 vLLM，和块 1 一起交给备用。"""
    two_started = threading.Event()

    def v_script(svc, jobs, on):
        if jobs[0].idx == 1:
            two_started.wait(2)  # 块 2 已在途时块 1 才失败
            raise TranslateError("connect")
        two_started.set()
        time.sleep(0.2)
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, v, m, _ = mk(v_script, echo("M"), chunk_chars=10, max_concurrency=2)
    res, out = run(r, segs(3))
    assert sorted(job_sets(v)) == [[1], [2]]
    assert {i: x.service for i, x in res.items()} == {1: "fallback", 2: "vllm", 3: "fallback"}
    assert len(out) == 3


def test_sequential_failed_chunk_stops_new_chunks():
    """max_concurrency=1 时同样：块 1 失败，块 2、3 不再发给 vLLM。"""
    def v_script(svc, jobs, on):
        raise TranslateError("first_token")

    r, v, m, _ = mk(v_script, echo("M"), chunk_chars=10, max_concurrency=1)
    res, _ = run(r, segs(3))
    assert job_sets(v) == [[1]]
    assert {x.service for x in res.values()} == {"fallback"}


def test_any_chunk_connectivity_failure_trips_breaker():
    """不是第一块、而是最后一块连不上，照样熔断。"""
    def v_script(svc, jobs, on):
        if jobs[0].idx == 3:
            raise TranslateError("http")
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, v, m, _ = mk(v_script, echo("M"), chunk_chars=10, max_concurrency=3)
    res, _ = run(r, segs(3))
    assert r.vllm_down() and r.vllm_last_error_kind() == "http"
    assert res[3].service == "fallback"


def test_vllm_format_error_retries_only_that_chunk_per_segment():
    """块 A（1、2）没按格式输出 → 只有 1、2 逐段重试；块 B（3、4）照常；备用不动。"""
    def v_script(svc, jobs, on):
        ids = [j.idx for j in jobs]
        if ids == [1, 2]:
            raise TranslateError("format")
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, v, m, _ = mk(v_script, echo("M"), chunk_chars=20, max_concurrency=2)
    res, out = run(r, segs(4))
    assert {x.service for x in res.values()} == {"vllm"} and len(out) == 4
    assert sorted(job_sets(v)) == [[1], [1, 2], [2], [3, 4]]
    assert m.calls == [] and not r.vllm_down()


def test_format_retry_skips_segments_already_delivered():
    """块 [1,2] 交了 1 之后才报 format：逐段重试只重试 2，1 不会回调两次。"""
    def v_script(svc, jobs, on):
        if len(jobs) > 1:
            on(jobs[0].idx, "V" + jobs[0].text)
            raise TranslateError("format", done={jobs[0].idx})
        on(jobs[0].idx, "V" + jobs[0].text)

    r, v, *_ = mk(v_script, echo("M"), chunk_chars=20, max_concurrency=2)
    res, out = run(r, segs(4))
    assert sorted(x.idx for x in out) == [1, 2, 3, 4]
    assert sorted(job_sets(v)) == [[1, 2], [2], [3, 4], [4]]


def test_per_segment_connectivity_failure_stops_new_chunks():
    """块 A 先 format，逐段重试时又连不上 → 熔断、不再发新块（块 C 交给备用）。"""
    b_started = threading.Event()

    def v_script(svc, jobs, on):
        ids = [j.idx for j in jobs]
        if ids == [1, 2]:
            b_started.wait(2)
            raise TranslateError("format")
        if ids == [1]:
            raise TranslateError("connect")
        b_started.set()
        time.sleep(0.2)
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, v, m, _ = mk(v_script, echo("M"), chunk_chars=20, max_concurrency=2)
    res, out = run(r, segs(6))
    assert sorted(job_sets(v)) == [[1], [1, 2], [3, 4]]
    assert {i: x.service for i, x in res.items()} == {1: "fallback", 2: "fallback", 3: "vllm", 4: "vllm",
                                                      5: "fallback", 6: "fallback"}
    assert len(out) == 6 and r.vllm_down()


def test_fallback_format_error_does_not_stop_other_chunks():
    """备用某块 format 错：只这一块交给腾讯，其余块照样发给备用。"""
    def fb_script(svc, jobs, on):
        if jobs[0].idx == 1:
            raise TranslateError("format")
        time.sleep(0.05)
        for j in jobs:
            on(j.idx, "M" + j.text)

    r, _, m, t = mk(None, fb_script, echo("T"), chunk_chars=10, max_concurrency=2)
    res, _ = run(r, segs(3))
    assert sorted(job_sets(m)) == [[1], [2], [3]]
    assert {i: x.service for i, x in res.items()} == {1: "tencent", 2: "fallback", 3: "fallback"}


def test_fallback_layer_is_chunked():
    r, _, m, _ = mk(None, echo("M"), chunk_chars=10, max_concurrency=2)
    run(r, segs(3))
    assert sorted(job_sets(m)) == [[1], [2], [3]]


def test_tencent_layer_not_chunked():
    r, *_, t = mk(None, None, echo("T"), chunk_chars=10, chunk_max_segments=1, max_concurrency=4)
    res, _ = run(r, segs(5))
    assert job_sets(t) == [[1, 2, 3, 4, 5]]
    assert {x.service for x in res.values()} == {"tencent"}


def test_worker_crash_is_raised_on_caller_thread():
    def v_script(svc, jobs, on):
        if jobs[0].idx == 2:
            raise RuntimeError("块里炸了")
        for j in jobs:
            on(j.idx, "V" + j.text)

    r, *_ = mk(v_script, chunk_chars=10, max_concurrency=2)
    with pytest.raises(RuntimeError, match="块里炸了"):
        run(r, segs(2))


# ---------- 取消 ----------
def test_cancel_stops_new_chunks_and_callbacks():
    """并发 2、4 块：块 1 交一段后取消；块 2 等取消后才交。之后不再回调，块 3、4 不再发出。"""
    cancel = threading.Event()
    after_cancel = threading.Event()
    calls_after = []

    def v_script(svc, jobs, on):
        if jobs[0].idx == 1:
            on(1, "V1")
            time.sleep(0.15)  # 让 V1 先被调用者线程取走
            cancel.set()
            after_cancel.set()
            return
        after_cancel.wait(2)
        for j in jobs:
            on(j.idx, "V" + j.text)

    def on_result(res):
        if cancel.is_set():
            calls_after.append(res.idx)

    r, v, *_ = mk(v_script, echo("M"), chunk_chars=10, max_concurrency=2)
    r.translate(segs(4), "en2zh", on_result, cancel)
    time.sleep(0.3)  # 让工作线程把手头的块收尾
    assert calls_after == []
    assert sorted(job_sets(v)) == [[1], [2]]


def test_cancel_returns_without_waiting_for_slow_chunk():
    """两块都不理会取消、迟迟不结束（队列里什么也不来）：调用者照样马上返回。"""
    cancel = threading.Event()
    slow_started = threading.Event()

    def v_script(svc, jobs, on):
        if jobs[0].idx == 1:
            slow_started.wait(2)
            cancel.set()
        else:
            slow_started.set()
        time.sleep(1.5)  # 不理会取消的慢块

    r, *_ = mk(v_script, chunk_chars=10, max_concurrency=2)
    t0 = time.monotonic()
    r.translate(segs(2), "en2zh", lambda res: None, cancel)
    assert time.monotonic() - t0 < 0.8

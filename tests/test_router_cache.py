"""译文缓存是 LRU，有上限：托盘程序常驻，不能无限涨。"""
from tests.router_fakes import echo, make, run, seg


def _translated_texts(v):
    return [j.text for c in v.calls for j in c["jobs"]]


def test_default_cap_is_2000():
    r, v, m = make(echo("V"))
    assert r.cache_max == 2000


def test_cache_never_exceeds_cap():
    r, v, m = make(echo("V"))
    r.cache_max = 3
    for i in range(10):
        run(r, [seg(1, f"t{i}")])
    assert len(r._cache) == 3


def test_oldest_entry_evicted_first():
    r, v, m = make(echo("V"))
    r.cache_max = 2
    for t in ("a", "b", "c"):
        run(r, [seg(1, t)])
    v.calls.clear()
    run(r, [seg(1, "c")])
    run(r, [seg(1, "b")])
    assert v.calls == []  # b、c 还在缓存里
    run(r, [seg(1, "a")])
    assert _translated_texts(v) == ["a"]  # a 最老，已被挤掉，要重新请求


def test_recently_used_entry_survives_eviction():
    r, v, m = make(echo("V"))
    r.cache_max = 2
    run(r, [seg(1, "a")])
    run(r, [seg(1, "b")])
    run(r, [seg(1, "a")])  # 命中，a 变成「刚用过」
    run(r, [seg(1, "c")])  # 挤掉的应是 b
    v.calls.clear()
    run(r, [seg(1, "a")])
    assert v.calls == []
    run(r, [seg(1, "b")])
    assert _translated_texts(v) == ["b"]


def test_retranslate_result_also_capped():
    r, v, m = make(echo("V"))
    r.cache_max = 1
    run(r, [seg(1, "a")])
    r.retranslate_one(seg(1, "b"), "en2zh", "fallback")
    assert len(r._cache) == 1

"""Task 18：用本机假 vLLM 服务器跑真实的 VllmClient，覆盖「并发的几块里有快有慢、有一块断流」的组合。"""
import re
import threading
import time

from shotrans.glossary import Glossary
from shotrans.translate.router import Router
from shotrans.translate.vllm import VllmClient
from tests.fakes import send_delta, send_done, start_sse
from tests.router_fakes import Clock, seg

_IDS = re.compile(r"⟦(\d+)⟧ (\S+)")


def _client(url, name):
    return VllmClient(url, "k", connect_timeout=0.5, first_token_timeout=0.8, stall_timeout=0.4,
                      name=name, model="fake-model")


def test_concurrent_chunks_mixed_speed_and_stall(make_server):
    spans = []  # (首段编号, 开始, 结束)
    lock = threading.Lock()

    def vllm_handler(h, payload):
        pairs = _IDS.findall(payload["messages"][1]["content"])
        first = int(pairs[0][0])
        t0 = time.monotonic()
        start_sse(h)
        if first == 3:  # 交出 3，4 写到一半就不动了 → 断流
            send_delta(h, f"⟦3⟧ 译{pairs[0][1]}\n⟦4⟧ 半")
            time.sleep(1.0)
            return
        delay = 0.15 if first == 1 else 0.0  # 第一块慢
        for idx, text in pairs:
            send_delta(h, f"⟦{idx}⟧ 译{text}\n")
            time.sleep(delay)
        send_done(h)
        with lock:
            spans.append((first, t0, time.monotonic()))

    def fb_handler(h, payload):
        pairs = _IDS.findall(payload["messages"][1]["content"])
        start_sse(h)
        for idx, text in pairs:
            send_delta(h, f"⟦{idx}⟧ 备{text}\n")
        send_done(h)

    vs, fs = make_server(vllm_handler), make_server(fb_handler)
    r = Router(_client(vs.url, "vllm"), _client(fs.url, "fallback"), Glossary(),
               breaker_seconds=60, clock=Clock(), chunk_chars=20, chunk_max_segments=8, max_concurrency=3)
    out, threads = [], set()

    def on(res):
        out.append(res)
        threads.add(threading.get_ident())

    r.translate([seg(i, chr(ord("a") + i) * 10) for i in range(1, 7)], "en2zh", on)

    assert sorted(x.idx for x in out) == [1, 2, 3, 4, 5, 6]  # 每段恰好一次
    got = {x.idx: (x.service, x.text) for x in out}
    assert got[4] == ("fallback", "备" + "e" * 10)
    assert {i: got[i][0] for i in (1, 2, 3, 5, 6)} == {i: "vllm" for i in (1, 2, 3, 5, 6)}
    assert got[3][1] == "译" + "d" * 10
    assert threads == {threading.get_ident()}
    assert r.vllm_down() and r.vllm_last_error_kind() == "stall"
    assert len(vs.requests) == 3 and len(fs.requests) == 1
    assert _IDS.findall(fs.requests[0]["json"]["messages"][1]["content"]) == [("4", "e" * 10)]
    # 第一块（慢）和第三块（快）的服务端处理时间重叠：确实并发
    (s1, e1), (s5, e5) = [(s, e) for f, s, e in sorted(spans) if f in (1, 5)]
    assert s5 < e1

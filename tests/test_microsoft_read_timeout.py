"""微软翻译读超时随这批文字长度放宽：8 秒 + 每 2000 字符 1 秒，最多 30 秒。"""
import time

from shotrans.translate.base import Job
from shotrans.translate.microsoft import MicrosoftClient
from tests.fakes import edge_map


def test_read_timeout_base_for_empty():
    assert MicrosoftClient().read_timeout_for([]) == 8.0


def test_read_timeout_one_second_per_2000_chars():
    assert MicrosoftClient().read_timeout_for(["x" * 2000]) == 9.0


def test_read_timeout_sums_whole_batch():
    assert MicrosoftClient().read_timeout_for(["x" * 4000, "y" * 6000]) == 13.0


def test_read_timeout_capped_at_30():
    assert MicrosoftClient().read_timeout_for(["x" * 80000]) == 30.0


def test_request_uses_scaled_read_timeout(make_edge, monkeypatch):
    srv = make_edge(edge_map(lambda t: "好"))
    c = MicrosoftClient(timeout_connect=0.5, timeout_read=8.0, url=srv.url)
    http = c._http()
    seen = []
    real_post = http.post

    def post(*a, **k):
        seen.append(k.get("timeout"))
        return real_post(*a, **k)

    monkeypatch.setattr(http, "post", post)
    c.translate_stream([Job(1, "x" * 2000), Job(2, "y" * 2000)], "en2zh", [], lambda i, t: None)
    assert seen[0].read == 10.0
    assert seen[0].connect == 0.5


def test_long_batch_survives_slow_server(make_edge):
    def slow(h, payload):
        time.sleep(0.6)
        edge_map(lambda t: "好")(h, payload)

    srv = make_edge(slow)
    # 基础读超时 0.2 秒；4000 字符再加 2 秒 → 2.2 秒，等得到 0.6 秒才回的服务器
    c = MicrosoftClient(timeout_connect=0.5, timeout_read=0.2, url=srv.url)
    got = []
    c.translate_stream([Job(1, "x" * 4000)], "en2zh", [], lambda i, t: got.append(t))
    assert got == ["好"]

"""腾讯读超时随这批文字长度放宽：8 秒 + 每 500 字符 1 秒，最多 30 秒。"""
import time

from shotrans.translate.base import Job
from shotrans.translate.tencent import TencentClient
from tests.fakes import transmart_map


def test_read_timeout_base_for_empty():
    assert TencentClient().read_timeout_for([]) == 8.0


def test_read_timeout_one_second_per_500_chars():
    assert TencentClient().read_timeout_for(["x" * 500]) == 9.0


def test_read_timeout_sums_whole_batch():
    assert TencentClient().read_timeout_for(["x" * 1000, "y" * 1500]) == 13.0


def test_read_timeout_capped_at_30():
    assert TencentClient().read_timeout_for(["x" * 20000]) == 30.0


def test_request_uses_scaled_read_timeout(make_transmart, monkeypatch):
    srv = make_transmart(transmart_map(lambda t: "好"))
    c = TencentClient(timeout_connect=0.5, timeout_read=8.0, url=srv.url)
    http = c._http()
    seen = []
    real_post = http.post

    def post(*a, **k):
        seen.append(k.get("timeout"))
        return real_post(*a, **k)

    monkeypatch.setattr(http, "post", post)
    c.translate_stream([Job(1, "x" * 500), Job(2, "y" * 500)], "en2zh", [], lambda i, t: None)
    assert seen[0].read == 10.0
    assert seen[0].connect == 0.5


def test_long_batch_survives_slow_server(make_transmart):
    def slow(h, payload):
        time.sleep(0.6)
        transmart_map(lambda t: "好")(h, payload)

    srv = make_transmart(slow)
    # 基础读超时 0.2 秒；1000 字符再加 2 秒 → 2.2 秒，等得到 0.6 秒才回的服务器
    c = TencentClient(timeout_connect=0.5, timeout_read=0.2, url=srv.url)
    got = []
    c.translate_stream([Job(1, "x" * 1000)], "en2zh", [], lambda i, t: got.append(t))
    assert got == ["好"]

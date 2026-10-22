"""内网/本机的 vLLM 绕过系统代理（本机设了 HTTP_PROXY 时，内网请求被送进代理 → /models 502）。"""
import pytest

from shotrans.translate.base import Job
from shotrans.translate.vllm import VllmClient, is_local_host
from tests.fakes import closed_port_url, sse_chunks


@pytest.fixture
def dead_proxy(monkeypatch):
    """所有代理变量都指向本机一个没人监听的端口；去掉 NO_PROXY，免得开发机自己的设置替我们绕过。"""
    dead = closed_port_url().replace("/v1", "")
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(k, dead)
    monkeypatch.delenv("NO_PROXY", raising=False)
    return dead


def test_models_on_loopback_bypasses_dead_proxy(dead_proxy, make_server):
    srv = make_server(sse_chunks(["⟦1⟧ 好"]))
    c = VllmClient(srv.url, "k", connect_timeout=0.5, first_token_timeout=1.0, stall_timeout=1.0)
    assert c.model_name() == "fake-model"
    assert srv.models_hits == 1


def test_chat_on_loopback_bypasses_dead_proxy(dead_proxy, make_server):
    srv = make_server(sse_chunks(["⟦1⟧ 你好"]))
    # 模型名直接给：只走 /chat/completions 这条路，不经过 /models
    c = VllmClient(srv.url, "k", connect_timeout=0.5, first_token_timeout=1.0, stall_timeout=1.0, model="m")
    got = []
    c.translate_stream([Job(1, "Hello")], "en2zh", [], lambda i, t: got.append((i, t)))
    assert got == [(1, "你好")]
    assert srv.models_hits == 0


def test_public_hostname_keeps_env_proxies():
    assert VllmClient("https://api.example.com/v1", "k").trust_env is True


def test_loopback_ip_does_not_trust_env():
    assert VllmClient("http://127.0.0.1:8000/v1", "k").trust_env is False


@pytest.mark.parametrize("url", [
    "http://localhost:8000/v1",
    "http://LOCALHOST/v1",
    "http://127.0.0.1:8000/v1",
    "http://172.25.0.1:8004/v1",
    "http://10.0.0.5/v1",
    "http://192.168.1.2/v1",
    "http://169.254.1.1/v1",
    "http://[::1]:8000/v1",
    "http://[fe80::1]/v1",
    "http://localhost./v1",
    "http://100.64.0.1/v1",
    "http://100.127.255.254/v1",
])
def test_local_hosts(url):
    assert is_local_host(url) is True


@pytest.mark.parametrize("url", [
    "https://api.example.com/v1",
    "https://8.8.8.8/v1",
    "https://localhost.example.com/v1",
    "http://172.32.0.1/v1",  # 172.16/12 之外
    "http://100.128.0.1/v1",  # 100.64/10 之外
    "http://100.63.255.255/v1",
    "",
])
def test_public_hosts(url):
    assert is_local_host(url) is False

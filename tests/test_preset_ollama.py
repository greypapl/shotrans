"""「Ollama（本机）」预设：本机地址、不要 key、关思考用 reasoning_effort = none（同时照发 temperature 和 max_tokens，
不像 OpenAI 推理模型那样改发 max_completion_tokens）；按钮是「下载 Ollama」。"""
import pytest

from shotrans import i18n
from shotrans.config import ServiceConfig, validate, Config
from shotrans.services import apply_preset
from shotrans.translate.base import Job
from shotrans.translate.vllm import is_local_host, sampling_params
from shotrans.ui.service_form import ServiceForm
from tests.fakes import FakeVllm, sse_chunks


def test_preset_values():
    s = apply_preset(ServiceConfig(), "ollama")
    assert (s.base_url, s.model, s.thinking_off, s.thinking_style) == (
        "http://localhost:11434/v1", "qwen3:8b", True, "ollama")
    assert s.configured and not s.api_key  # 不要 key 也算配置好了
    assert validate(Config(primary=s)) == []
    assert is_local_host(s.base_url)  # 本机：不走系统代理


def test_sampling_keeps_temperature_and_max_tokens():
    assert sampling_params("ollama", 200) == {"temperature": 0.2, "max_tokens": 200}


@pytest.fixture
def ollama(monkeypatch):
    servers = []

    def _make():
        srv = FakeVllm(sse_chunks(["好"]))
        servers.append(srv)
        return srv
    yield _make
    for s in servers:
        s.close()


def test_request_turns_thinking_off_with_reasoning_effort(ollama):
    from shotrans.translate.vllm import VllmClient

    srv = ollama()
    c = VllmClient(srv.base + "/v1", "", 1.0, 3.0, 3.0, "", model="qwen3:8b", thinking_off=True,
                   thinking_style="ollama")
    c.translate_stream([Job(1, "Hello")], "en2zh", [], lambda i, t: None)
    body = srv.requests[0]["json"]
    assert body["reasoning_effort"] == "none"
    assert "temperature" in body and "max_completion_tokens" not in body
    assert srv.requests[0]["auth"] is None  # 没填 key：不发 Authorization


def test_form_button_says_download_and_empty_key_ok(qtbot, track):
    f = ServiceForm(False, tester=lambda svc: None, lister=lambda u, k: None, open_url=lambda u: None)
    track(f)
    f.preset.setCurrentIndex(f.preset.findData("ollama"))
    assert f.signup.text() == "下载 Ollama" and f.validate() == []
    f.preset.setCurrentIndex(f.preset.findData("zhipu"))
    assert f.signup.text() == "去注册 / 获取 key"
    f.preset.setCurrentIndex(f.preset.findData("ollama"))
    i18n.set_language("en")
    assert f.signup.text() == "Download Ollama"

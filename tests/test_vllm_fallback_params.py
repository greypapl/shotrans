"""VllmClient 为「备用接口」新增的三个参数：name / model / thinking_off。每个参数各用独立的服务器和数据。"""
from shotrans.translate.base import Job
from shotrans.translate.vllm import VllmClient
from tests.fakes import sse_chunks


def _run(client):
    got = []
    client.translate_stream([Job(1, "a")], "en2zh", [], lambda i, t: got.append((i, t)))
    return got


# ---------- name ----------

def test_name_default_is_vllm():
    assert VllmClient("http://x/v1", "").name == "vllm"


def test_name_override_is_instance_attribute():
    fb = VllmClient("http://x/v1", "", name="fallback")
    other = VllmClient("http://x/v1", "")
    assert fb.name == "fallback"
    assert other.name == "vllm"  # 不污染类属性和别的实例


# ---------- model ----------

def test_explicit_model_used_without_asking_models(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 一"]))
    c = VllmClient(s.url, "k", 0.5, 0.4, 0.4, model="deepseek-chat")
    assert _run(c) == [(1, "一")]
    assert s.requests[0]["json"]["model"] == "deepseek-chat"
    assert s.models_hits == 0


def test_empty_model_asks_models_once(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 一"]))
    c = VllmClient(s.url, "k", 0.5, 0.4, 0.4, model="")
    _run(c)
    assert s.requests[0]["json"]["model"] == "fake-model"
    assert s.models_hits == 1


# ---------- thinking_off ----------

def test_thinking_off_false_omits_chat_template_kwargs(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 一"]))
    c = VllmClient(s.url, "k", 0.5, 0.4, 0.4, thinking_off=False)
    assert _run(c) == [(1, "一")]
    assert "chat_template_kwargs" not in s.requests[0]["json"]
    assert s.requests[0]["json"]["stream"] is True  # 其余字段不受影响


def test_thinking_off_true_sends_enable_thinking_false(make_server):
    s = make_server(sse_chunks(["⟦1⟧ 一"]))
    c = VllmClient(s.url, "k", 0.5, 0.4, 0.4, thinking_off=True)
    _run(c)
    assert s.requests[0]["json"]["chat_template_kwargs"] == {"enable_thinking": False}

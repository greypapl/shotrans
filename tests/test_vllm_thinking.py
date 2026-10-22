"""「关闭思考」只发配置的那一种字段。"""
import pytest

from shotrans.translate.base import Job
from shotrans.translate.vllm import VllmClient
from tests.fakes import sse_chunks

THINK_KEYS = ("chat_template_kwargs", "enable_thinking", "thinking", "reasoning_effort")


def _send(make_server, **kw):
    srv = make_server(sse_chunks(["⟦1⟧你好"]))
    c = VllmClient(srv.url, "k", 2.0, 5.0, 5.0, model="m", **kw)
    out = []
    c.translate_stream([Job(1, "Hi")], "en2zh", [], lambda i, t: out.append(t))
    assert out == ["你好"]
    return srv.requests[0]["json"]


@pytest.mark.parametrize("style,expected", [
    ("chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": False}}),
    ("enable_thinking", {"enable_thinking": False}),
    ("deepseek", {"thinking": {"type": "disabled"}}),
    ("openai_reasoning", {"reasoning_effort": "none"}),
])
def test_thinking_off_sends_only_its_field(make_server, style, expected):
    body = _send(make_server, thinking_off=True, thinking_style=style)
    assert {k: body[k] for k in THINK_KEYS if k in body} == expected


def test_thinking_on_sends_nothing(make_server):
    body = _send(make_server, thinking_off=False, thinking_style="deepseek")
    assert not any(k in body for k in THINK_KEYS)


def test_default_is_vllm_style(make_server):
    body = _send(make_server)
    assert {k: body[k] for k in THINK_KEYS if k in body} == {"chat_template_kwargs": {"enable_thinking": False}}


def test_unknown_style_rejected():
    with pytest.raises(ValueError):
        VllmClient("http://127.0.0.1:9/v1", "", thinking_style="magic")

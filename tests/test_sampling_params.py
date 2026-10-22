"""请求体里的采样/长度参数按「关闭思考方式」分：OpenAI 推理模型不收 temperature、不收 max_tokens。

依据（2026-10-16 查证，见 docs/预设核实-2026-10-14.md 末节）：OpenAI 文档写 max_tokens 已弃用、与推理模型不兼容，
改用 max_completion_tokens；reasoning_effort 不是 none 时要去掉 temperature。
其他几种方式的请求体必须和改动前一字不差——下面的期望值全部手写，不从被测模块读。"""
import json
from dataclasses import replace

import httpx
import pytest

from shotrans.config import ServiceConfig
from shotrans.services import test_connection
from shotrans.translate.base import Job
from shotrans.translate.vllm import VllmClient
from tests.fakes import sse_chunks

SVC = ServiceConfig(preset="custom", base_url="https://api.example.com/v1", api_key="sk-test-0001", model="m1")
BASE_TEST = {"model", "messages", "stream"}
BASE_STREAM = {"model", "messages", "stream"}

# (thinking_style, thinking_off) → 关闭思考时额外多出的那个键
STYLE_FIELD = {
    "chat_template_kwargs": "chat_template_kwargs",
    "enable_thinking": "enable_thinking",
    "deepseek": "thinking",
    "openai_reasoning": "reasoning_effort",
}
OTHER_STYLES = ["chat_template_kwargs", "enable_thinking", "deepseek"]


def _conn_body(style, off):
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "⟦1⟧ 你好"}}]})

    r = test_connection(replace(SVC, thinking_style=style, thinking_off=off), transport=httpx.MockTransport(h))
    assert r.kind == "ok"
    assert len(seen) == 1
    return json.loads(seen[0].content)


def _stream_body(make_server, style, off):
    srv = make_server(sse_chunks(["⟦1⟧你好"]))
    c = VllmClient(srv.url, "k", 2.0, 5.0, 5.0, model="m", thinking_off=off, thinking_style=style)
    out = []
    c.translate_stream([Job(1, "Hi")], "en2zh", [], lambda i, t: out.append(t))
    assert out == ["你好"]
    return srv.requests[0]["json"]


# ---------- 测试连接 ----------
@pytest.mark.parametrize("off", [True, False])
@pytest.mark.parametrize("style", OTHER_STYLES)
def test_connection_other_styles_unchanged(style, off):
    body = _conn_body(style, off)
    extra = {STYLE_FIELD[style]} if off else set()
    assert set(body) == BASE_TEST | {"temperature", "max_tokens"} | extra
    assert body["temperature"] == 0.2 and body["max_tokens"] == 200


def test_connection_unknown_style_unchanged():
    body = _conn_body("zzz", True)
    assert set(body) == BASE_TEST | {"temperature", "max_tokens"}


@pytest.mark.parametrize("off", [True, False])
def test_connection_openai_uses_max_completion_tokens_no_temperature(off):
    body = _conn_body("openai_reasoning", off)
    extra = {"reasoning_effort"} if off else set()
    assert set(body) == BASE_TEST | {"max_completion_tokens"} | extra
    # 推理 token 也算在上限里：200 在思考开着时可能被推理吃光，所以给 2000（勾不勾都一样）
    assert body["max_completion_tokens"] == 2000
    if off:
        assert body["reasoning_effort"] == "none"


# ---------- 真翻译（流式） ----------
@pytest.mark.parametrize("off", [True, False])
@pytest.mark.parametrize("style", OTHER_STYLES)
def test_stream_other_styles_unchanged(make_server, style, off):
    body = _stream_body(make_server, style, off)
    extra = {STYLE_FIELD[style]} if off else set()
    assert set(body) == BASE_STREAM | {"temperature"} | extra
    assert body["temperature"] == 0.2 and body["stream"] is True


@pytest.mark.parametrize("off", [True, False])
def test_stream_openai_no_temperature_no_length_cap(make_server, off):
    body = _stream_body(make_server, "openai_reasoning", off)
    extra = {"reasoning_effort"} if off else set()
    assert set(body) == BASE_STREAM | extra
    if off:
        assert body["reasoning_effort"] == "none"

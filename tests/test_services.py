"""服务预设、地址规整、测试连接的错误分类、模型列表。网络全部走 httpx.MockTransport。"""
import json
from dataclasses import replace

import httpx
import pytest

from shotrans.config import ServiceConfig
from shotrans.services import (PRESETS, ConnResult, ModelsResult, apply_preset, list_models, normalize_base_url,
                               preset, test_connection)

SVC = ServiceConfig(preset="deepseek", base_url="https://api.example.com/v1", api_key="sk-test-0001", model="m1")


def _mock(handler, seen=None):
    def h(request):
        if seen is not None:
            seen.append(request)
        return handler(request)
    return httpx.MockTransport(h)


def _ok(content="⟦1⟧ 你好，世界。"):
    return lambda req: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _clock(*values):
    it = iter(values)
    return lambda: next(it)


# ---------- 预设 ----------
def test_preset_ids_in_order():
    assert [p.id for p in PRESETS] == ["deepseek", "siliconflow", "bailian", "openai", "custom"]


def test_deepseek_preset_turns_thinking_off():
    p = preset("deepseek")
    assert (p.base_url, p.model, p.thinking_style, p.thinking_off_default) == (
        "https://api.deepseek.com", "deepseek-flash", "deepseek", True)
    assert p.signup_url == "https://platform.deepseek.com/api_keys"


def test_siliconflow_preset():
    p = preset("siliconflow")
    assert (p.base_url, p.model, p.thinking_style, p.thinking_off_default) == (
        "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct", "enable_thinking", False)
    assert p.signup_url == "https://cloud.siliconflow.cn/account/ak"


def test_bailian_preset():
    p = preset("bailian")
    assert (p.base_url, p.model, p.thinking_style, p.thinking_off_default) == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-flash", "enable_thinking", False)


def test_openai_preset():
    p = preset("openai")
    assert (p.base_url, p.model, p.thinking_style, p.thinking_off_default) == (
        "https://api.openai.com/v1", "gpt-6-luna", "openai_reasoning", True)


def test_custom_preset():
    p = preset("custom")
    assert (p.base_url, p.model, p.signup_url, p.thinking_style, p.thinking_off_default) == (
        "", "", "", "chat_template_kwargs", True)


def test_unknown_preset_is_custom():
    assert preset("nope").id == "custom"


def test_public_presets_have_signup_cost_and_names():
    for p in PRESETS[:-1]:
        assert p.signup_url.startswith("https://") and p.cost_zh and p.cost_en and p.name_zh and p.name_en


def test_bailian_and_others_signup_urls():
    # 核实 2026-10-14（docs/预设核实-2026-10-14.md）
    assert preset("bailian").signup_url == "https://help.aliyun.com/zh/model-studio/get-api-key"
    assert preset("siliconflow").signup_url == "https://cloud.siliconflow.cn/account/ak"
    assert preset("openai").signup_url == "https://platform.openai.com/api-keys"


def test_public_preset_costs_defer_to_official_pricing():
    # 价格只写官网能看到的「约」数，且一律注明以官网为准（中英两份都要）
    for p in PRESETS[:-1]:
        assert "以官网" in p.cost_zh, p.id
        assert "official" in p.cost_en.lower(), p.id


def test_name_and_cost_by_language():
    p = preset("bailian")
    assert p.name("zh") == "阿里云百炼" and p.name("en") == "Alibaba Cloud Bailian"
    assert p.cost("zh") == p.cost_zh and p.cost("en") == p.cost_en


def test_apply_preset_fills_fields_keeps_key():
    s = apply_preset(ServiceConfig(api_key="k", base_url="http://old/v1", model="old"), "deepseek")
    assert s == ServiceConfig(preset="deepseek", base_url="https://api.deepseek.com", api_key="k",
                              model="deepseek-flash", thinking_off=True, thinking_style="deepseek")


def test_apply_custom_keeps_fields():
    s = ServiceConfig(preset="deepseek", base_url="http://mine/v1", model="x", thinking_off=False,
                      thinking_style="deepseek", api_key="k")
    assert apply_preset(s, "custom") == replace(s, preset="custom")


# ---------- 地址规整 ----------
@pytest.mark.parametrize("raw,expected", [
    ("https://api.example.com/v1", "https://api.example.com/v1"),
    ("  https://api.example.com/v1/ \n", "https://api.example.com/v1"),
    ("https://api.example.com/v1/chat/completions", "https://api.example.com/v1"),
    ("https://api.example.com/v1/chat/completions/", "https://api.example.com/v1"),
    ("https://api.example.com/v1/models", "https://api.example.com/v1"),
    ("https://api.example.com/V1/Chat/Completions", "https://api.example.com/V1"),
    ("", ""),
])
def test_normalize_base_url(raw, expected):
    assert normalize_base_url(raw) == expected


# ---------- 测试连接：成功 ----------
def test_ok_result_and_request_shape():
    seen = []
    r = test_connection(SVC, transport=_mock(_ok(), seen), clock=_clock(10.0, 11.5))
    assert r == ConnResult("ok", 1.5, "", "你好，世界。", "m1")
    req = seen[0]
    assert str(req.url) == "https://api.example.com/v1/chat/completions" and req.method == "POST"
    assert req.headers["authorization"] == "Bearer sk-test-0001"
    body = json.loads(req.content)
    assert body["model"] == "m1" and body["stream"] is False
    assert "Hello, world." in body["messages"][1]["content"]


def test_no_key_no_auth_header():
    seen = []
    test_connection(ServiceConfig(base_url="https://api.example.com/v1", model="m"), transport=_mock(_ok(), seen))
    assert "authorization" not in seen[0].headers


def test_pasted_whitespace_and_endpoint_stripped():
    seen = []
    svc = ServiceConfig(base_url="  https://api.example.com/v1/chat/completions/ \n", api_key=" sk-test-0001\n",
                        model=" m1 ")
    assert test_connection(svc, transport=_mock(_ok(), seen)).kind == "ok"
    assert str(seen[0].url) == "https://api.example.com/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-test-0001"
    assert json.loads(seen[0].content)["model"] == "m1"


def test_thinking_field_sent_per_style():
    seen = []
    svc = replace(SVC, thinking_off=True, thinking_style="deepseek")
    test_connection(svc, transport=_mock(_ok(), seen))
    body = json.loads(seen[0].content)
    assert body["thinking"] == {"type": "disabled"} and "chat_template_kwargs" not in body


def test_thinking_on_sends_none():
    seen = []
    test_connection(replace(SVC, thinking_off=False), transport=_mock(_ok(), seen))
    body = json.loads(seen[0].content)
    assert not any(k in body for k in ("thinking", "chat_template_kwargs", "enable_thinking", "reasoning_effort"))


def test_empty_model_uses_first_listed():
    seen = []

    def h(req):
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "auto-1"}, {"id": "auto-2"}]})
        return _ok()(req)

    r = test_connection(replace(SVC, model=""), transport=_mock(h, seen))
    assert r.kind == "ok" and r.model == "auto-1"
    assert json.loads(seen[1].content)["model"] == "auto-1"


def test_empty_model_list_failure_reported_without_post():
    seen = []
    r = test_connection(replace(SVC, model=""), transport=_mock(lambda req: httpx.Response(401, text="no"), seen))
    assert r.kind == "auth" and len(seen) == 1 and seen[0].method == "GET"


# ---------- 测试连接：错误分类 ----------
def _status(code, **kw):
    return lambda req: httpx.Response(code, **kw)


@pytest.mark.parametrize("handler,kind", [
    (_status(401, json={"error": {"message": "Authentication Fails, Your api key is invalid", "code": "invalid_api_key"}}), "auth"),
    (_status(403, text="forbidden"), "auth"),
    (_status(404, json={"error": {"message": "The model `x` does not exist", "code": "model_not_found"}}), "model_not_found"),
    (_status(400, json={"code": 20012, "message": "Model does not exist. Please check it carefully."}), "model_not_found"),
    (_status(404, text="Not Found"), "unreachable"),
    (_status(402, json={"error": {"message": "Insufficient Balance"}}), "http"),
    (_status(500, text="boom"), "http"),
    (_status(200, json={"foo": 1}), "bad_format"),
    (_status(200, text="<html>hi</html>"), "bad_format"),
    (_status(200, json={"choices": [{"message": {"content": "  "}}]}), "bad_format"),
    (_status(200, json={"choices": []}), "bad_format"),
])
def test_status_classification(handler, kind):
    assert test_connection(SVC, transport=_mock(handler)).kind == kind


def _raise(exc_type):
    def h(req):
        raise exc_type("x", request=req)
    return h


@pytest.mark.parametrize("exc,kind", [
    (httpx.ConnectError, "unreachable"),
    (httpx.ConnectTimeout, "unreachable"),
    (httpx.ReadTimeout, "timeout"),
    (httpx.WriteTimeout, "timeout"),
    (httpx.RemoteProtocolError, "unreachable"),
])
def test_transport_errors(exc, kind):
    assert test_connection(SVC, transport=_mock(_raise(exc))).kind == kind


def test_http_detail_has_status_and_message():
    r = test_connection(SVC, transport=_mock(_status(402, json={"error": {"message": "Insufficient Balance"}})))
    assert "402" in r.detail and "Insufficient Balance" in r.detail


def test_key_echo_scrubbed():
    r = test_connection(SVC, transport=_mock(_status(401, text="invalid key sk-test-0001 for you")))
    assert "sk-test-0001" not in r.detail and "***" in r.detail


def test_detail_truncated():
    r = test_connection(SVC, transport=_mock(_status(500, text="x" * 1000)))
    assert len(r.detail) <= 200


@pytest.mark.parametrize("url", ["api.example.com/v1", "", "ftp://x/v1"])
def test_bad_scheme_unreachable_without_request(url):
    seen = []
    r = test_connection(replace(SVC, base_url=url), transport=_mock(_ok(), seen))
    assert r.kind == "unreachable" and seen == []


# ---------- 模型列表 ----------
def test_list_models_ok_in_order():
    seen = []
    r = list_models("https://api.example.com/v1/", "sk-test-0001", transport=_mock(
        lambda req: httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}, {"id": 3}, {"x": 1}]}), seen))
    assert r == ModelsResult("ok", ("b", "a"))
    assert str(seen[0].url) == "https://api.example.com/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-test-0001"


@pytest.mark.parametrize("handler,kind", [
    (_status(200, json={"data": []}), "bad_format"),
    (_status(200, json=[1, 2]), "bad_format"),
    (_status(200, text="nope"), "bad_format"),
    (_status(401, text="no"), "auth"),
    (_status(404, text="Not Found"), "unreachable"),
    (_status(500, text="boom"), "http"),
])
def test_list_models_errors(handler, kind):
    assert list_models("https://api.example.com/v1", "k", transport=_mock(handler)).kind == kind


def test_list_models_connect_error():
    assert list_models("https://api.example.com/v1", transport=_mock(_raise(httpx.ConnectError))).kind == "unreachable"


def test_list_models_timeout():
    assert list_models("https://api.example.com/v1", transport=_mock(_raise(httpx.ReadTimeout))).kind == "timeout"


# ---------- 评审第 1 轮：非 ASCII key、model_not_found 判定、未知思考方式 ----------
@pytest.mark.parametrize("key", ["sk-令牌", "sk-ｔｅｓｔ"])
def test_connection_non_ascii_key_is_auth_without_request(key):
    seen = []
    r = test_connection(replace(SVC, api_key=key), transport=_mock(_ok(), seen))
    assert r.kind == "auth" and seen == []
    assert not any(ch in r.detail for ch in key) and "非英文" in r.detail


def test_connection_non_ascii_key_empty_model_no_models_request():
    seen = []
    r = test_connection(replace(SVC, api_key="sk-令牌", model=""), transport=_mock(_ok(), seen))
    assert r.kind == "auth" and seen == []


@pytest.mark.parametrize("key", ["sk-令牌", "sk-ｔｅｓｔ"])
def test_list_models_non_ascii_key_is_auth_without_request(key):
    seen = []
    r = list_models("https://api.example.com/v1", key, transport=_mock(_status(200, json={"data": [{"id": "a"}]}), seen))
    assert r.kind == "auth" and seen == []
    assert not any(ch in r.detail for ch in key) and "非英文" in r.detail


@pytest.mark.parametrize("handler,kind", [
    (_status(400, json={"error": {"message": "Unsupported parameter: 'max_tokens' is not supported with this model."}}), "http"),
    (_status(400, json={"error": {"message": "This model's maximum context length is 8192 tokens."}}), "http"),
    (_status(400, json={"error": {"message": "bad request", "code": "model_not_found"}}), "model_not_found"),
    (_status(400, json={"error": {"message": "Invalid model: abc"}}), "model_not_found"),
    (_status(400, json={"message": "model abc not found"}), "model_not_found"),
    (_status(404, json={"error": {"message": "unknown model abc"}}), "model_not_found"),
    (_status(404, json={"error": {"message": "gone", "code": "model_not_found"}}), "model_not_found"),
    (_status(400, text="plain bad request"), "http"),
])
def test_model_not_found_classification(handler, kind):
    assert test_connection(SVC, transport=_mock(handler)).kind == kind


def test_other_400_detail_keeps_message_and_scrubs_key():
    r = test_connection(SVC, transport=_mock(_status(400, json={"error": {"message": "Unsupported parameter sk-test-0001"}})))
    assert r.kind == "http" and "Unsupported parameter" in r.detail and "sk-test-0001" not in r.detail


def test_unknown_thinking_style_does_not_raise():
    seen = []
    r = test_connection(replace(SVC, thinking_off=True, thinking_style="zzz"), transport=_mock(_ok(), seen))
    assert r.kind == "ok"
    body = json.loads(seen[0].content)
    assert not any(k in body for k in ("thinking", "chat_template_kwargs", "enable_thinking", "reasoning_effort"))

class _KeyEcho(UnicodeError):
    def __str__(self):
        return "bad header sk-令牌"


def _raise_unicode(req):
    raise _KeyEcho()


def test_unicode_error_caught_and_text_not_echoed():
    r = test_connection(SVC, transport=_mock(_raise_unicode))
    assert r.kind == "unreachable" and "令牌" not in r.detail and "sk-" not in r.detail
    m = list_models("https://api.example.com/v1", "k", transport=_mock(_raise_unicode))
    assert m.kind == "unreachable" and "令牌" not in m.detail and "sk-" not in m.detail
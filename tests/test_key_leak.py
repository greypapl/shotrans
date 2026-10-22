"""key 里有非 ASCII / 零宽字符时：配置校验拒收；万一绕过校验（代码直接构造），
翻译报错、日志、自检报告里都不出现 key（原样和转义写法都不出现），翻译换下一层而不是整个崩掉。"""
import logging

import pytest

from shotrans import doctor
from shotrans.config import Config, ServiceConfig, describe, validate
from shotrans.glossary import Glossary
from shotrans.translate.base import Job, TranslateError
from shotrans.translate.router import Router
from shotrans.translate.vllm import VllmClient
from tests.fakes import closed_port_url
from tests.router_fakes import FakeService, echo, run, seg

KEY_CN = "sk-中文SECRET1234"
KEY_ZW = "sk-abc\u200bSECRET1234"
# 既有中文（repr 不转义）又有零宽字符（repr 转义）：repr 写法和 ascii 写法、原样写法三者都不同
KEY_MIX = "sk-中文\u200bSECRET1234"
SECRET = "SECRET1234"


def _clean(text: str) -> None:
    assert SECRET not in text
    assert "sk-中文" not in text and "\\u4e2d" not in text
    assert "\u200b" not in text and "\\u200b" not in text


# ---------- 配置校验 ----------
def test_validate_rejects_chinese_key_without_echo():
    errs = validate(Config(primary=ServiceConfig(base_url="http://x/v1", api_key=KEY_CN)))
    assert [(e.field, e.code) for e in errs] == [("[primary] api_key", "key")]
    _clean(describe(errs[0]) + repr(errs[0]))


def test_validate_rejects_zero_width_key_on_backup():
    errs = validate(Config(backup=ServiceConfig(base_url="http://x/v1", api_key=KEY_ZW)))
    assert [(e.field, e.code) for e in errs] == [("[backup] api_key", "key")]
    _clean(describe(errs[0]) + repr(errs[0]))


def test_validate_rejects_space_inside_key():
    errs = validate(Config(primary=ServiceConfig(base_url="http://x/v1", api_key="sk-ab cd")))
    assert [(e.field, e.code) for e in errs] == [("[primary] api_key", "key")]


def test_validate_accepts_ascii_key_and_surrounding_spaces():
    assert validate(Config(primary=ServiceConfig(base_url="http://x/v1", api_key="  sk-AZaz09_-.~!  "))) == []


def test_key_error_message_states_reason():
    errs = validate(Config(primary=ServiceConfig(base_url="http://x/v1", api_key=KEY_CN)))
    assert describe(errs[0]) == ("配置项 [primary] api_key 只能是英文字母、数字和符号"
                                 "（不能有空格、中文或看不见的字符），请重新复制 key")


# ---------- VllmClient ----------
def _client(key, model=""):
    return VllmClient(closed_port_url(), key, connect_timeout=0.5, first_token_timeout=1.0, stall_timeout=1.0,
                      model=model)


@pytest.mark.parametrize("key", [KEY_CN, KEY_ZW])
def test_model_name_non_ascii_key_is_auth_without_key(key):
    with pytest.raises(TranslateError) as ei:
        _client(key).model_name()
    assert ei.value.kind == "auth"
    _clean(str(ei.value) + ei.value.detail + repr(ei.value))
    assert ei.value.__cause__ is None and ei.value.__suppress_context__ is True


@pytest.mark.parametrize("key", [KEY_CN, KEY_ZW])
def test_stream_non_ascii_key_is_auth_without_key(key):
    with pytest.raises(TranslateError) as ei:
        _client(key, model="m").translate_stream([Job(1, "Hello")], "en2zh", [], lambda i, t: None)
    assert ei.value.kind == "auth"
    _clean(str(ei.value) + ei.value.detail)


def test_stream_other_error_reports_type_name_only(monkeypatch):
    """worker 里的其它异常只报类型名：异常原文可能带出请求头。"""
    import httpx

    def boom(self, *a, **kw):
        raise RuntimeError("Authorization: Bearer sk-ascii-SECRET1234")

    monkeypatch.setattr(httpx.Client, "stream", boom)
    with pytest.raises(TranslateError) as ei:
        _client("sk-ascii-SECRET1234", model="m").translate_stream([Job(1, "Hello")], "en2zh", [],
                                                                    lambda i, t: None)
    assert ei.value.kind == "http" and ei.value.detail == "RuntimeError"


# ---------- Router ----------
@pytest.mark.parametrize("key", [KEY_CN, KEY_ZW])
def test_router_falls_back_and_logs_no_key(key, caplog):
    caplog.set_level(logging.DEBUG)
    r = Router(_client(key), FakeService("fallback", echo("M")), Glossary(), max_concurrency=1)
    by, _ = run(r, [seg(1, "Hello")])
    assert by[1].text == "MHello" and by[1].service == "fallback"
    assert r.vllm_last_error_kind() == "auth"
    _clean(caplog.text)


class KeyedService(FakeService):
    def __init__(self, name, script, api_key):
        super().__init__(name, script)
        self.api_key = api_key


def _echo_key_error(text):
    def script(svc, jobs, on):
        raise TranslateError("http", text)
    return script


def test_router_log_scrubs_raw_key(caplog):
    caplog.set_level(logging.DEBUG)
    key = "sk-plainSECRET1234"
    v = KeyedService("vllm", _echo_key_error(f"服务端回显 {key}"), key)
    r = Router(v, FakeService("fallback", echo("M")), Glossary(), max_concurrency=1)
    by, _ = run(r, [seg(1, "Hello")])
    assert by[1].service == "fallback"
    assert "vllm 失败" in caplog.text and "***" in caplog.text
    assert SECRET not in caplog.text


def test_router_log_scrubs_escaped_key(caplog):
    caplog.set_level(logging.DEBUG)
    v = KeyedService("vllm", _echo_key_error(f"回显 {KEY_MIX!r}"), KEY_MIX)  # repr 写法：中文原样、\u200b 被转义
    r = Router(v, FakeService("fallback", echo("M")), Glossary(), max_concurrency=1)
    run(r, [seg(1, "Hello")])
    assert "vllm 失败" in caplog.text
    assert SECRET not in caplog.text


def test_router_log_scrubs_ascii_escaped_key(caplog):
    caplog.set_level(logging.DEBUG)
    v = KeyedService("vllm", _echo_key_error(f"回显 {ascii(KEY_CN)}"), KEY_CN)  # ascii 写法：中文被转义
    r = Router(v, FakeService("fallback", echo("M")), Glossary(), max_concurrency=1)
    run(r, [seg(1, "Hello")])
    assert "vllm 失败" in caplog.text
    assert SECRET not in caplog.text


# ---------- 自检 ----------
@pytest.mark.parametrize("key", [KEY_CN, KEY_ZW])
def test_doctor_primary_check_and_report_have_no_key(key):
    cfg = Config(primary=ServiceConfig(base_url=closed_port_url(), api_key=key), tencent_enabled=False)
    ok, msg = doctor.run_one(lambda: doctor.check_primary(cfg))
    assert ok is False
    _clean(msg)
    _clean(doctor.format_report([("主服务", ok, msg)], cfg, "1.0", now=0))


def test_report_masks_repr_escaped_key():
    cfg = Config(primary=ServiceConfig(base_url="http://x/v1", api_key=KEY_MIX))
    text = doctor.format_report([("主服务", False, f"回显 {KEY_MIX!r}")], cfg, "1.0", now=0)
    assert SECRET not in text


def test_report_masks_ascii_escaped_key():
    cfg = Config(primary=ServiceConfig(base_url="http://x/v1", api_key=KEY_CN))
    text = doctor.format_report([("主服务", False, f"回显 {ascii(KEY_CN)}")], cfg, "1.0", now=0)
    assert SECRET not in text

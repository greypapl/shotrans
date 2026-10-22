"""直接构造的 Config（设置界面会这么做）类型不对时：validate 报类型错误、write_config 抛 ConfigError，绝不是 TypeError。"""
import dataclasses

import pytest

from shotrans.config import Config, ConfigError, ServiceConfig, validate, write_config


def _codes(c):
    return [(e.field, e.code) for e in validate(c)]


def test_int_field_none_is_type_error():
    assert _codes(Config(chunk_chars=None)) == [("[translate] chunk_chars", "type")]


def test_write_none_int_is_config_error(tmp_path):
    with pytest.raises(ConfigError) as ei:
        write_config(tmp_path / "config.toml", Config(chunk_chars=None))
    assert "[translate] chunk_chars" in str(ei.value) and "int" in str(ei.value)
    assert list(tmp_path.iterdir()) == []


def test_float_field_string_is_type_error():
    assert _codes(Config(stall_timeout="20")) == [("[timeouts] stall", "type")]


def test_float_field_accepts_int():
    assert validate(Config(connect_timeout=5)) == []


def test_bool_not_accepted_for_int():
    assert _codes(Config(history_limit=True)) == [("[history] limit", "type")]


def test_switch_must_be_bool():
    assert _codes(Config(tencent_enabled=1)) == [("[tencent] enabled", "type")]


def test_autostart_type_checked():
    assert _codes(Config(autostart="yes")) == [("[general] autostart", "type")]


def test_service_base_url_none_is_type_error():
    c = Config(backup=ServiceConfig(base_url=None))
    assert _codes(c) == [("[backup] base_url", "type")]


def test_service_not_a_service_config():
    assert _codes(Config(primary={"base_url": "http://x/v1"})) == [("[primary]", "type")]


def test_api_key_wrong_type_not_echoed(tmp_path):
    c = Config(primary=ServiceConfig(base_url="http://x/v1", api_key=12345678))
    with pytest.raises(ConfigError) as ei:
        write_config(tmp_path / "config.toml", c)
    msg = str(ei.value)
    assert "[primary] api_key" in msg and "12345678" not in msg and "int" in msg


def test_type_errors_reported_before_value_checks():
    # 类型错了就不再做数值比较（否则 None < 1 抛 TypeError）
    c = dataclasses.replace(Config(), history_limit=None, chunk_chars=0)
    assert ("[history] limit", "type") in _codes(c)
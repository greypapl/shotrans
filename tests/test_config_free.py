"""[tencent] enabled：开关项只认 true/false。
默认关（Config()、新格式文件没写都算关）；只有旧格式（[vllm]/[fallback]）文件没写这一项时按开着算（旧版本的默认，升级不丢兜底）。"""
import tomllib

import pytest

from shotrans.config import Config, ConfigError, load_config, write_config
from shotrans.migrate import LEGACY_NAME, migrate_legacy
from shotrans.paths import config_path


def _load(tmp_path, text, name="config.local.toml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return load_config(p)


def test_fresh_config_has_free_off():
    assert Config().free_enabled is False


def test_fresh_config_written_says_enabled_false_explicitly(tmp_path):
    p = tmp_path / "config.toml"
    write_config(p, Config())
    assert tomllib.loads(p.read_text(encoding="utf-8"))["free"]["enabled"] is False
    assert load_config(p).free_enabled is False


def test_written_true_survives_reload(tmp_path):
    p = tmp_path / "config.toml"
    write_config(p, Config(free_enabled=True))
    assert tomllib.loads(p.read_text(encoding="utf-8"))["free"]["enabled"] is True
    assert load_config(p).free_enabled is True


def test_empty_file_is_off(tmp_path):
    assert _load(tmp_path, "").free_enabled is False


def test_v2_file_without_free_section_is_off(tmp_path):
    # 新用户手写的最小配置（只配了内网主服务）：主服务失败时不能悄悄把文字发给免费翻译层
    text = '[general]\nlanguage = "zh"\n[primary]\nbase_url = "http://192.168.1.5:8000/v1"\n'
    assert _load(tmp_path, text, "config.toml").free_enabled is False


def test_v2_free_section_without_enabled_is_off(tmp_path):
    assert _load(tmp_path, "[primary]\n[tencent]\nconnect = 5.0\n", "config.toml").free_enabled is False


def test_mixed_old_and_new_sections_count_as_v2_off(tmp_path):
    assert _load(tmp_path, '[vllm]\napi_key = ""\n[primary]\n').free_enabled is False


def test_v2_explicit_true_is_on(tmp_path):
    assert _load(tmp_path, "[primary]\n[tencent]\nenabled = true\n", "config.toml").free_enabled is True


def test_v2_explicit_false_stays_off(tmp_path):
    assert _load(tmp_path, "[primary]\n[tencent]\nenabled = false\n", "config.toml").free_enabled is False


def test_legacy_file_without_free_keeps_it_on(tmp_path):
    assert _load(tmp_path, '[vllm]\napi_key = ""\n').free_enabled is True


def test_legacy_fallback_only_without_free_keeps_it_on(tmp_path):
    assert _load(tmp_path, '[fallback]\nbase_url = "http://127.0.0.1:9/v1"\n').free_enabled is True


def test_legacy_free_section_without_enabled_keeps_it_on(tmp_path):
    assert _load(tmp_path, '[vllm]\napi_key = ""\n[tencent]\nconnect = 5.0\n').free_enabled is True


def test_legacy_explicit_false_stays_off(tmp_path):
    assert _load(tmp_path, '[vllm]\napi_key = ""\n[tencent]\nenabled = false\n').free_enabled is False


def test_migration_keeps_legacy_explicit_false(tmp_path):
    home, legacy = tmp_path / "home", tmp_path / "old"
    legacy.mkdir()
    (legacy / LEGACY_NAME).write_text('[vllm]\napi_key = ""\n[tencent]\nenabled = false\n', encoding="utf-8")
    assert migrate_legacy(home, legacy).status == "migrated"
    assert tomllib.loads(config_path(home).read_text(encoding="utf-8"))["free"]["enabled"] is False

def test_migration_writes_free_on_explicitly_when_legacy_omits_it(tmp_path):
    home, legacy = tmp_path / "home", tmp_path / "old"
    legacy.mkdir()
    (legacy / LEGACY_NAME).write_text('[vllm]\napi_key = ""\n', encoding="utf-8")
    assert migrate_legacy(home, legacy).status == "migrated"
    raw = tomllib.loads(config_path(home).read_text(encoding="utf-8"))
    assert raw["free"]["enabled"] is True  # 写成显式的 true，之后读回不再依赖「没写按开着算」


def test_free_can_be_disabled(tmp_path):
    assert _load(tmp_path, "[tencent]\nenabled = false\n").free_enabled is False


def test_free_explicit_true(tmp_path):
    assert _load(tmp_path, "[tencent]\nenabled = true\n").free_enabled is True


@pytest.mark.parametrize("bad", ["1", "0", '"true"', '"no"', "1.0"])
def test_free_enabled_rejects_non_bool(tmp_path, bad):
    with pytest.raises(ConfigError) as ei:
        _load(tmp_path, f"[tencent]\nenabled = {bad}\n")
    msg = str(ei.value)
    assert "[free] enabled" in msg  # 旧名 [tencent] 照读，报错按现在的名字 and "bool" in msg


def test_free_non_table_is_config_error(tmp_path):
    with pytest.raises(ConfigError) as ei:
        _load(tmp_path, "tencent = true\n")
    assert "[tencent]" in str(ei.value)


def test_free_connect_default(tmp_path):
    assert _load(tmp_path, "[timeouts]\nconnect = 1\n").free_connect_timeout == 6.0  # 不跟 vLLM 的走


def test_free_connect_int_becomes_float(tmp_path):
    c = _load(tmp_path, "[tencent]\nconnect = 8\n")
    assert c.free_connect_timeout == 8.0 and isinstance(c.free_connect_timeout, float)


def test_free_connect_wrong_type(tmp_path):
    with pytest.raises(ConfigError, match=r"\[free\] connect"):
        _load(tmp_path, '[tencent]\nconnect = "slow"\n')


def test_bool_still_rejected_for_float(tmp_path):
    with pytest.raises(ConfigError, match="breaker"):
        _load(tmp_path, "[timeouts]\nbreaker = true\n")

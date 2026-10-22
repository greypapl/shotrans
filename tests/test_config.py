"""配置 v2：新格式读取、默认值、key 读取、校验。期望值写死。"""
import pytest

from shotrans import secret
from shotrans.config import Config, ConfigError, ServiceConfig, load_config

NEW_FULL = """
[general]
language = "en"
autostart = true
[primary]
preset = "deepseek"
base_url = "https://api.deepseek.com"
api_key = "sk-plain-1111"
model = "deepseek-flash"
thinking_off = true
thinking_style = "deepseek"
[backup]
preset = "custom"
base_url = "http://10.0.0.2:8000/v1"
model = ""
thinking_off = false
[hotkey]
capture = "Ctrl+Shift+F1"
[translate]
domain_hint = "心理学"
chunk_chars = 300
chunk_max_segments = 5
max_concurrency = 2
[timeouts]
connect = 1.5
first_token = 4.5
stall = 12.5
breaker = 30.5
backup_connect = 2.5
backup_first_token = 8.5
backup_stall = 15.5
[history]
limit = 50
[tencent]
enabled = false
connect = 7.5
"""


def _write(tmp_path, text, name="config.toml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_raises_with_hint(tmp_path):
    with pytest.raises(ConfigError, match="设置"):
        load_config(tmp_path / "config.toml")


def test_new_format_all_values(tmp_path):
    c = load_config(_write(tmp_path, NEW_FULL))
    assert c.language == "en" and c.autostart is True
    assert c.primary == ServiceConfig(preset="deepseek", base_url="https://api.deepseek.com",
                                      api_key="sk-plain-1111", model="deepseek-flash", thinking_off=True,
                                      thinking_style="deepseek", key_plaintext=True, key_unreadable=False)
    assert c.backup == ServiceConfig(preset="custom", base_url="http://10.0.0.2:8000/v1", api_key="",
                                     model="", thinking_off=False, thinking_style="chat_template_kwargs")
    assert c.hotkey == "Ctrl+Shift+F1" and c.domain_hint == "心理学"
    assert (c.chunk_chars, c.chunk_max_segments, c.max_concurrency) == (300, 5, 2)
    assert (c.connect_timeout, c.first_token_timeout, c.stall_timeout, c.breaker_seconds) == (1.5, 4.5, 12.5, 30.5)
    assert (c.backup_connect_timeout, c.backup_first_token_timeout, c.backup_stall_timeout) == (2.5, 8.5, 15.5)
    assert c.history_limit == 50
    assert (c.tencent_enabled, c.tencent_connect_timeout) == (False, 7.5)


def test_defaults_when_empty(tmp_path):
    c = load_config(_write(tmp_path, ""))
    assert c == Config()
    assert c.language == "zh" and c.autostart is None
    assert c.primary == ServiceConfig() and not c.primary.configured
    assert c.backup == ServiceConfig()
    assert (c.connect_timeout, c.first_token_timeout, c.stall_timeout, c.breaker_seconds) == (6.0, 10.0, 20.0, 60.0)
    assert (c.backup_connect_timeout, c.backup_first_token_timeout, c.backup_stall_timeout) == (6.0, 10.0, 20.0)
    assert c.hotkey == "Ctrl+Alt+Q" and c.domain_hint == "" and c.history_limit == 200
    assert (c.chunk_chars, c.chunk_max_segments, c.max_concurrency) == (600, 8, 4)
    assert (c.tencent_enabled, c.tencent_connect_timeout) == (False, 6.0)  # 新格式文件没写 = 关


def test_service_defaults():
    s = ServiceConfig()
    assert (s.preset, s.base_url, s.api_key, s.model, s.thinking_off, s.thinking_style) == (
        "custom", "", "", "", False, "chat_template_kwargs")


def test_configured_means_base_url_filled():
    assert ServiceConfig(base_url="http://x/v1").configured is True
    assert ServiceConfig(base_url="   ").configured is False
    assert ServiceConfig(api_key="k").configured is False


def test_encrypted_key_read(tmp_path):
    enc = secret.protect("sk-enc-2222")
    c = load_config(_write(tmp_path, f'[primary]\nbase_url = "http://x/v1"\napi_key_enc = "{enc}"\n'))
    assert c.primary.api_key == "sk-enc-2222"
    assert c.primary.key_plaintext is False and c.primary.key_unreadable is False


def test_encrypted_key_wins_over_plain(tmp_path):
    enc = secret.protect("sk-enc-3333")
    c = load_config(_write(tmp_path, f'[backup]\nbase_url = "http://x/v1"\napi_key_enc = "{enc}"\n'
                                     'api_key = "sk-plain-3333"\n'))
    assert c.backup.api_key == "sk-enc-3333" and c.backup.key_plaintext is False


def test_undecryptable_key_treated_as_empty(tmp_path):
    c = load_config(_write(tmp_path, '[primary]\nbase_url = "http://x/v1"\napi_key_enc = "AAAAAAAA"\n'))
    assert c.primary.api_key == "" and c.primary.key_unreadable is True
    assert c.primary.configured is True  # 地址还在：只是 key 要重新填


def test_bom_tolerated(tmp_path):
    p = tmp_path / "config.toml"
    p.write_bytes(b"\xef\xbb\xbf" + '[hotkey]\ncapture = "Ctrl+Alt+W"\n'.encode("utf-8"))
    assert load_config(p).hotkey == "Ctrl+Alt+W"


def test_not_utf8_is_config_error(tmp_path):
    p = tmp_path / "config.toml"
    p.write_bytes('[translate]\ndomain_hint = "心理"\n'.encode("gbk"))
    with pytest.raises(ConfigError, match="UTF-8"):
        load_config(p)


# ---------- 类型 ----------
def test_int_timeout_becomes_float(tmp_path):
    c = load_config(_write(tmp_path, "[timeouts]\nconnect = 5\nbackup_stall = 30\n"))
    assert c.connect_timeout == 5.0 and isinstance(c.connect_timeout, float)
    assert c.backup_stall_timeout == 30.0 and isinstance(c.backup_stall_timeout, float)


def test_wrong_type_raises(tmp_path):
    with pytest.raises(ConfigError, match="connect"):
        load_config(_write(tmp_path, '[timeouts]\nconnect = "fast"\n'))


def test_wrong_type_non_secret_still_echoed(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, '[timeouts]\nconnect = "fast"\n'))
    assert "'fast'" in str(ei.value)


def test_bool_rejected_for_int(tmp_path):
    with pytest.raises(ConfigError, match="limit"):
        load_config(_write(tmp_path, "[history]\nlimit = true\n"))


@pytest.mark.parametrize("section", ["primary", "backup"])
@pytest.mark.parametrize("key", ["api_key", "api_key_enc"])
def test_wrong_type_key_not_echoed(tmp_path, section, key):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, f"[{section}]\n{key} = 12345678\n"))
    msg = str(ei.value)
    assert "12345678" not in msg
    assert key in msg and "***" in msg and "int" in msg


@pytest.mark.parametrize("text", ["general = 1\n", "primary = 3\n", 'timeouts = "x"\n', "backup = [1, 2]\n"])
def test_non_table_section_is_config_error(tmp_path, text):
    section = text.split(" ")[0]
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, text))
    assert f"[{section}]" in str(ei.value)


def test_bad_toml_raises(tmp_path):
    with pytest.raises(ConfigError, match="格式错误"):
        load_config(_write(tmp_path, "[primary\n"))


# ---------- 校验 ----------
@pytest.mark.parametrize("bad", ["Q", "Ctrl+", "Hyper+Q", "Ctrl+Alt+F25", "Ctrl+Alt+Esc"])
def test_bad_hotkey_is_config_error(tmp_path, bad):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, f'[hotkey]\ncapture = "{bad}"\n'))
    msg = str(ei.value)
    assert "[hotkey] capture" in msg and "Ctrl+Alt+Q" in msg


def test_good_hotkey_accepted(tmp_path):
    assert load_config(_write(tmp_path, '[hotkey]\ncapture = "Win+Shift+F12"\n')).hotkey == "Win+Shift+F12"


@pytest.mark.parametrize("key", ["connect", "first_token", "stall", "breaker",
                                 "backup_connect", "backup_first_token", "backup_stall"])
def test_timeouts_must_be_positive(tmp_path, key):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, f"[timeouts]\n{key} = 0\n"))
    assert f"[timeouts] {key}" in str(ei.value) and "大于 0" in str(ei.value)


def test_tencent_connect_must_be_positive(tmp_path):
    with pytest.raises(ConfigError, match=r"\[tencent\] connect"):
        load_config(_write(tmp_path, "[tencent]\nconnect = -1\n"))


def test_infinite_timeout_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"\[timeouts\] stall"):
        load_config(_write(tmp_path, "[timeouts]\nstall = inf\n"))


def test_history_limit_zero_rejected(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, "[history]\nlimit = 0\n"))
    assert "[history] limit" in str(ei.value) and "至少是 1" in str(ei.value)


def test_language_must_be_zh_or_en(tmp_path):
    with pytest.raises(ConfigError, match=r"\[general\] language"):
        load_config(_write(tmp_path, '[general]\nlanguage = "fr"\n'))


def test_autostart_must_be_bool(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, "[general]\nautostart = 1\n"))
    assert "[general] autostart" in str(ei.value) and "bool" in str(ei.value)


def test_base_url_scheme_checked(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, '[backup]\nbase_url = "api.deepseek.com"\n'))
    assert "[backup] base_url" in str(ei.value) and "http" in str(ei.value)


def test_upper_case_scheme_accepted(tmp_path):
    assert load_config(_write(tmp_path, '[primary]\nbase_url = "HTTPS://API.X.COM/v1"\n')).primary.configured


def test_thinking_style_checked(tmp_path):
    with pytest.raises(ConfigError, match=r"\[primary\] thinking_style"):
        load_config(_write(tmp_path, '[primary]\nthinking_style = "magic"\n'))


# 读文件时的类型报错要带上用户写的值（validate 兜底的类型错误只说类型名，所以这两条专测读取路径）
def test_bool_for_int_message_shows_value(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, "[history]\nlimit = true\n"))
    assert "现在是 True" in str(ei.value)


def test_autostart_wrong_type_message_shows_value(tmp_path):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, "[general]\nautostart = 7\n"))
    assert "现在是 7" in str(ei.value)
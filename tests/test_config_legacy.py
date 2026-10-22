"""旧格式（config.local.toml：[vllm]、[fallback]、明文 api_key）照样能读，含义不变。"""
import pytest

from shotrans.config import ConfigError, ServiceConfig, is_legacy, legacy_to_v2, load_config

LEGACY_FULL = """
[vllm]
base_url = "http://10.0.0.1:9000/v1"
api_key = "abc"
[hotkey]
capture = "Ctrl+Shift+F1"
[translate]
domain_hint = "心理学"
[timeouts]
connect = 1.5
first_token = 4.5
stall = 12.5
breaker = 30.5
[history]
limit = 50
[fallback]
base_url = "https://api.deepseek.com/v1"
api_key = "sk-test"
model = "deepseek-chat"
first_token = 8.5
stall = 15.5
"""


def _load(tmp_path, text):
    p = tmp_path / "config.local.toml"
    p.write_text(text, encoding="utf-8")
    return load_config(p)


def test_legacy_full_maps_to_v2(tmp_path):
    c = _load(tmp_path, LEGACY_FULL)
    assert c.primary == ServiceConfig(preset="custom", base_url="http://10.0.0.1:9000/v1", api_key="abc", model="",
                                      thinking_off=True, thinking_style="chat_template_kwargs", key_plaintext=True)
    assert c.backup == ServiceConfig(preset="custom", base_url="https://api.deepseek.com/v1", api_key="sk-test",
                                     model="deepseek-chat", thinking_off=False,
                                     thinking_style="chat_template_kwargs", key_plaintext=True)
    assert (c.connect_timeout, c.first_token_timeout, c.stall_timeout, c.breaker_seconds) == (1.5, 4.5, 12.5, 30.5)
    # 旧格式里备用没有自己的连接超时：沿用 [timeouts] connect
    assert (c.backup_connect_timeout, c.backup_first_token_timeout, c.backup_stall_timeout) == (1.5, 8.5, 15.5)
    assert c.hotkey == "Ctrl+Shift+F1" and c.domain_hint == "心理学" and c.history_limit == 50
    assert c.language == "zh" and c.autostart is None


def test_legacy_defaults_keep_old_timeouts(tmp_path):
    c = _load(tmp_path, '[vllm]\napi_key = "k"\n')
    # 没写 base_url：程序里不再带作者的内网地址，当作没配置主服务（key 照样留着）
    assert c.primary.base_url == "" and c.primary.configured is False and c.primary.api_key == "k"
    assert (c.connect_timeout, c.first_token_timeout, c.stall_timeout, c.breaker_seconds) == (2.0, 3.0, 10.0, 60.0)
    assert (c.backup_connect_timeout, c.backup_first_token_timeout, c.backup_stall_timeout) == (2.0, 10.0, 20.0)


def test_legacy_vllm_without_key_is_not_configured(tmp_path):
    c = _load(tmp_path, '[vllm]\nbase_url = "http://10.0.0.1/v1"\napi_key = ""\n')
    assert c.primary.base_url == "" and c.primary.configured is False


def test_legacy_fallback_only(tmp_path):
    c = _load(tmp_path, '[fallback]\nbase_url = "http://127.0.0.1:9/v1"\n')
    assert c.primary.configured is False
    assert c.backup.configured is True and c.backup.thinking_off is False and c.backup.api_key == ""
    assert c.backup.key_plaintext is False  # 没填 key 就不算「明文 key」


def test_new_sections_win_over_legacy(tmp_path):
    c = _load(tmp_path, '[vllm]\napi_key = "old"\n[primary]\nbase_url = "http://new/v1"\n')
    assert c.primary.base_url == "http://new/v1" and c.primary.api_key == ""
    assert c.connect_timeout == 6.0  # 有 [primary] 就是新格式：新默认值


def test_is_legacy():
    assert is_legacy({"vllm": {}}) is True
    assert is_legacy({"fallback": {}}) is True
    assert is_legacy({"vllm": {}, "primary": {}}) is False
    assert is_legacy({"fallback": {}, "backup": {}}) is False
    assert is_legacy({"tencent": {}}) is False
    assert is_legacy({}) is False


def test_legacy_to_v2_pure():
    raw = {"vllm": {"api_key": "k"}, "tencent": {"enabled": False}, "timeouts": {"stall": 9}}
    assert legacy_to_v2(raw) == {
        "general": {"language": "zh"},
        "primary": {"preset": "custom", "base_url": "", "api_key": "k", "model": "",
                    "thinking_off": True, "thinking_style": "chat_template_kwargs"},
        "backup": {"preset": "custom", "base_url": "", "api_key": "", "model": "",
                   "thinking_off": False, "thinking_style": "chat_template_kwargs"},
        "tencent": {"enabled": False},
        "timeouts": {"stall": 9, "connect": 2.0, "first_token": 3.0, "backup_connect": 2.0},
    }
    assert raw == {"vllm": {"api_key": "k"}, "tencent": {"enabled": False}, "timeouts": {"stall": 9}}  # 不改入参


def test_legacy_fallback_timeouts_moved():
    out = legacy_to_v2({"fallback": {"base_url": "http://x/v1", "first_token": 12, "stall": 30}})
    assert out["timeouts"]["backup_first_token"] == 12 and out["timeouts"]["backup_stall"] == 30


def test_legacy_non_table_still_reported(tmp_path):
    with pytest.raises(ConfigError) as ei:
        _load(tmp_path, "vllm = 3\n")
    assert "[vllm]" in str(ei.value)


def test_legacy_fallback_non_table_reported(tmp_path):
    with pytest.raises(ConfigError) as ei:
        _load(tmp_path, "fallback = [1, 2]\n")
    assert "[fallback]" in str(ei.value)


@pytest.mark.parametrize("section", ["vllm", "fallback"])
def test_legacy_wrong_type_key_not_echoed(tmp_path, section):
    with pytest.raises(ConfigError) as ei:
        _load(tmp_path, f"[{section}]\napi_key = 12345678\n")
    msg = str(ei.value)
    assert "12345678" not in msg and "api_key" in msg and "***" in msg and "int" in msg


def test_legacy_fallback_first_token_wrong_type(tmp_path):
    with pytest.raises(ConfigError, match="first_token"):
        _load(tmp_path, '[fallback]\nfirst_token = "slow"\n')


# ---------- 评审第 1 轮：legacy_to_v2 被直接调用（迁移会用）时的输入要求 ----------
def test_legacy_to_v2_vllm_not_table():
    with pytest.raises(ConfigError, match=r"\[vllm\]"):
        legacy_to_v2({"vllm": 3})


def test_legacy_to_v2_fallback_not_table():
    with pytest.raises(ConfigError, match=r"\[fallback\]"):
        legacy_to_v2({"fallback": [1, 2]})


def test_legacy_to_v2_other_section_not_table_not_dropped():
    with pytest.raises(ConfigError, match=r"\[timeouts\]"):
        legacy_to_v2({"vllm": {"api_key": "k"}, "timeouts": "x"})


def test_legacy_to_v2_rejects_new_format():
    with pytest.raises(ConfigError, match="不是旧格式"):
        legacy_to_v2({"tencent": {"enabled": False}, "hotkey": {"capture": "Ctrl+Alt+W"}})


def test_legacy_to_v2_rejects_mixed_without_overwriting():
    raw = {"vllm": {"api_key": "k"}, "primary": {"base_url": "http://new/v1"}}
    with pytest.raises(ConfigError, match="不是旧格式"):
        legacy_to_v2(raw)
    assert raw == {"vllm": {"api_key": "k"}, "primary": {"base_url": "http://new/v1"}}


def test_legacy_to_v2_rejects_mixed_backup():
    with pytest.raises(ConfigError, match="不是旧格式"):
        legacy_to_v2({"fallback": {"base_url": "http://old/v1"}, "backup": {"base_url": "http://new/v1"}})

def test_legacy_explicit_base_url_kept_unchanged(tmp_path):
    c = _load(tmp_path, '[vllm]\nbase_url = "http://192.168.7.7:8004/v1"\napi_key = "k"\n')
    assert c.primary.base_url == "http://192.168.7.7:8004/v1" and c.primary.configured is True


def test_shipped_legacy_default_has_no_lan_address():
    from shotrans import config
    assert config.LEGACY_VLLM_URL == ""

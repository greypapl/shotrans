"""写配置：读回一致、key 只存密文、临时文件不残留、旧文件备份、读回失败不替换。"""
import pytest

from shotrans import config, secret
from shotrans.config import Config, ConfigError, ServiceConfig, load_config, write_config


def _full():
    return Config(
        language="en", autostart=False,
        primary=ServiceConfig(preset="deepseek", base_url="https://api.deepseek.com", api_key="sk-write-4444",
                              model="deepseek-flash", thinking_off=True, thinking_style="deepseek"),
        backup=ServiceConfig(preset="custom", base_url="http://10.0.0.2:8000/v1", api_key="",
                             model='m"q\\x', thinking_off=False),
        hotkey="Ctrl+Shift+F2", domain_hint='心理学 "学术" \\ 论文\n第二行',
        chunk_chars=321, chunk_max_segments=3, max_concurrency=2,
        connect_timeout=1.25, first_token_timeout=4.5, stall_timeout=12.0, breaker_seconds=30.0,
        backup_connect_timeout=2.5, backup_first_token_timeout=8.5, backup_stall_timeout=15.5,
        history_limit=77, tencent_enabled=False, tencent_connect_timeout=7.0)


def test_round_trip(tmp_path):
    p = tmp_path / "config.toml"
    write_config(p, _full())
    assert load_config(p) == _full()


def test_key_stored_encrypted_not_plain(tmp_path):
    p = tmp_path / "config.toml"
    write_config(p, _full())
    text = p.read_text(encoding="utf-8")
    assert "sk-write-4444" not in text
    assert "api_key_enc" in text
    assert "\napi_key =" not in text


def test_plaintext_key_converted_on_save(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[primary]\nbase_url = "http://x/v1"\napi_key = "sk-hand-5555"\n', encoding="utf-8")
    c = load_config(p)
    assert c.primary.key_plaintext is True
    write_config(p, c)
    assert "sk-hand-5555" not in p.read_text(encoding="utf-8")
    c2 = load_config(p)
    assert c2.primary.api_key == "sk-hand-5555" and c2.primary.key_plaintext is False


def test_no_tmp_left_and_bak_kept(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('# 手写的注释\n[hotkey]\ncapture = "Ctrl+Alt+W"\n', encoding="utf-8")
    write_config(p, Config())
    assert sorted(x.name for x in tmp_path.iterdir()) == ["config.toml", "config.toml.bak"]
    assert (tmp_path / "config.toml.bak").read_text(encoding="utf-8").startswith("# 手写的注释")


def test_first_write_has_no_bak(tmp_path):
    p = tmp_path / "config.toml"
    write_config(p, Config())
    assert sorted(x.name for x in tmp_path.iterdir()) == ["config.toml"]


def test_readback_mismatch_keeps_original(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text("[history]\nlimit = 9\n", encoding="utf-8")
    real = config.config_to_dict

    def broken(c):
        d = real(c)
        d["history"]["limit"] = 1234
        return d

    monkeypatch.setattr(config, "config_to_dict", broken)
    with pytest.raises(ConfigError, match="不一致"):
        write_config(p, Config(history_limit=10))
    assert p.read_text(encoding="utf-8") == "[history]\nlimit = 9\n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["config.toml"]


def test_readback_parse_failure_keeps_original(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text("[history]\nlimit = 9\n", encoding="utf-8")
    monkeypatch.setattr(config, "dump_toml", lambda data, header="": "[broken\n")
    with pytest.raises(ConfigError):
        write_config(p, Config())
    assert p.read_text(encoding="utf-8") == "[history]\nlimit = 9\n"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["config.toml"]


def test_replace_os_error_is_config_error_and_keeps_original(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text("[history]\nlimit = 9\n", encoding="utf-8")

    def locked(src, dst):
        raise PermissionError(13, "文件被占用")

    monkeypatch.setattr(config.os, "replace", locked)
    with pytest.raises(ConfigError, match="原文件没有改动"):
        write_config(p, Config(history_limit=10))
    assert p.read_text(encoding="utf-8") == "[history]\nlimit = 9\n"
    assert not (tmp_path / "config.toml.tmp").exists()


def test_invalid_config_not_written(tmp_path):
    p = tmp_path / "config.toml"
    with pytest.raises(ConfigError, match=r"\[hotkey\] capture"):
        write_config(p, Config(hotkey="Q"))
    assert not p.exists()


def test_invalid_config_rejected_before_any_write(tmp_path, monkeypatch):
    # 读回校验也会拦住坏配置；这条专测「写之前先校验」：坏配置连加密、生成文本都不该走到
    dumped = []
    monkeypatch.setattr(config, "dump_toml", lambda data, header="": dumped.append(1) or "")
    with pytest.raises(ConfigError, match="至少是 1"):
        write_config(tmp_path / "config.toml", Config(history_limit=0))
    assert dumped == []
    assert list(tmp_path.iterdir()) == []


def test_encrypt_failure_is_config_error(tmp_path, monkeypatch):
    def boom(plain):
        raise secret.SecretError("坏了")

    monkeypatch.setattr(secret, "protect", boom)
    p = tmp_path / "config.toml"
    with pytest.raises(ConfigError, match="加密"):
        write_config(p, _full())
    assert not p.exists()


def test_sections_in_order_with_header(tmp_path):
    p = tmp_path / "config.toml"
    write_config(p, _full())
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# ")
    assert [ln for ln in text.splitlines() if ln.startswith("[")] == [
        "[general]", "[primary]", "[backup]", "[tencent]", "[hotkey]", "[translate]", "[timeouts]", "[history]"]


def test_autostart_none_not_written(tmp_path):
    p = tmp_path / "config.toml"
    write_config(p, Config())
    assert "autostart" not in p.read_text(encoding="utf-8")
    assert load_config(p).autostart is None


def test_chinese_path(tmp_path):
    p = tmp_path / "数据 目录" / "截图翻译" / "config.toml"
    write_config(p, _full())
    assert load_config(p) == _full()


# ---------- 评审第 1 轮 M1：解不开的密文，没重新填就原样保留 ----------
UNREADABLE = '[primary]\nbase_url = "http://x/v1"\napi_key_enc = "AAAAAAAA"\n'


def test_unreadable_key_kept_when_saving_other_changes(tmp_path):
    import dataclasses
    p = tmp_path / "config.toml"
    p.write_text(UNREADABLE, encoding="utf-8")
    c = load_config(p)
    write_config(p, dataclasses.replace(c, hotkey="Ctrl+Alt+W"))
    assert 'api_key_enc = "AAAAAAAA"' in p.read_text(encoding="utf-8")
    back = load_config(p)
    assert back.hotkey == "Ctrl+Alt+W" and back.primary.key_unreadable is True


def test_unreadable_key_replaced_when_refilled(tmp_path):
    import dataclasses
    p = tmp_path / "config.toml"
    p.write_text(UNREADABLE, encoding="utf-8")
    c = load_config(p)
    write_config(p, dataclasses.replace(c, primary=dataclasses.replace(c.primary, api_key="sk-new-6666")))
    assert "AAAAAAAA" not in p.read_text(encoding="utf-8")
    back = load_config(p)
    assert back.primary.api_key == "sk-new-6666" and back.primary.key_unreadable is False


def test_unreadable_key_cleared_on_purpose(tmp_path):
    import dataclasses
    p = tmp_path / "config.toml"
    p.write_text(UNREADABLE, encoding="utf-8")
    c = load_config(p)
    cleared = dataclasses.replace(c.primary, key_enc="", key_unreadable=False)
    write_config(p, dataclasses.replace(c, primary=cleared))
    assert 'api_key_enc = ""' in p.read_text(encoding="utf-8")
    assert load_config(p).primary.key_unreadable is False

def test_readable_key_cleared_by_emptying(tmp_path):
    # 能解开的 key，界面里清空 api_key：保存后就没有 key 了，原密文不能「复活」
    import dataclasses
    p = tmp_path / "config.toml"
    p.write_text(f'[primary]\nbase_url = "http://x/v1"\napi_key_enc = "{secret.protect("sk-old-7777")}"\n',
                 encoding="utf-8")
    c = load_config(p)
    write_config(p, dataclasses.replace(c, primary=dataclasses.replace(c.primary, api_key="")))
    assert 'api_key_enc = ""' in p.read_text(encoding="utf-8")
    assert load_config(p).primary.api_key == ""
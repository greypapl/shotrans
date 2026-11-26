from dataclasses import replace

import pytest

from shotrans.config import Config, ConfigError, config_from_raw, load_config, write_config, write_select_config


def test_defaults_and_roundtrip(tmp_path):
    assert config_from_raw({"general": {"language": "en"}}).select_enabled is True
    path = tmp_path / "config.toml"
    config = Config(select_enabled=False, select_exclude=["Test.EXE", "另一个.exe"])
    write_config(path, config)
    assert load_config(path) == config


@pytest.mark.parametrize("table", [{"enabled": 1}, {"exclude": "app.exe"}, {"exclude": [True]}])
def test_reject_wrong_types(table):
    with pytest.raises(ConfigError):
        config_from_raw({"select": table})


def test_update_select_preserves_other_sections_and_does_not_reencrypt(tmp_path, monkeypatch):
    from shotrans import config as module

    path = tmp_path / "config.toml"
    prefix = '# comment\r\n[primary]\r\napi_key = "preserve-this"\r\n'
    suffix = '[unknown]\r\nmessage = """\r\n[select]\r\nnot a real section\r\n"""\r\n'
    path.write_bytes((prefix + '[ "select" ] # comment\r\nenabled = true\r\n' + suffix).encode())
    config = replace(load_config(path), select_enabled=False, select_exclude=["test.exe"])
    monkeypatch.setattr(module.secret, "protect", lambda *_: pytest.fail("unrelated settings rewritten"))
    write_select_config(path, config)
    result = path.read_bytes().decode()
    assert result.startswith(prefix)
    assert result.endswith(suffix)
    assert load_config(path).select_exclude == ["test.exe"]
    assert not load_config(path).select_enabled

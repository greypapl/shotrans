"""[hotkey] clipboard：默认 Ctrl+Alt+C；写 "" = 不用；写错、和截图键相同都报错；读写往返；没写用默认。"""
import pytest

from shotrans.config import Config, ConfigError, config_from_raw, load_config, validate, write_config


def test_default():
    assert Config().clip_hotkey == "Ctrl+Alt+C"
    assert config_from_raw({"hotkey": {"capture": "Ctrl+Alt+Q"}}).clip_hotkey == "Ctrl+Alt+C"


def test_empty_means_off():
    assert validate(Config(clip_hotkey="")) == [] and validate(Config(clip_hotkey="  ")) == []


@pytest.mark.parametrize("spec", ["C", "Hyper+C", "Ctrl+Alt+"])
def test_invalid(spec):
    errs = validate(Config(clip_hotkey=spec))
    assert [(e.field, e.code) for e in errs] == [("[hotkey] clipboard", "clip_hotkey")]


def test_same_as_capture_any_spelling():
    errs = validate(Config(hotkey="Ctrl+Alt+Q", clip_hotkey="ctrl+alt+q"))
    assert [(e.field, e.code) for e in errs] == [("[hotkey] clipboard", "hotkey_same")]
    with pytest.raises(ConfigError, match="重复"):
        config_from_raw({"hotkey": {"capture": "Ctrl+Alt+Q", "clipboard": "Ctrl+Alt+Q"}})


def test_bad_capture_does_not_add_same_error():
    errs = validate(Config(hotkey="Q", clip_hotkey="Ctrl+Alt+C"))
    assert [e.field for e in errs] == ["[hotkey] capture"]


def test_wrong_type():
    with pytest.raises(ConfigError):
        config_from_raw({"hotkey": {"clipboard": 3}})


@pytest.mark.parametrize("spec", ["", "Ctrl+Shift+F9"])
def test_round_trip(tmp_path, spec):
    p = tmp_path / "config.toml"
    write_config(p, Config(clip_hotkey=spec))
    assert load_config(p).clip_hotkey == spec

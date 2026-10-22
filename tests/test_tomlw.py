"""最小 TOML 写入：tomllib 读回必须一模一样。"""
import tomllib

import pytest

from shotrans.tomlw import dump_toml

TRICKY = ["plain", "C:\\Users\\张三\\截图", 'say "hi"', "line1\nline2", "tab\there",
          "ctl\x01\x1f\x7f", "emoji 😀", "", "it's", "a\r\nb", "\\n 不是换行"]


@pytest.mark.parametrize("s", TRICKY)
def test_string_round_trip(s):
    assert tomllib.loads(dump_toml({"a": {"k": s}})) == {"a": {"k": s}}


def test_scalar_types():
    text = dump_toml({"s": {"b": True, "i": 3, "f": 6.0, "g": 0.1, "n": False}})
    back = tomllib.loads(text)
    assert back == {"s": {"b": True, "i": 3, "f": 6.0, "g": 0.1, "n": False}}
    assert type(back["s"]["i"]) is int and type(back["s"]["f"]) is float
    assert "b = true" in text and "i = 3" in text and "f = 6.0" in text and "n = false" in text


def test_none_skipped():
    assert tomllib.loads(dump_toml({"s": {"a": None, "b": 1}})) == {"s": {"b": 1}}


def test_non_bare_keys_quoted():
    data = {"节 1": {"键.点": 1, "ok_key-2": 2}}
    assert tomllib.loads(dump_toml(data)) == data


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_rejected(bad):
    with pytest.raises(ValueError):
        dump_toml({"s": {"x": bad}})


@pytest.mark.parametrize("bad", [[1, 2], {"x": 1}, b"raw"])
def test_unsupported_type_rejected(bad):
    with pytest.raises(TypeError):
        dump_toml({"s": {"x": bad}})


def test_section_must_be_dict():
    with pytest.raises(TypeError):
        dump_toml({"s": 1})


def test_header_is_comment():
    text = dump_toml({"a": {"k": 1}}, header="第一行\n第二行")
    assert text.startswith("# 第一行\n# 第二行\n")
    assert tomllib.loads(text) == {"a": {"k": 1}}


def test_empty_section_written():
    assert tomllib.loads(dump_toml({"a": {}})) == {"a": {}}

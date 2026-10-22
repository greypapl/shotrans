"""文字表：两种语言键一致、占位符一致、代码里用到的键都存在、界面调用里没有写死的中文。"""
import ast
import logging
import re
import string
from pathlib import Path

import pytest

from shotrans import i18n
from shotrans.i18n import N_, merge_tables, set_language, t

PKG = Path(i18n.__file__).parent
CJK = re.compile(r"[\u3400-\u9fff]")
KEY_USE = re.compile(r"\b(?:t|N_)\(\s*[\"']([^\"']+)[\"']")
# 这些调用的参数会显示给用户（或成为显示给用户的报错）：参数里不许直接写中文，一律 t("键")
UI_CALLS = {"showMessage", "show_notice", "setText", "setToolTip", "setWindowTitle", "addAction", "_toast", "setPlaceholderText",
            "critical", "information", "warning", "question", "_show_fatal", "setTabText", "addTab",
            "setHeaderLabels", "setHorizontalHeaderLabels", "MessageBoxW", "notify", "QAction", "QLabel",
            "QPushButton", "QCheckBox", "QToolButton", "QGroupBox", "QRadioButton", "addItem", "setTitle",
            "setStatusTip", "ConfigError", "OcrError", "SecretError", "MigrateResult", "ConnResult", "ModelsResult"}
LOGGERS = {"log", "logging", "logger"}


def _code_files():
    return [p for p in PKG.rglob("*.py") if "texts" not in p.relative_to(PKG).parts]


def test_each_module_has_same_keys_in_both_languages():
    for name, zh, en in i18n.load_modules():
        assert set(zh) == set(en), (f"texts/{name}.py：只在中文里 {sorted(set(zh) - set(en))}，"
                                    f"只在英文里 {sorted(set(en) - set(zh))}")


def _fields(s):
    return {f for _, f, _, _ in string.Formatter().parse(s) if f is not None}


def test_placeholders_match():
    tb = i18n.tables()
    for k, zh in tb["zh"].items():
        assert _fields(zh) == _fields(tb["en"][k]), k


def test_no_empty_texts():
    for lang in ("zh", "en"):
        for k, v in i18n.tables()[lang].items():
            assert v.strip(), f"{lang}:{k}"


def test_english_has_no_chinese_except_native_names():
    for k, v in i18n.tables()["en"].items():
        if ".native." not in k:
            assert not CJK.search(v), k


def test_used_keys_exist():
    defined = set(i18n.tables()["zh"])
    missing = [f"{p.relative_to(PKG)}: {m.group(1)}" for p in _code_files()
               for m in KEY_USE.finditer(p.read_text(encoding="utf-8")) if m.group(1) not in defined]
    assert missing == []


def test_no_dynamic_keys():
    bad = [str(p.relative_to(PKG)) for p in _code_files() if re.search(r"\bt\(\s*f[\"']", p.read_text(encoding="utf-8"))]
    assert bad == []


def _call_name(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    return f.id if isinstance(f, ast.Name) else None


def _is_logger_call(node):
    f = node.func
    return isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in LOGGERS


def test_no_hardcoded_chinese_in_ui_calls():
    offenders = []
    for p in _code_files():
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or _call_name(node) not in UI_CALLS or _is_logger_call(node):
                continue
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and CJK.search(n.value)
                       for n in ast.walk(arg)):
                    offenders.append(f"{p.relative_to(PKG)}:{node.lineno}")
    assert offenders == []


def test_t_formats_and_falls_back(monkeypatch):
    monkeypatch.setattr(i18n, "_tables", {"zh": {"a": "甲{n}", "b": "乙"}, "en": {"a": "A{n}"}})
    assert t("a", n=1) == "甲1"
    i18n._current = "en"
    assert t("a", n=2) == "A2"
    assert t("b") == "乙"  # 英文表缺这个键：退回中文


def test_missing_key_returns_key_and_logs(monkeypatch, caplog):
    monkeypatch.setattr(i18n, "_tables", {"zh": {}, "en": {}})
    with caplog.at_level(logging.WARNING):
        assert t("x.y") == "x.y"
    assert any("x.y" in r.getMessage() for r in caplog.records)


def test_bad_params_return_raw_text(monkeypatch):
    monkeypatch.setattr(i18n, "_tables", {"zh": {"a": "甲{n}"}, "en": {}})
    assert t("a", m=1) == "甲{n}"


def test_set_language_emits_once_per_change(qapp):
    got = []
    i18n.hub().language_changed.connect(got.append)
    try:
        set_language("en")
        set_language("en")
        set_language("zh")
    finally:
        i18n.hub().language_changed.disconnect(got.append)
    assert got == ["en", "zh"] and i18n.language() == "zh"


def test_set_language_rejects_unknown():
    with pytest.raises(ValueError):
        set_language("fr")


def test_merge_rejects_duplicate_keys():
    with pytest.raises(ValueError, match="a.b"):
        merge_tables([("x", {"a.b": "1"}, {"a.b": "1"}), ("y", {"a.b": "2"}, {"a.b": "2"})])


def test_N_is_identity():
    assert N_("k.k") == "k.k"


def test_core_examples():
    assert t("tray.settings") == "设置…" and t("status.vllm") == "主服务"
    set_language("en")
    assert t("tray.settings") == "Settings…" and t("status.vllm") == "Primary"


def test_texts_registry_matches_directory():
    """frozen 下 pkgutil 扫不到文字模块，改为显式登记：新增文字模块忘了登记，这里会红。"""
    from shotrans import texts
    on_disk = sorted(p.stem for p in (PKG / "texts").glob("*.py") if p.stem != "__init__")
    assert list(texts.MODULES) == on_disk


def test_load_modules_follows_registry(monkeypatch):
    from shotrans import texts
    monkeypatch.setattr(texts, "MODULES", ("about",))
    assert [name for name, _, _ in i18n.load_modules()] == ["about"]
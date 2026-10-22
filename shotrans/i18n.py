"""界面文字的中英文：t(键名, **参数)。当前语言来自配置；切换时 hub().language_changed 发信号，
已打开的窗口和托盘接这个信号刷新文字。

文字表按模块分文件放在 shotrans/texts/ 下（每个窗口一份，并行开发不撞文件），第一次用到时合并（模块名在 texts/__init__.py 的 MODULES 里登记）；
同一个键出现在两个文件里直接报错。发给大模型的提示词不走这里。
线程：t() 只读字典，工作线程里也能调用；set_language() 只在主线程调用。"""
from __future__ import annotations

import importlib
import logging

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

LANGUAGES = ("zh", "en")
_current = "zh"
_tables: dict[str, dict[str, str]] | None = None
_hub = None


def N_(key: str) -> str:
    """只做标记：先存进表、稍后才 t() 的键用它包住，键检查（tests/test_i18n.py）能扫到。"""
    return key


def merge_tables(modules) -> dict[str, dict[str, str]]:
    zh: dict[str, str] = {}
    en: dict[str, str] = {}
    owner: dict[str, str] = {}
    for name, z, e in modules:
        for key in set(z) | set(e):
            if key in owner:
                raise ValueError(f"文字键 {key} 同时出现在 texts/{owner[key]}.py 和 texts/{name}.py")
            owner[key] = name
        zh.update(z)
        en.update(e)
    return {"zh": zh, "en": en}


def load_modules() -> list[tuple[str, dict, dict]]:
    from . import texts

    out = []
    for name in texts.MODULES:
        mod = importlib.import_module(f"{texts.__name__}.{name}")
        out.append((name, mod.ZH, mod.EN))
    return out


def tables() -> dict[str, dict[str, str]]:
    global _tables
    if _tables is None:
        _tables = merge_tables(load_modules())
    return _tables


def language() -> str:
    return _current


class _Hub(QObject):
    language_changed = Signal(str)


def hub() -> _Hub:
    global _hub
    if _hub is None:
        _hub = _Hub()
    return _hub


def set_language(lang: str) -> None:
    global _current
    if lang not in LANGUAGES:
        raise ValueError(f"不支持的界面语言：{lang!r}")
    if lang == _current:
        return
    _current = lang
    hub().language_changed.emit(lang)


def t(key: str, /, **kw) -> str:
    """key 只能按位置传（/）：文字里可以有名叫 {key} 的占位符（config.err.type）。"""
    tb = tables()
    text = tb[_current].get(key)
    if text is None:
        text = tb["zh"].get(key)
    if text is None:
        log.warning("缺少文字键 %s", key)
        return key
    if not kw:
        return text
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        log.warning("文字键 %s 的参数不对：%s", key, sorted(kw))
        return text

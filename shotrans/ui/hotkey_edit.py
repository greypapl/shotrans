"""快捷键录入框：点一下，按下组合键即录入（修饰键 + 字母/数字/F 键，规则同 hotkey.parse_hotkey）；
录入后立刻用 checker 试注册（托盘程序的 hotkey_free：临时 id，成功即注销），被占用当场标红。"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QLineEdit, QVBoxLayout, QWidget

from .. import i18n
from ..hotkey import parse_hotkey
from ..i18n import t

RED = "border:1px solid #ef4444"
_MOD_ORDER = ((Qt.KeyboardModifier.ControlModifier, "Ctrl"), (Qt.KeyboardModifier.AltModifier, "Alt"),
              (Qt.KeyboardModifier.ShiftModifier, "Shift"), (Qt.KeyboardModifier.MetaModifier, "Win"))


def _v(x) -> int:
    return x.value if hasattr(x, "value") else int(x)


def spec_from_key(key, modifiers) -> str | None:
    k = _v(key)
    is_f = _v(Qt.Key.Key_F1) <= k <= _v(Qt.Key.Key_F24)
    if _v(Qt.Key.Key_A) <= k <= _v(Qt.Key.Key_Z) or _v(Qt.Key.Key_0) <= k <= _v(Qt.Key.Key_9):
        name = chr(k)
    elif is_f:
        name = f"F{k - _v(Qt.Key.Key_F1) + 1}"
    else:
        return None
    parts = [label for flag, label in _MOD_ORDER if modifiers & flag]
    if not parts and not is_f:  # 字母 F 不是 F 键：不带修饰键的只有 F1～F24（和 parse_hotkey 的规则一致）
        return None
    return "+".join(parts + [name])


class _KeyLine(QLineEdit):
    def __init__(self, on_key, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self._on_key = on_key

    def keyPressEvent(self, e) -> None:
        if _v(e.key()) in (_v(Qt.Key.Key_Tab), _v(Qt.Key.Key_Backtab)):
            super().keyPressEvent(e)
            return
        self._on_key(e.key(), e.modifiers())


class HotkeyEdit(QWidget):
    changed = Signal(str)

    def __init__(self, checker: Callable[[str], bool], parent=None):
        super().__init__(parent)
        self._checker = checker
        self._spec = ""
        self.taken = False
        self.invalid = False
        self.edit = _KeyLine(self._on_key, self)
        self.status = QLabel(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit)
        lay.addWidget(self.status)
        i18n.hub().language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self, *_) -> None:
        self.edit.setPlaceholderText(t("hotkey.placeholder"))
        if self._spec:
            self._show()

    def spec(self) -> str:
        return self._spec

    def is_valid(self) -> bool:
        return bool(self._spec) and not self.invalid and not self.taken

    def set_spec(self, spec: str) -> None:
        self._spec = spec
        self.edit.setText(spec)
        try:
            parse_hotkey(spec)
            self.invalid = False
        except ValueError:
            self.invalid = True
        self.taken = (not self.invalid) and not self._checker(spec)
        self._show()

    def _show(self) -> None:
        bad = self.invalid or self.taken
        self.edit.setStyleSheet(RED if bad else "")
        if self.invalid:
            self.status.setText(t("hotkey.invalid"))
        elif self.taken:
            self.status.setText(t("hotkey.taken", hotkey=self._spec))
        else:
            self.status.setText(t("hotkey.free"))

    def _on_key(self, key, modifiers) -> None:
        spec = spec_from_key(key, modifiers)
        if spec is None:
            if not _is_modifier_key(key):
                self.status.setText(t("hotkey.need_modifier"))
            return
        self.set_spec(spec)
        self.changed.emit(spec)


def _is_modifier_key(key) -> bool:
    return _v(key) in {_v(k) for k in (Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta)}

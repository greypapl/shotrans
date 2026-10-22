import pytest
from PySide6.QtCore import Qt

from shotrans.ui.hotkey_edit import HotkeyEdit, spec_from_key

K, M = Qt.Key, Qt.KeyboardModifier


@pytest.mark.parametrize("key,mods,expected", [
    (K.Key_W, M.ControlModifier | M.AltModifier, "Ctrl+Alt+W"),
    (K.Key_5, M.ControlModifier | M.ShiftModifier, "Ctrl+Shift+5"),
    (K.Key_F5, M.NoModifier, "F5"),
    (K.Key_F24, M.AltModifier, "Alt+F24"),
    (K.Key_Q, M.ControlModifier | M.AltModifier | M.ShiftModifier | M.MetaModifier, "Ctrl+Alt+Shift+Win+Q"),
    (K.Key_F, M.NoModifier, None),          # 字母 F 不是 F 键：要修饰键
    (K.Key_A, M.ShiftModifier, "Shift+A"),
    (K.Key_Escape, M.ControlModifier, None),
    (K.Key_Control, M.ControlModifier, None),
    (K.Key_Space, M.ControlModifier, None),
])
def test_spec_from_key(key, mods, expected):
    assert spec_from_key(key, mods) == expected


@pytest.fixture
def make(qtbot):
    def _make(taken=()):
        checks = []
        w = HotkeyEdit(lambda spec: checks.append(spec) or spec not in taken)
        qtbot.addWidget(w)
        w.checks = checks
        return w
    return _make


def test_key_press_records_and_checks(qtbot, make):
    w = make()
    got = []
    w.changed.connect(got.append)
    qtbot.keyClick(w.edit, K.Key_W, M.ControlModifier | M.AltModifier)
    assert w.spec() == "Ctrl+Alt+W" and w.edit.text() == "Ctrl+Alt+W"
    assert w.checks == ["Ctrl+Alt+W"] and got == ["Ctrl+Alt+W"]
    assert w.is_valid() and w.status.text() == "可用"


def test_taken_marked_red(qtbot, make):
    w = make(taken={"Ctrl+Alt+W"})
    qtbot.keyClick(w.edit, K.Key_W, M.ControlModifier | M.AltModifier)
    assert w.taken and not w.is_valid()
    assert "#ef4444" in w.edit.styleSheet() and w.status.text() == "Ctrl+Alt+W 被别的软件占用了"


def test_bare_letter_ignored(qtbot, make):
    w = make()
    w.set_spec("Ctrl+Alt+Q")
    qtbot.keyClick(w.edit, K.Key_A)
    assert w.spec() == "Ctrl+Alt+Q" and "修饰键" in w.status.text()


def test_set_spec_invalid(make):
    w = make()
    w.set_spec("Hyper+Q")
    assert w.invalid and not w.is_valid() and w.checks == []


def test_edit_is_read_only_text(qtbot, make):
    w = make()
    qtbot.keyClicks(w.edit, "abc")
    assert w.edit.text() == ""

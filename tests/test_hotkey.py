import ctypes
from ctypes import wintypes

import pytest
from PySide6.QtCore import QByteArray

from shotrans.hotkey import WM_HOTKEY, GlobalHotkey, parse_hotkey


def test_parse_hotkey():
    assert parse_hotkey("Ctrl+Alt+D") == (0x2 | 0x1, ord("D"))
    assert parse_hotkey("ctrl + shift + f12") == (0x2 | 0x4, 0x7B)
    assert parse_hotkey("Win+1") == (0x8, ord("1"))
    assert parse_hotkey("F9") == (0, 0x78)
    assert parse_hotkey(" f1 ") == (0, 0x70)


@pytest.mark.parametrize("bad", ["D", "1", "Ctrl+", "Hyper+D", "Ctrl+Enter", "F0", "F25", "Fx", "F"])
def test_parse_hotkey_rejects(bad):
    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_register_then_second_is_occupied(qapp):
    a = GlobalHotkey("Ctrl+Alt+Shift+F11", lambda: None, hotkey_id=0xB0F1)
    b = GlobalHotkey("Ctrl+Alt+Shift+F11", lambda: None, hotkey_id=0xB0F2)
    try:
        assert a.register() is True
        assert b.register() is False
    finally:
        a.unregister()
        b.unregister()


def _msg(message, wparam):
    m = wintypes.MSG()
    m.message = message
    m.wParam = wparam
    return m


def test_native_filter_fires_callback_for_own_id():
    hits = []
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: hits.append(1), hotkey_id=0xB0F3)
    m = _msg(WM_HOTKEY, 0xB0F3)
    assert hk.nativeEventFilter(QByteArray(b"windows_generic_MSG"), ctypes.addressof(m))[0] is True
    assert hits == [1]


def test_native_filter_ignores_other_id():
    hits = []
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: hits.append(1), hotkey_id=0xB0F3)
    m = _msg(WM_HOTKEY, 0xB0F4)
    assert hk.nativeEventFilter(QByteArray(b"windows_generic_MSG"), ctypes.addressof(m))[0] is False
    assert hits == []

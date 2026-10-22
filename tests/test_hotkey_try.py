import ctypes

import pytest

from shotrans import hotkey
from shotrans.hotkey import try_register


def test_free_combo_registers_and_releases(qapp):
    assert try_register("Ctrl+Alt+Shift+F7") is True
    assert try_register("Ctrl+Alt+Shift+F7") is True  # 第一次必须注销，否则第二次失败


def test_taken_combo(qapp):
    user32 = ctypes.windll.user32
    assert user32.RegisterHotKey(None, 0xB0A5, 0x1 | 0x2 | 0x4 | 0x4000, 0x76)  # Ctrl+Alt+Shift+F7
    try:
        assert try_register("Ctrl+Alt+Shift+F7") is False
    finally:
        user32.UnregisterHotKey(None, 0xB0A5)


def test_invalid_spec_raises():
    with pytest.raises(ValueError):
        try_register("Hyper+Q")


def test_uses_probe_id_and_norepeat(monkeypatch):
    calls = []

    class U:
        def RegisterHotKey(self, hwnd, hid, mods, vk):
            calls.append(("reg", hid, mods, vk))
            return 1

        def UnregisterHotKey(self, hwnd, hid):
            calls.append(("unreg", hid))
            return 1

    class W:
        user32 = U()

    class C:
        windll = W()

    monkeypatch.setattr(hotkey, "ctypes", C())
    assert try_register("Ctrl+Alt+Shift+F9") is True
    assert calls == [("reg", 0xB0A4, 0x1 | 0x2 | 0x4 | 0x4000, 0x78), ("unreg", 0xB0A4)]

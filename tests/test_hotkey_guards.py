"""hotkey 各守卫/分支的专属测试，每个只被它自己的那一处触发。"""
import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QByteArray

from shotrans import hotkey
from shotrans.hotkey import WM_HOTKEY, GlobalHotkey, parse_hotkey

TYPE = QByteArray(b"windows_generic_MSG")


# ---------- parse_hotkey ----------

def test_single_part_rejected():
    # 只有一个键、没有修饰键
    with pytest.raises(ValueError):
        parse_hotkey("D")


@pytest.mark.parametrize("spec,expected", [
    ("Alt+D", (0x1, ord("D"))),
    ("Shift+D", (0x4, ord("D"))),
    ("Control+D", (0x2, ord("D"))),
    ("Ctrl+Alt+Shift+Win+D", (0x2 | 0x1 | 0x4 | 0x8, ord("D"))),
    ("ctrl+d", (0x2, ord("D"))),
    ("Ctrl+0", (0x2, ord("0"))),
])
def test_modifier_values(spec, expected):
    assert parse_hotkey(spec) == expected


@pytest.mark.parametrize("spec", ["Ctrl+AB", "Ctrl+!", "Ctrl+é", "Ctrl+F1_0", "Ctrl+Fx", "Ctrl+X5"])
def test_bad_key_rejected(spec):
    with pytest.raises(ValueError):
        parse_hotkey(spec)


def test_non_ascii_alnum_key_rejected():
    # 'é'.isalnum() 为真，只有 isascii() 能挡住
    with pytest.raises(ValueError):
        parse_hotkey("Ctrl+é")


def test_f_key_range():
    assert parse_hotkey("Ctrl+F1") == (0x2, 0x70)
    assert parse_hotkey("Ctrl+F24") == (0x2, 0x87)


def test_f25_rejected():
    with pytest.raises(ValueError):
        parse_hotkey("Ctrl+F25")


def test_f0_rejected():
    with pytest.raises(ValueError):
        parse_hotkey("Ctrl+F0")


def test_modifier_only_rejected():
    with pytest.raises(ValueError):
        parse_hotkey("Ctrl+Alt")


# ---------- register / unregister（用假的 user32 记录调用） ----------

class FakeUser32:
    def __init__(self, result=1):
        self.result = result
        self.reg = []
        self.unreg = []

    def RegisterHotKey(self, *args):
        self.reg.append(args)
        return self.result

    def UnregisterHotKey(self, *args):
        self.unreg.append(args)
        return 1


@pytest.fixture
def fake(monkeypatch):
    f = FakeUser32()
    monkeypatch.setattr(hotkey, "ctypes", SimpleNamespace(windll=SimpleNamespace(user32=f)))
    return f


def test_register_passes_norepeat_and_args(fake):
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: None, hotkey_id=0xB0F5)
    assert hk.register() is True
    assert fake.reg == [(None, 0xB0F5, 0x2 | 0x1 | 0x4000, ord("D"))]


def test_register_failure_sets_flag_false(fake):
    fake.result = 0
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: None)
    assert hk.register() is False
    assert hk.registered is False


def test_unregister_without_register_does_nothing(fake):
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: None)
    hk.unregister()
    assert fake.unreg == []


def test_unregister_after_failed_register_does_nothing(fake):
    fake.result = 0
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: None)
    hk.register()
    hk.unregister()
    assert fake.unreg == []


def test_unregister_after_register_calls_once_and_clears(fake):
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: None, hotkey_id=0xB0F6)
    hk.register()
    hk.unregister()
    assert fake.unreg == [(None, 0xB0F6)]
    assert hk.registered is False
    hk.unregister()  # 第二次不再调用
    assert len(fake.unreg) == 1


def test_default_hotkey_id():
    assert GlobalHotkey("Ctrl+Alt+D", lambda: None).id == 0xB0A1


# ---------- nativeEventFilter ----------

def _msg(message, wparam):
    m = wintypes.MSG()
    m.message = message
    m.wParam = wparam
    return m


def test_filter_ignores_other_message_type_with_own_id():
    hits = []
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: hits.append(1), hotkey_id=0xB0F3)
    m = _msg(0x0100, 0xB0F3)  # WM_KEYDOWN，wParam 恰好等于自己的 id
    assert hk.nativeEventFilter(TYPE, ctypes.addressof(m)) == (False, 0)
    assert hits == []


def test_filter_ignores_other_event_type():
    hits = []
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: hits.append(1), hotkey_id=0xB0F3)
    m = _msg(WM_HOTKEY, 0xB0F3)
    assert hk.nativeEventFilter(QByteArray(b"xcb_generic_event_t"), ctypes.addressof(m)) == (False, 0)
    assert hits == []


def test_filter_own_id_returns_true_zero():
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: None, hotkey_id=0xB0F3)
    m = _msg(WM_HOTKEY, 0xB0F3)
    assert hk.nativeEventFilter(TYPE, ctypes.addressof(m)) == (True, 0)


def test_wm_hotkey_constant():
    assert WM_HOTKEY == 0x0312


def test_filter_receives_real_thread_message(qapp):
    """把 WM_HOTKEY 真的投进本线程消息队列，确认 Qt 会把它交给已安装的过滤器。"""
    hits = []
    hk = GlobalHotkey("Ctrl+Alt+D", lambda: hits.append(1), hotkey_id=0xB0F7)
    qapp.installNativeEventFilter(hk)
    try:
        tid = ctypes.windll.kernel32.GetCurrentThreadId()
        assert ctypes.windll.user32.PostThreadMessageW(tid, WM_HOTKEY, 0xB0F7, 0)
        qapp.processEvents()
    finally:
        qapp.removeNativeEventFilter(hk)
    assert hits == [1]

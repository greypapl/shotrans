"""全局快捷键：Windows RegisterHotKey + Qt 原生事件过滤器。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}


def parse_hotkey(spec: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in spec.split("+")]
    bare_f = len(parts) == 1 and parts[0].startswith("f") and parts[0][1:].isdigit()  # 单独的 F1~F24 允许不带修饰键
    if (len(parts) < 2 and not bare_f) or not all(parts):
        raise ValueError(f"快捷键至少要一个修饰键加一个键（F1~F24 除外）：{spec!r}")
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            raise ValueError(f"不认识的修饰键：{p}")
        mods |= _MODS[p]
    key = parts[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        return mods, ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return mods, 0x70 + int(key[1:]) - 1
    raise ValueError(f"不认识的键：{key}")


class GlobalHotkey(QAbstractNativeEventFilter):
    def __init__(self, spec: str, callback: Callable[[], None], hotkey_id: int = 0xB0A1):
        super().__init__()
        self.mods, self.vk = parse_hotkey(spec)
        self.callback = callback
        self.id = hotkey_id
        self.registered = False

    def register(self) -> bool:
        ok = ctypes.windll.user32.RegisterHotKey(None, self.id, self.mods | MOD_NOREPEAT, self.vk)
        self.registered = bool(ok)
        return self.registered

    def unregister(self) -> None:
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.id)
            self.registered = False

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.id:
                self.callback()
                return True, 0
        return False, 0


PROBE_ID = 0xB0A4  # 设置窗口、引导里检查「快捷键是否被占用」用的临时 id（自检用 0xB0A2）


def try_register(spec: str, hotkey_id: int = PROBE_ID) -> bool:
    """试注册一下，成功立刻注销：只回答「现在能不能用」。spec 不合法抛 ValueError。"""
    mods, vk = parse_hotkey(spec)
    user32 = ctypes.windll.user32
    if user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
        user32.UnregisterHotKey(None, hotkey_id)
        return True
    return False

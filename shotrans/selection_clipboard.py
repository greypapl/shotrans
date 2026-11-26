"""备份可复制的 Windows 剪贴板格式，在同一把系统锁内读取并恢复。"""
from __future__ import annotations

import ctypes
import logging
from contextlib import contextmanager
from ctypes import wintypes

from .selection import ClipboardBusy, _bind

log = logging.getLogger(__name__)
_UNICODE_TEXT = 13
_MOVEABLE = 2
_DISPLAY_FORMATS = {0x82: 2, 0x83: 3, 0x8E: 14}


class _Medium(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("handle", wintypes.HANDLE), ("release", ctypes.c_void_p)]


class WinClipboard:
    def __init__(self, owner_window):
        self._owner_window = owner_window
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        ole = ctypes.WinDLL("ole32", use_last_error=True)
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        handle, hwnd, uint = wintypes.HANDLE, wintypes.HWND, wintypes.UINT
        self._open = _bind(user, "OpenClipboard", wintypes.BOOL, hwnd)
        self._close = _bind(user, "CloseClipboard", wintypes.BOOL)
        self._empty = _bind(user, "EmptyClipboard", wintypes.BOOL)
        self._get = _bind(user, "GetClipboardData", handle, uint)
        self._put = _bind(user, "SetClipboardData", handle, uint, handle)
        self._next = _bind(user, "EnumClipboardFormats", uint, uint)
        self._owner = _bind(user, "GetClipboardOwner", hwnd)
        self._pid = _bind(user, "GetWindowThreadProcessId", wintypes.DWORD, hwnd, ctypes.POINTER(wintypes.DWORD))
        self.sequence = _bind(user, "GetClipboardSequenceNumber", wintypes.DWORD)
        self._alloc = _bind(kernel, "GlobalAlloc", handle, uint, ctypes.c_size_t)
        self._lock_memory = _bind(kernel, "GlobalLock", ctypes.c_void_p, handle)
        self._unlock_memory = _bind(kernel, "GlobalUnlock", wintypes.BOOL, handle)
        self._size = _bind(kernel, "GlobalSize", ctypes.c_size_t, handle)
        self._duplicate = _bind(ole, "OleDuplicateData", handle, handle, wintypes.WORD, uint)
        self._release = _bind(ole, "ReleaseStgMedium", None, ctypes.POINTER(_Medium))
        self._copy_emf = _bind(gdi, "CopyEnhMetaFileW", handle, handle, wintypes.LPCWSTR)
        register = _bind(user, "RegisterClipboardFormatW", uint, wintypes.LPCWSTR)
        self._history = register("ExcludeClipboardContentFromMonitorProcessing")
        # 这些是 OLE 用于找回原 IDataObject 的内部索引，不能与已独立复制的格式一起恢复。
        self._ole_metadata = frozenset(register(name) for name in ("Ole Private Data", "DataObject"))

    @contextmanager
    def _locked(self):
        if not self._open(self._owner_window()):
            raise ClipboardBusy()
        try:
            yield
        finally:
            self._close()

    def snapshot(self) -> tuple[int, dict[int, int]]:
        backup = {}
        try:
            with self._locked():
                fmt = 0
                while True:
                    ctypes.set_last_error(0)
                    fmt = self._next(fmt)
                    if not fmt:
                        if ctypes.get_last_error():
                            raise ctypes.WinError(ctypes.get_last_error())
                        break
                    if fmt in self._ole_metadata or fmt == self._history:
                        continue
                    if fmt == 0x80 or 0x0200 <= fmt <= 0x02FF:
                        raise RuntimeError("剪贴板格式依赖原窗口")
                    data = self._get(fmt)
                    canonical = _DISPLAY_FORMATS.get(fmt, fmt)
                    if not data:
                        raise RuntimeError("剪贴板格式不可读取")
                    copied = (self._copy_emf(data, None) if canonical == 14
                              else self._duplicate(data, canonical, _MOVEABLE))
                    if not copied:
                        raise RuntimeError("剪贴板格式不可备份")
                    backup[fmt] = copied
                # 延迟格式可能在读取时增加序号，保存完整快照之后再取基准。
                return self.sequence(), backup
        except Exception:
            self.discard(backup)
            raise

    def take_and_restore(self, before: int, target_pid: int, backup: dict[int, int]) -> tuple[bool, str | None]:
        with self._locked():
            if self.sequence() == before:
                return False, None
            owner = self._owner()
            pid = wintypes.DWORD()
            if not owner or not self._pid(owner, ctypes.byref(pid)) or pid.value != target_pid:
                return True, None
            try:
                return True, self._text()
            finally:
                self._restore(backup)

    def _text(self) -> str:
        handle = self._get(_UNICODE_TEXT)
        address = self._lock_memory(handle) if handle else None
        if not address:
            return ""
        try:
            size = self._size(handle)
            return ctypes.wstring_at(address, size // ctypes.sizeof(ctypes.c_wchar)).split("\0", 1)[0]
        finally:
            self._unlock_memory(handle)

    def _restore(self, backup: dict[int, int]) -> None:
        marker = self._alloc(_MOVEABLE | 0x0040, 4) if backup and self._history else None
        try:
            if not self._empty():
                raise ctypes.WinError(ctypes.get_last_error())
            if marker and self._put(self._history, marker):
                marker = None  # 系统接管句柄。
            for fmt, handle in list(backup.items()):
                if self._put(fmt, handle):
                    del backup[fmt]
                else:
                    log.warning("恢复剪贴板格式 %s 失败", fmt)
        finally:
            if marker:
                self.discard({self._history: marker})

    def discard(self, backup: dict[int, int]) -> None:
        for fmt, handle in backup.items():
            canonical = _DISPLAY_FORMATS.get(fmt, fmt)
            kind = {2: 16, 3: 32, 9: 16, 14: 64}.get(canonical, 1)
            self._release(ctypes.byref(_Medium(kind, handle, None)))
        backup.clear()

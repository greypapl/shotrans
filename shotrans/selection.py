"""划词手势、Win32 鼠标监听与剪贴板取字。"""
from __future__ import annotations

import ctypes
import logging
import ntpath
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QEventLoop, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication

from .config import Config
from .terminal_detect import standalone_terminal

log = logging.getLogger(__name__)
_SHELL_CLASSES = frozenset({"cabinetwclass", "progman", "workerw"})
_KEYUP = 0x0002
_EXTENDED = 0x0001
_MODIFIERS = ((0xA0, 0), (0xA1, 0), (0xA4, 0), (0xA5, _EXTENDED),
              (0x5B, _EXTENDED), (0x5C, _EXTENDED))
_COPY_TAG = 0x53485452


class ClipboardBusy(Exception):
    """另一个窗口暂时占用了剪贴板。"""


class SelectionGesture:
    def __init__(self, double_click_ms: int):
        self.double_click_ms = double_click_ms
        self.reset()

    def reset(self) -> None:
        self._down = None
        self._last_click = None

    def feed(self, event: str, x: int, y: int, when: int) -> bool:
        if event == "left_down":
            self._down = x, y
            return False
        if event != "left_up":
            self.reset()
            return False
        down, self._down = self._down, None
        if down is None:
            return False
        if (x - down[0]) ** 2 + (y - down[1]) ** 2 >= 8 ** 2:
            self._last_click = None
            return True
        last, self._last_click = self._last_click, (x, y, when)
        # MSLLHOOKSTRUCT.time 是 32 位毫秒时钟，每约 49.7 天回绕。
        return bool(last and (when - last[2]) % 2**32 <= self.double_click_ms
                    and max(abs(x - last[0]), abs(y - last[1])) <= 4)


@dataclass(frozen=True)
class ForegroundInfo:
    hwnd: int
    pid: int = 0
    process: str = ""
    window_class: str = ""
    fullscreen: bool = False
    own: bool = False
    focus: int = 0

    @property
    def target(self) -> tuple[int, int, int]:
        return self.hwnd, self.pid, self.focus


def should_offer(info: ForegroundInfo, config: Config, *, busy: bool = False, modal: bool = False) -> bool:
    if not config.select_enabled or busy or modal or not info.hwnd or not info.pid or not info.process:
        return False
    if info.own or info.fullscreen or info.window_class.casefold() in _SHELL_CLASSES:
        return False
    if standalone_terminal(info.window_class, info.process):
        return False
    return info.process.casefold() not in {name.strip().casefold() for name in config.select_exclude}


_LRESULT = ctypes.c_ssize_t
_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class _MouseEvent(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KeyEvent(ctypes.Structure):
    _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD)] + [
        (field, wintypes.HWND) for field in (
            "hwndActive", "hwndFocus", "hwndCapture", "hwndMenuOwner", "hwndMoveSize", "hwndCaret")
    ] + [("rcCaret", wintypes.RECT)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT),
                ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32)]


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _KeyInput(ctypes.Structure):
    _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _InputValue(ctypes.Union):
    _fields_ = [("mouse", _MouseInput), ("key", _KeyInput)]  # mouse 决定联合体大小及对齐。


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", _InputValue)]


def _bind(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn


class SelectionWindows:
    """与操作系统交互的薄层，保留原生像素和指针大小。"""

    def __init__(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        hwnd, dword, boolean = wintypes.HWND, wintypes.DWORD, wintypes.BOOL
        ptr = ctypes.POINTER
        self.set_hook = _bind(user, "SetWindowsHookExW", wintypes.HHOOK,
                              ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, dword)
        self.unhook = _bind(user, "UnhookWindowsHookEx", boolean, wintypes.HHOOK)
        self.next_hook = _bind(user, "CallNextHookEx", _LRESULT, wintypes.HHOOK,
                               ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        self.module = _bind(kernel, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
        self.double_click_time = _bind(user, "GetDoubleClickTime", wintypes.UINT)
        self._foreground = _bind(user, "GetForegroundWindow", hwnd)
        self._window_pid = _bind(user, "GetWindowThreadProcessId", dword, hwnd, ptr(dword))
        self._gui_thread = _bind(user, "GetGUIThreadInfo", boolean, dword, ptr(_GuiThreadInfo))
        self._class = _bind(user, "GetClassNameW", ctypes.c_int, hwnd, wintypes.LPWSTR, ctypes.c_int)
        self._rect = _bind(user, "GetWindowRect", boolean, hwnd, ptr(wintypes.RECT))
        self._get_style = _bind(user, "GetWindowLongW", wintypes.LONG, hwnd, ctypes.c_int)
        self._set_style = _bind(user, "SetWindowLongW", wintypes.LONG, hwnd, ctypes.c_int, wintypes.LONG)
        self._window_at = _bind(user, "WindowFromPoint", hwnd, wintypes.POINT)
        self._ancestor = _bind(user, "GetAncestor", hwnd, hwnd, wintypes.UINT)
        self._window_monitor = _bind(user, "MonitorFromWindow", wintypes.HMONITOR, hwnd, dword)
        self._point_monitor = _bind(user, "MonitorFromPoint", wintypes.HMONITOR, wintypes.POINT, dword)
        self._monitor_info = _bind(user, "GetMonitorInfoW", boolean, wintypes.HMONITOR, ptr(_MonitorInfo))
        self._open_process = _bind(kernel, "OpenProcess", wintypes.HANDLE, dword, boolean, dword)
        self._image_name = _bind(kernel, "QueryFullProcessImageNameW", boolean,
                                 wintypes.HANDLE, dword, wintypes.LPWSTR, ptr(dword))
        self._close = _bind(kernel, "CloseHandle", boolean, wintypes.HANDLE)
        self._key_state = _bind(user, "GetAsyncKeyState", wintypes.SHORT, ctypes.c_int)
        self._send = _bind(user, "SendInput", wintypes.UINT, wintypes.UINT, ptr(_Input), ctypes.c_int)

    def _process_name(self, pid: int) -> str:
        process = self._open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return ""
        try:
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path))
            return ntpath.basename(path.value) if self._image_name(process, 0, path, ctypes.byref(size)) else ""
        finally:
            self._close(process)

    def foreground(self) -> ForegroundInfo:
        hwnd = self._foreground()
        if not hwnd:
            return ForegroundInfo(0)
        pid = wintypes.DWORD()
        thread = self._window_pid(hwnd, ctypes.byref(pid))
        gui = _GuiThreadInfo(ctypes.sizeof(_GuiThreadInfo))
        focus = 0
        if thread and self._gui_thread(thread, ctypes.byref(gui)):
            focus = gui.hwndFocus or 0
        window_class = ctypes.create_unicode_buffer(256)
        self._class(hwnd, window_class, len(window_class))
        own = pid.value == os.getpid()
        return ForegroundInfo(hwnd, pid.value, self._process_name(pid.value) if not own else "",
                              window_class.value, self._fullscreen(hwnd), own, focus)

    def root_at(self, x: int, y: int) -> int:
        window = self._window_at(wintypes.POINT(x, y))
        return (self._ancestor(window, 2) or window or 0) if window else 0  # GA_ROOT

    def _fullscreen(self, hwnd: int) -> bool:
        if self._get_style(hwnd, -16) & 0x00C00000 == 0x00C00000:  # WS_CAPTION
            return False
        monitor = self._monitor(self._window_monitor(hwnd, 2))
        rect = wintypes.RECT()
        if monitor is None or not self._rect(hwnd, ctypes.byref(rect)):
            return False
        return all(getattr(rect, edge) == getattr(monitor.monitor, edge)
                   for edge in ("left", "top", "right", "bottom"))

    def _monitor(self, handle) -> _MonitorInfo | None:
        info = _MonitorInfo(ctypes.sizeof(_MonitorInfo))
        if handle and self._monitor_info(handle, ctypes.byref(info)):
            return info
        return None

    def logical_point(self, x: int, y: int) -> QPoint:
        monitor = self._monitor(self._point_monitor(wintypes.POINT(x, y), 2))
        if monitor is not None:
            for screen in QGuiApplication.screens():
                if screen.name().casefold() == monitor.device.casefold():
                    scale = screen.devicePixelRatio()
                    offset = QPoint(round((x - monitor.monitor.left) / scale),
                                    round((y - monitor.monitor.top) / scale))
                    return screen.geometry().topLeft() + offset
        return QCursor.pos()

    def no_activate(self, hwnd: int) -> None:
        self._set_style(hwnd, -20, self._get_style(hwnd, -20) | 0x08000000)  # WS_EX_NOACTIVATE

    def copy_chord_down(self, key: int) -> bool:
        return key in (ord("C"), ord("X"), 0x2D) and bool(self._key_state(0x11) & 0x8000)

    def send_copy(self) -> bool:
        keys = copy_keys(self._key_state)
        inputs = (_Input * len(keys))(*(_Input(1, _InputValue(key=_KeyInput(vk, 0, flags, 0, _COPY_TAG)))
                                       for vk, flags in keys))
        return self._send(len(inputs), inputs, ctypes.sizeof(_Input)) == len(inputs)


def copy_keys(key_state) -> list[tuple[int, int]]:
    released = [(key, flags | _KEYUP) for key, flags in _MODIFIERS if key_state(key) & 0x8000]
    return released + [(0x11, 0), (ord("C"), 0), (ord("C"), _KEYUP), (0x11, _KEYUP)]


_MOUSE_MESSAGES = {
    0x0201: "left_down", 0x0202: "left_up", 0x0204: "right_down",
    0x0207: "middle_down", 0x020B: "extra_down",
}


class MouseHook:
    def __init__(self, windows: SelectionWindows, record):
        self._windows, self._record = windows, record
        self._handle = None
        self._callback = _HOOKPROC(self._on_event)

    def install(self) -> bool:
        if self._handle is None:
            self._handle = self._windows.set_hook(14, self._callback, self._windows.module(None), 0)
        return bool(self._handle)

    def uninstall(self) -> None:
        if self._handle and self._windows.unhook(self._handle):
            self._handle = None

    def _on_event(self, code, message, data):
        try:
            event = _MOUSE_MESSAGES.get(message) if code == 0 else None
            if event:
                raw = _MouseEvent.from_address(data)
                self._record(event, raw.pt.x, raw.pt.y, raw.time)
        except Exception:
            log.exception("鼠标事件记录失败")
        return self._windows.next_hook(None, code, message, data)


class CopyKeyGuard:
    """只在复制事务内识别外部复制/剪切快捷键，避免覆盖同一应用的新内容。"""

    def __init__(self, windows: SelectionWindows):
        self._windows = windows
        self._handle = None
        self._callback = _HOOKPROC(self._on_event)
        self.interrupted = False

    def start(self) -> bool:
        self.interrupted = False
        if self._handle is None:
            self._handle = self._windows.set_hook(13, self._callback, self._windows.module(None), 0)
        return bool(self._handle)

    def stop(self) -> None:
        if self._handle and self._windows.unhook(self._handle):
            self._handle = None

    def _on_event(self, code, message, data):
        if code == 0 and message in (0x0100, 0x0104):
            key = _KeyEvent.from_address(data)
            if key.extra != _COPY_TAG and self._windows.copy_chord_down(key.vk):
                self.interrupted = True
        return self._windows.next_hook(None, code, message, data)


class SelectionGrabber(QObject):
    """在 Qt 线程等待复制完成，备份与恢复由剪贴板适配器实现。"""

    def __init__(self, clipboard, send_copy, parent=None, *, key_guard=None):
        super().__init__(parent)
        self._clipboard, self._send_copy = clipboard, send_copy
        self._guard = key_guard
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._poll)
        self._pending = None
        self._prepared = None
        self._stopping = None
        self._when_idle = None

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def prepare(self) -> bool:
        if self.busy:
            return False
        if self._prepared is None:
            try:
                self._prepared = self._clipboard.snapshot()
            except Exception:
                log.debug("无法备份剪贴板", exc_info=True)
        return self._prepared is not None

    def cancel_preparation(self) -> None:
        if self._prepared is not None:
            _, backup = self._prepared
            self._prepared = None
            self._clipboard.discard(backup)

    def start(self, target_pid: int, can_copy, done) -> None:
        if not self.prepare():
            done(None)
            return
        before, backup = self._prepared
        try:
            guard_ready = self._guard is None or self._guard.start()
            if not guard_ready or not can_copy() or self._clipboard.sequence() != before or not self._send_copy():
                self.cancel_preparation()
                if self._guard is not None:
                    self._guard.stop()
                done(None)
                return
        except Exception:
            self.cancel_preparation()
            if self._guard is not None:
                self._guard.stop()
            log.debug("无法发送复制", exc_info=True)
            done(None)
            return
        self._prepared = None
        self._pending = before, backup, target_pid, time.monotonic() + 0.500, done
        self._timer.start()

    def _poll(self) -> None:
        if self._pending is None:
            return
        before, backup, pid, deadline, _ = self._pending
        try:
            if self._guard is not None and self._guard.interrupted:
                self._finish(None)
                return
            changed, text = self._clipboard.take_and_restore(before, pid, backup)
            if changed:
                self._finish(text if text and text.strip() else None)
                return
        except ClipboardBusy:
            pass  # 让源程序先完成写入，再在下一轮尝试。
        except Exception:
            log.debug("读取或恢复剪贴板失败", exc_info=True)
            self._finish(None)
            return
        if time.monotonic() >= deadline:
            self._finish(None)

    def shutdown(self) -> None:
        self.cancel_preparation()
        if self.busy:
            self._poll()
        if self.busy:
            self._stopping = QEventLoop(self)
            self._stopping.exec()
            self._stopping = None

    def when_idle(self, callback) -> None:
        if self.busy:
            self._when_idle = callback
        else:
            callback()

    def _finish(self, text: str | None) -> None:
        _, backup, _, _, done = self._pending
        self._pending = None
        self._timer.stop()
        if self._guard is not None:
            self._guard.stop()
        self._clipboard.discard(backup)
        if self._stopping is not None:
            self._stopping.quit()
        callback, self._when_idle = self._when_idle, None
        try:
            done(text)
        finally:
            if callback is not None:
                callback()

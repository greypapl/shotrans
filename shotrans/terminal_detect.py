"""终端分类和有时间上限的焦点控件查询。"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from concurrent.futures import Future
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, QTimer

log = logging.getLogger(__name__)

TERMINAL_CLASSES = frozenset({"consolewindowclass", "cascadia_hosting_window_class", "mintty",
                              "putty", "virtualconsoleclass"})
TERMINAL_PROCESSES = frozenset({
    "putty.exe", "kitty.exe", "mintty.exe", "conemu.exe", "conemu64.exe", "alacritty.exe",
    "wezterm-gui.exe", "tabby.exe", "hyper.exe", "windowsterminal.exe", "openconsole.exe",
    "cmd.exe", "powershell.exe", "pwsh.exe", "wsl.exe", "mobaxterm.exe", "xshell.exe", "termius.exe",
})
_XTERM_EDITORS = frozenset({"code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe"})
_JETBRAINS_EDITORS = frozenset({
    "idea64.exe", "pycharm64.exe", "webstorm64.exe", "clion64.exe", "goland64.exe", "rider64.exe",
    "datagrip64.exe", "phpstorm64.exe", "rubymine64.exe", "studio64.exe",
})
_VISUAL_STUDIO = "devenv.exe"


@dataclass(frozen=True)
class ControlInfo:
    class_name: str = ""
    name: str = ""
    automation_id: str = ""


def standalone_terminal(window_class: str, process: str) -> bool:
    return window_class.casefold() in TERMINAL_CLASSES or process.casefold() in TERMINAL_PROCESSES


def terminal_editor(process: str) -> bool:
    process = process.casefold()
    return process in _XTERM_EDITORS or process in _JETBRAINS_EDITORS or process == _VISUAL_STUDIO


def embedded_terminal(process: str, controls: list[ControlInfo]) -> bool:
    process = process.casefold()
    controls = controls[:6]
    if process in _XTERM_EDITORS:
        return any("xterm" in item.class_name.casefold() for item in controls)
    if process in _JETBRAINS_EDITORS:
        return any("terminal" in item.class_name.casefold() or "terminal" in item.name.casefold()
                   for item in controls)
    if process == _VISUAL_STUDIO:
        return any("terminal" in item.name.casefold() or "terminal" in item.automation_id.casefold()
                   for item in controls)
    return False


def create_automation():
    from comtypes.client import CreateObject, GetModule

    uia = GetModule("UIAutomationCore.dll")
    return CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)


def focused_controls(pid: int, hwnd: int) -> list[ControlInfo]:
    """只在无窗口的 COM 工作线程运行，返回普通 Python 对象。"""
    from comtypes import CoInitializeEx, CoUninitialize, COINIT_MULTITHREADED

    CoInitializeEx(COINIT_MULTITHREADED)
    try:
        return _read_controls(pid, hwnd)
    finally:
        CoUninitialize()


def _read_controls(pid: int, hwnd: int) -> list[ControlInfo]:
    automation = create_automation()
    element = automation.GetFocusedElement()
    if not element:
        return []
    root = ctypes.windll.user32.GetAncestor
    root.restype = wintypes.HWND
    root.argtypes = (wintypes.HWND, wintypes.UINT)
    walker = automation.RawViewWalker
    controls = []
    belongs = False
    for depth in range(32):
        if not element or (depth >= 6 and belongs):
            break
        if not belongs:
            native = element.CurrentNativeWindowHandle
            belongs = element.CurrentProcessId == pid or bool(hwnd and native and root(native, 2) == hwnd)
        if depth < 6:
            controls.append(ControlInfo(element.CurrentClassName or "", element.CurrentName or "",
                                        element.CurrentAutomationId or ""))
        if depth < 5 or not belongs:
            element = walker.GetParentElement(element)
    return controls if belongs else []


class TerminalProbe(QObject):
    """一次只向 UIA 发一个调用。挂起的提供方不会累积后台线程。"""

    def __init__(self, parent=None, query=None):
        super().__init__(parent)
        self._query = query
        self._running: Future | None = None
        self._active_query: Future | None = None
        self._request = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._poll)

    def check(self, process: str, pid: int, done, *, hwnd: int = 0) -> None:
        self.cancel()
        if not terminal_editor(process):
            done(False)
            return
        if self._query is None:
            try:
                import comtypes  # 首次导入初始化本线程，工作线程再单独选 MTA。
            except (ImportError, OSError):
                log.debug("加载 comtypes 失败", exc_info=True)
                done(False)
                return
        self._request = process, pid, hwnd, done, time.monotonic() + 0.300
        self._poll()
        if self._request is not None:
            self._timer.start()

    def cancel(self) -> None:
        self._request = None
        self._active_query = None
        self._timer.stop()

    def _poll(self) -> None:
        if self._request is None:
            return
        process, pid, hwnd, done, deadline = self._request
        if time.monotonic() >= deadline:
            self.cancel()
            done(False)
            return
        if self._running is not None and not self._running.done():
            return
        if self._running is not None and self._running is self._active_query:
            controls = self._running.result()
            self.cancel()
            done(embedded_terminal(process, controls))
            return
        future = self._running = self._active_query = Future()
        thread = threading.Thread(target=self._read, args=(pid, hwnd, future), name="shotrans-uia", daemon=True)
        thread.start()

    def _read(self, pid: int, hwnd: int, future: Future) -> None:
        try:
            controls = focused_controls(pid, hwnd) if self._query is None else self._query(pid)
        except Exception:
            log.debug("读取焦点控件失败", exc_info=True)
            controls = []
        future.set_result(controls)

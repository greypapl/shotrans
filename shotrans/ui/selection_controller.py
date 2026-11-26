"""连接鼠标手势、前台验证、小图标和翻译入口。"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QPoint, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication

from ..selection import CopyKeyGuard, ForegroundInfo, MouseHook, SelectionGesture, SelectionGrabber, SelectionWindows
from ..selection import should_offer
from ..selection_clipboard import WinClipboard
from ..terminal_detect import TerminalProbe
from .select_icon import SelectIcon, show_failure

log = logging.getLogger(__name__)


class SelectionController(QObject):
    _mouse_event = Signal(str, int, int, object)

    def __init__(self, app, *, windows=None, probe=None, grabber=None):
        super().__init__(app)
        self._app = app
        self._windows = windows or SelectionWindows()
        self._gesture = SelectionGesture(self._windows.double_click_time())
        self._hook = MouseHook(self._windows, self._mouse_event.emit)
        self._mouse_event.connect(self._on_mouse, Qt.ConnectionType.QueuedConnection)
        self._probe = probe or TerminalProbe(self)
        self._icon = None
        self._grabber = grabber or SelectionGrabber(
            WinClipboard(lambda: int(self._icon.winId())), self._windows.send_copy, self,
            key_guard=CopyKeyGuard(self._windows),
        )
        self._enabled = False
        self._offered: ForegroundInfo | None = None
        self._down_window = 0

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._enabled = self._hook.install()
            if not self._enabled:
                log.warning("安装划词鼠标钩子失败")
        else:
            self._enabled = False
            self._hook.uninstall()
            self.dismiss()
            self._gesture.reset()
            self._down_window = 0

    def dismiss(self) -> None:
        self._probe.cancel()
        self._grabber.cancel_preparation()
        self._offered = None
        if self._icon is not None:
            self._icon.dismiss()

    def shutdown(self) -> None:
        self.set_enabled(False)
        self._grabber.shutdown()
        if self._icon is not None:
            self._icon.close()
            self._icon.deleteLater()
            self._icon = None

    def finish_then(self, callback) -> None:
        self.set_enabled(False)
        self._grabber.when_idle(callback)

    def _allowed(self, info: ForegroundInfo) -> bool:
        return (self._enabled and
                should_offer(info, self._app.config, busy=self._app.capturer.busy,
                             modal=QApplication.activeModalWidget() is not None))

    def _still_allowed(self, original: ForegroundInfo) -> bool:
        current = self._windows.foreground()
        return original.target == current.target and self._allowed(current)

    @Slot(str, int, int, object)
    def _on_mouse(self, event: str, x: int, y: int, when: int) -> None:
        if not self._enabled:
            return
        if event.endswith("_down"):
            point = self._windows.logical_point(x, y) if self._icon and self._icon.isVisible() else None
            if point is not None and self._icon.contains(point):
                self._gesture.reset()
                return
            self.dismiss()
            root = self._windows.root_at(x, y)
            if root != self._down_window:
                self._gesture.reset()
            self._down_window = root
        if not self._gesture.feed(event, x, y, when) or self._grabber.busy:
            return
        info = self._windows.foreground()
        if info.hwnd != self._down_window or not self._allowed(info):
            self._gesture.reset()
            return
        pos = self._windows.logical_point(x, y)
        self._probe.check(info.process, info.pid, lambda terminal: self._show(info, pos, terminal), hwnd=info.hwnd)

    def _show(self, info: ForegroundInfo, pos: QPoint, terminal: bool) -> None:
        if terminal or not self._still_allowed(info):
            return
        if self._icon is None:
            icon = self._icon = SelectIcon(self._windows.no_activate)
            icon.selected.connect(self._select)
            icon.excluded.connect(self._app.exclude_selection_app)
            icon.disabled.connect(lambda: self._app.update_select(enabled=False))
        self._offered = info
        self._icon.offer(pos, info.process)

    def _select(self, pos: QPoint) -> None:
        info = self._offered
        self.dismiss()
        if info is None or not self._still_allowed(info):
            return
        if self._app.needs_setup:
            self._app.open_window("wizard")
            return
        if not self._grabber.prepare():
            self._complete(pos, None)
            return
        # 备份延迟格式可能耗时，在备份之后检查当前焦点再发键。
        self._probe.check(info.process, info.pid, lambda terminal: self._copy(info, pos, terminal), hwnd=info.hwnd)

    def _copy(self, info: ForegroundInfo, pos: QPoint, terminal: bool) -> None:
        if terminal or not self._still_allowed(info):
            self._grabber.cancel_preparation()
            return
        self._grabber.start(info.pid, lambda: self._still_allowed(info),
                            lambda text: self._complete(pos, text))

    def _complete(self, pos: QPoint, text: str | None) -> None:
        if not self._enabled or self._app.capturer.busy or QApplication.activeModalWidget() is not None:
            return
        if text is None:
            show_failure(pos)
        else:
            self._app.translate_text(text, pos)

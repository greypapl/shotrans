"""每种窗口只开一个：再次打开时激活已有窗口（可带页面参数，例如设置窗口的「快捷键」页）。

窗口必须用 close() 关闭，不要用 hide()：close() 触发 WA_DeleteOnClose 删除窗口、取消标志和定时器跟着收尾；
只 hide() 的窗口会被 get() 当作「没开」，下次打开时另建一个新窗口，旧的那个就一直留在内存里。"""
from __future__ import annotations

import itertools
from typing import Callable

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget


class WindowManager:
    def __init__(self, factories: dict[str, Callable[[str | None], QWidget]]):
        self._factories = dict(factories)
        self._open: dict[str, tuple[int, QWidget]] = {}
        self._tokens = itertools.count(1)

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._factories)

    def get(self, kind: str) -> QWidget | None:
        item = self._open.get(kind)
        if item is None:
            return None
        w = item[1]
        if not shiboken6.isValid(w) or not w.isVisible():  # 关掉了（等待删除中）也不算开着
            self._open.pop(kind, None)
            return None
        return w

    def show(self, kind: str, page: str | None = None) -> QWidget:
        if kind not in self._factories:
            raise KeyError(kind)
        w = self.get(kind)
        if w is None:
            w = self._factories[kind](page)
            w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            token = next(self._tokens)
            self._open[kind] = (token, w)
            w.destroyed.connect(lambda *_, k=kind, tk=token: self._forget(k, tk))
        elif page is not None and hasattr(w, "show_page"):
            w.show_page(page)
        w.show()
        w.setWindowState(w.windowState() & ~Qt.WindowState.WindowMinimized)
        w.raise_()
        w.activateWindow()
        return w

    def _forget(self, kind: str, token: int) -> None:
        item = self._open.get(kind)
        if item is not None and item[0] == token:  # 旧窗口晚到的 destroyed 不能把新窗口忘掉
            self._open.pop(kind, None)

    def close_all(self) -> None:
        for _, w in list(self._open.values()):
            if shiboken6.isValid(w):
                w.close()
        self._open.clear()

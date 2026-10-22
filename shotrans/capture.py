"""冻结全屏 + 框选。每块屏幕一个框选窗口，一次框选限定在一块屏幕内。"""
from __future__ import annotations

import logging
from typing import Callable

from PIL import Image
from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .qtimage import qimage_to_pil


log = logging.getLogger(__name__)


def _phys_rect(rect: QRect, dpr: float) -> QRect:
    """逻辑矩形 → 物理像素矩形：四条边各自取整，宽高用差值，避免小数 DPR 下取整漂移。"""
    left, top = round(rect.x() * dpr), round(rect.y() * dpr)
    right, bottom = round((rect.x() + rect.width()) * dpr), round((rect.y() + rect.height()) * dpr)
    return QRect(left, top, right - left, bottom - top)


def crop_to_image(pix: QPixmap, rect: QRect) -> Image.Image:
    phys = _phys_rect(rect, pix.devicePixelRatio())
    return qimage_to_pil(pix.copy(phys).toImage())


class SelectionWindow(QWidget):
    selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self, pixmap: QPixmap, geometry: QRect, screen=None):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        super().__init__(None, flags)
        self.pixmap = pixmap
        if screen is not None:
            self.setScreen(screen)  # 先指定屏幕再设几何，混合 DPI 副屏才放得对
        self.setGeometry(geometry)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self._start: QPoint | None = None
        self._end: QPoint | None = None

    def _rect(self) -> QRect:
        if self._start is None or self._end is None:
            return QRect()
        x0, y0 = min(self._start.x(), self._end.x()), min(self._start.y(), self._end.y())
        return QRect(x0, y0, abs(self._end.x() - self._start.x()), abs(self._end.y() - self._start.y()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.pixmap)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        r = self._rect()
        if not r.isEmpty():
            src = _phys_rect(r, self.pixmap.devicePixelRatio())
            p.drawPixmap(r, self.pixmap, src)
            p.setPen(QPen(QColor("#3b82f6"), 2))
            p.drawRect(r)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._start = self._end = e.position().toPoint()
            self.update()
        elif e.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()

    def mouseMoveEvent(self, e):
        if self._start is not None:
            self._end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        self._end = e.position().toPoint()
        r = self._rect()
        self._start = self._end = None
        if r.width() >= 4 and r.height() >= 4:
            self.selected.emit(r)
        else:
            self.cancelled.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()


class Capturer(QObject):
    captured = Signal(object, QRect)
    cancelled = Signal()

    def __init__(self, before_grab: Callable[[], None] | None = None,
                 after_grab: Callable[[], None] | None = None):
        super().__init__()
        self._windows: list[SelectionWindow] = []
        self._before = before_grab
        self._after = after_grab
        self._busy = False

    @property
    def busy(self) -> bool:
        """从按下快捷键（藏浮窗、等 80 毫秒、截屏）到框选结束或取消，都算正在截图。"""
        return self._busy

    def start(self) -> None:
        if self._busy:
            return
        self._busy = True
        if self._before:
            self._before()  # 先把浮窗都藏起来，等 80 毫秒让它们从屏幕上消失
        QTimer.singleShot(80, self._grab)

    def _grab(self) -> None:
        try:
            # 先把所有屏幕都截好，再统一建窗口显示，免得先显示的窗口被后截的屏幕截进去
            grabbed = [(screen, screen.grabWindow(0)) for screen in QGuiApplication.screens()]
            if not grabbed:
                raise RuntimeError("没有可用的屏幕")
            windows = []
            for screen, pix in grabbed:
                w = SelectionWindow(pix, screen.geometry(), screen)
                w.selected.connect(lambda r, w=w: self._finish(w, r))
                w.cancelled.connect(lambda: self._finish(None, None))
                windows.append(w)
            self._windows = windows
            for w in windows:
                w.show()
                w.raise_()
                w.activateWindow()
        except Exception:
            log.exception("截屏失败")
            self._finish(None, None)

    def _finish(self, win: SelectionWindow | None, local: QRect | None) -> None:
        result = None
        if win is not None and local is not None:
            result = (crop_to_image(win.pixmap, local), local.translated(win.geometry().topLeft()))
        windows, self._windows = self._windows, []
        for w in windows:
            w.close()
            w.deleteLater()
        self._busy = False
        if self._after:
            self._after()
        if result is not None:
            self.captured.emit(*result)
        else:
            self.cancelled.emit()  # 没框成（Esc、右键、太小、截屏失败）

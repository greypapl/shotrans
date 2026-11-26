"""鼠标旁边的不激活翻译按钮。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu, QToolButton, QToolTip

from ..i18n import t
from ..iconart import make_icon
from .clip_window import place_near

HIDE_MS = 3000
SIZE = 28


class SelectIcon(QToolButton):
    selected = Signal(QPoint)
    excluded = Signal(str)
    disabled = Signal()

    def __init__(self, no_activate):
        super().__init__()
        self._no_activate = no_activate
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(SIZE, SIZE)
        self.setIcon(make_icon())
        self.setIconSize(QSize(19, 19))
        self.setStyleSheet(
            "QToolButton{border:1px solid #cbd5e1;border-radius:14px;background:#f8fafc;}"
            "QToolButton:hover{background:#dbeafe;border-color:#60a5fa;}"
            "QToolButton:pressed{background:#bfdbfe;}"
        )
        self.clicked.connect(lambda: self.selected.emit(self.pos()))
        self._hide_timer = QTimer(self, singleShot=True)
        self._hide_timer.timeout.connect(self.dismiss)
        self._process = ""
        self._menu = QMenu(self)
        self._menu.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
        self._menu.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._exclude = self._menu.addAction("")
        self._disable = self._menu.addAction("")
        self._exclude.triggered.connect(self._exclude_process)
        self._disable.triggered.connect(self._disable_selection)
        self._menu.aboutToHide.connect(self._restart_timer)

    def offer(self, point: QPoint, process: str) -> None:
        self.dismiss()
        self._process = process
        self.setToolTip(t("select.tooltip"))
        self.setAccessibleName(self.toolTip())
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        self.setScreen(screen)
        self.move(place_near(point, (SIZE, SIZE), screen.availableGeometry(), gap=12))
        self._no_activate(int(self.winId()))
        self.show()
        self._restart_timer()

    def contains(self, point: QPoint) -> bool:
        return (self.isVisible() and self.frameGeometry().contains(point)
                or self._menu.isVisible() and self._menu.frameGeometry().contains(point))

    def dismiss(self) -> None:
        self._menu.hide()
        self._hide_timer.stop()
        self.hide()

    def enterEvent(self, event) -> None:
        self._hide_timer.stop()

    def leaveEvent(self, event) -> None:
        self._restart_timer()

    def contextMenuEvent(self, event) -> None:
        event.accept()
        self._open_menu()

    def _open_menu(self) -> None:
        self._hide_timer.stop()
        self._exclude.setText(t("select.exclude_app", process=self._process))
        self._disable.setText(t("select.disable"))
        self._no_activate(int(self._menu.winId()))
        self._menu.popup(self.mapToGlobal(QPoint(0, SIZE)))

    def _restart_timer(self) -> None:
        if self.isVisible() and not self.underMouse() and not self._menu.isVisible():
            self._hide_timer.start(HIDE_MS)

    def _exclude_process(self) -> None:
        self.dismiss()
        self.excluded.emit(self._process)

    def _disable_selection(self) -> None:
        self.dismiss()
        self.disabled.emit()


def show_failure(point: QPoint) -> None:
    QToolTip.showText(point, t("select.empty"))

"""程序自己画的提示小窗，替代托盘气泡（QSystemTrayIcon.showMessage）。

托盘气泡在 Windows 10/11 上走系统通知：用户关了通知、开了勿扰 / 专注助手，它就被系统直接吞掉，
「快捷键被占用」「key 解不开」「缺 OCR 语言包」这些提醒用户一个都看不到。所以改成自己画：
- 右下角（主屏可用区域，也就是任务栏上方）弹出，不抢焦点、不进任务栏和 Alt+Tab；
- 约 6 秒后自动消失，鼠标停在上面时不消失（停着时来了新一条也不消失）；× 只关掉；点其余地方 = 点气泡（发 clicked，并关掉）；
- 只有一个实例：新提醒直接替换旧的文字并重新计时（和托盘气泡一样，最新的一条为准）。
托盘程序在截图前会藏起小窗，截图 / 框选期间来的提醒推迟到结束后再弹（见 app.py notify）。
需要 QApplication 已存在。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QStyle, QSystemTrayIcon, QToolButton, QVBoxLayout,
                               QWidget)

from ..i18n import t
from ..iconart import BLUE, icon_pixmap

TIMEOUT_MS = 6000   # 自动消失时间
WIDTH = 360         # 小窗总宽（和 Windows 系统通知差不多宽）
MARGIN = 12         # 离屏幕可用区域右边、下边的距离
ICON_SIZE = 32
AMBER = "#d97706"   # 警告的强调色；提示用程序图标的蓝色
_ZWSP = "​"    # 零宽空格


def _wrappable(text: str) -> str:
    """反斜杠后面插零宽空格：Unicode 断行规则不在「\\」处断行（「/」处本来就能断），不插的话一整条 Windows
    路径（如日志位置）是一个词，在固定宽度的小窗里右半截会被切掉。只影响显示，self.message 仍是原文。"""
    return text.replace("\\", "\\" + _ZWSP)


def _kind(icon) -> str:
    return "warning" if icon in (QSystemTrayIcon.MessageIcon.Warning, QSystemTrayIcon.MessageIcon.Critical) else "info"


class NoticePopup(QWidget):
    clicked = Signal()  # 点了小窗（× 以外的地方）

    def __init__(self, timeout_ms: int = TIMEOUT_MS):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)  # 圆角外面透明
        self.timeout_ms = timeout_ms
        self.kind = "info"
        self.message = ""
        self._hovered = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet("#card { background: #ffffff; border: 1px solid #c4c9d0; border-radius: 8px; }"
                           "QLabel { color: #1f2328; background: transparent; }"
                           "QToolButton { color: #57606a; border: none; background: transparent;"
                           " font-size: 16px; padding: 0 5px; }"
                           "QToolButton:hover { color: #1f2328; background: #eaeef2; border-radius: 4px; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        self.accent = QFrame(card)  # 左边一条强调色：蓝 = 提示，琥珀 = 警告
        self.accent.setFixedWidth(4)
        self.app_icon = QLabel(card)
        self.app_icon.setPixmap(icon_pixmap(ICON_SIZE))
        self.app_icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.warn_badge = QLabel(card)  # 警告时标题前的小三角
        self.warn_badge.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(16, 16))
        self.title_label = QLabel(card)
        f = QFont(self.title_label.font())
        f.setBold(True)
        self.title_label.setFont(f)
        self.close_button = QToolButton(card)
        self.close_button.setText("×")
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.close_button.clicked.connect(self.hide)
        self.text_label = QLabel(card)
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)

        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(self.warn_badge)
        head.addWidget(self.title_label, 1)
        head.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(4)
        body.addLayout(head)
        body.addWidget(self.text_label)
        row = QHBoxLayout(card)
        row.setContentsMargins(6, 10, 8, 12)
        row.setSpacing(10)
        row.addWidget(self.accent)
        icon_box = QVBoxLayout()
        icon_box.setContentsMargins(0, 4, 0, 0)
        icon_box.addWidget(self.app_icon)
        icon_box.addStretch(1)
        row.addLayout(icon_box)
        body.setContentsMargins(0, 0, 0, 0)
        row.addLayout(body, 1)
        self.setFixedWidth(WIDTH)

    # ---------- 对外 ----------
    def show_notice(self, title: str, text: str, icon=QSystemTrayIcon.MessageIcon.Information,
                    clickable: bool = True) -> None:
        """显示（或替换成）这条提醒并重新计时。clickable = 点了有去处（只影响鼠标指针样子）。"""
        self.kind = _kind(icon)
        self.message = text
        self.title_label.setText(title)
        self.text_label.setText(_wrappable(text))
        self.close_button.setToolTip(t("notice.close"))
        warn = self.kind == "warning"
        self.accent.setStyleSheet(f"background: {AMBER if warn else BLUE}; border: none; border-radius: 2px;")
        self.warn_badge.setVisible(warn)
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
        self._place()
        self.show()
        self.raise_()
        if not self._hovered:  # 鼠标正停在上面：照旧暂停，等移开再计时，别在光标底下消失
            self._timer.start(self.timeout_ms)

    # ---------- 内部 ----------
    def _place(self) -> None:
        """主屏可用区域（不含任务栏）的右下角；坐标、尺寸都是 Qt 的逻辑像素，多屏不同缩放时由 Qt 换算。
        高度按固定宽度下文字折行后的高度算。"""
        self.resize(WIDTH, self.heightForWidth(WIDTH))
        screen = QGuiApplication.primaryScreen()
        if screen is None:  # 没有屏幕（远程桌面断开之类）：不挪位置，别让提醒把程序弄崩
            return
        g = screen.availableGeometry()
        self.move(g.x() + g.width() - self.width() - MARGIN, g.y() + g.height() - self.height() - MARGIN)

    def enterEvent(self, event) -> None:  # 鼠标停在上面：先不消失
        self._hovered = True
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # 移开：重新计时
        self._hovered = False
        self._timer.start(self.timeout_ms)
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:  # 悬停时被点掉：不一定还会收到离开事件，别让下一条永远不自动消失
        self._hovered = False
        super().hideEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """左键在小窗里按下又在小窗里松开 = 点了它（按下后拖出去再松开不算）。"""
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.hide()
            self.clicked.emit()
        event.accept()

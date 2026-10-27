"""「复制后翻译」的结果小窗：鼠标旁边弹出，译文逐段出现，可选中复制；方向按钮反向重翻；原文默认收起。
Esc 关闭；没钉住时点到别处就关（同浮窗）。翻完记一条历史（"source": "clipboard"，没有图片）。

线程规则同 overlay.py：后台线程只往队列里放 (种类, 代际, 内容)，界面线程的定时器取出来处理；线程不碰 Qt 对象、不持有窗口。"""
from __future__ import annotations

import logging
import queue
import threading
import time
import uuid

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTextEdit, QToolButton, QToolTip, QVBoxLayout, QWidget

from ..cliptext import to_segments
from ..i18n import t
from ..layout import detect_direction
from ..overlay import POLL_MS, STATUS, _translate_worker

log = logging.getLogger(__name__)

WIDTH = 520
MAX_SCREEN_FRACTION = 0.6
GAP = 14  # 小窗离鼠标的距离


def place_near(cursor: QPoint, size: tuple[int, int], screen: QRect) -> QPoint:
    """放在鼠标右下方；右边/下边放不下就翻到左边/上边；最后夹在屏幕里。"""
    w, h = size
    x, y = cursor.x() + GAP, cursor.y() + GAP
    right, bottom = screen.left() + screen.width(), screen.top() + screen.height()
    if x + w > right:
        x = cursor.x() - GAP - w
    if y + h > bottom:
        y = cursor.y() - GAP - h
    x = min(max(x, screen.left()), max(screen.left(), right - w))
    y = min(max(y, screen.top()), max(screen.top(), bottom - h))
    return QPoint(x, y)


class ClipWindow(QWidget):
    closed = Signal(object)

    def __init__(self, paragraphs: list[str], router, history=None, cursor: QPoint | None = None, clipboard=None):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("clip")
        self.setStyleSheet("#clip{background:white;border:1px solid #3b82f6}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._router = router
        self._history = history
        self._clipboard = clipboard or QGuiApplication.clipboard()
        self._cursor = cursor if cursor is not None else QCursor.pos()
        self.session_id = uuid.uuid4().hex  # 换方向重翻：历史里还是同一条
        self.segments = to_segments(paragraphs)
        self.direction = detect_direction(" ".join(paragraphs))
        self.results: dict = {}
        self.generation = 0
        self.pinned = False
        self._error: str | None = None
        self._cancel = threading.Event()
        self._press: tuple[QPoint, QPoint] | None = None  # (按下时鼠标全局位置, 窗口位置)：拖动用
        self._inbox: queue.Queue = queue.Queue()
        self._pump = QTimer(self)
        self._pump.setInterval(POLL_MS)
        self._pump.timeout.connect(self._drain)
        self._pump.start()
        self._placed = False  # 第一次算好高度时放到鼠标旁边
        self._build()
        self._start()

    # ---------- 界面 ----------
    def _build(self) -> None:
        bar = QWidget(self)
        bar.setObjectName("bar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setStyleSheet("#bar{background:#1f2937} QLabel{color:white} QToolButton{border:none;padding:2px 6px;"
                          "color:white} QToolButton:hover{background:#374151} QToolButton:checked{background:#2563eb}")
        self.bar = bar
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(6, 2, 4, 2)
        bl.setSpacing(2)
        self.status = QLabel(bar)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.status, 1)

        def btn(text: str, slot, tip: str = "", checkable: bool = False) -> QToolButton:
            b = QToolButton(bar)
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            bl.addWidget(b)
            return b

        self.btn_flip = btn("", self.flip_direction, t("clip.flip_tip"))
        self.btn_copy = btn(t("clip.copy"), self.copy_translation)
        self.btn_pin = btn("📌", self._on_pin, t("clip.pin_tip"), checkable=True)
        btn("×", self.close, t("clip.close"))

        self.view = QTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setFrameShape(QTextEdit.Shape.NoFrame)
        self.view.setStyleSheet("QTextEdit{font-size:11pt;padding:4px}")
        self.view.installEventFilter(self)
        self.btn_original = QToolButton(self)
        self.btn_original.setAutoRaise(True)
        self.btn_original.setCheckable(True)
        self.btn_original.toggled.connect(self._toggle_original)
        self.original = QTextEdit(self)
        self.original.setReadOnly(True)
        self.original.setFrameShape(QTextEdit.Shape.NoFrame)
        self.original.setStyleSheet("QTextEdit{color:#6b7280;font-size:9pt;padding:4px}")
        self.original.setPlainText("\n\n".join(s.text for s in self.segments))
        self.original.installEventFilter(self)
        self.original.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.btn_original, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.original)
        self.setFixedWidth(WIDTH)
        self._toggle_original(False)

    def _toggle_original(self, on: bool) -> None:
        self.btn_original.setText(t("clip.hide_original") if on else t("clip.show_original"))
        self.original.setVisible(on)
        self._fit()

    def translation_text(self) -> str:
        """已到的段是译文，没到的段显示「…」（翻译中）。翻译服务不可用的段给原文（同浮窗：显示原文）。"""
        return "\n\n".join(self.results[s.idx].text if s.idx in self.results else "…" for s in self.segments)

    def service_status(self) -> str:
        services = {r.service for r in self.results.values()}
        for name in ("none", "tencent", "fallback", "vllm"):
            if name in services:
                return name
        return "pending"

    def _refresh(self) -> None:
        bar = self.view.verticalScrollBar()
        keep = bar.value()
        self.view.setPlainText(self.translation_text())
        bar.setValue(keep)
        if self._error is not None:
            text, color = t("status.translate_failed", error=self._error), "#ef4444"
        else:
            key, color = STATUS[self.service_status()]
            text = t(key)
        self.status.setText(f"<span style='color:{color}'>●</span> {text}")
        self.btn_flip.setText(t("clip.zh2en") if self.direction == "zh2en" else t("clip.en2zh"))
        self._fit()

    def _fit(self) -> None:
        """高度随内容：译文（和展开的原文）全显示出来，最多屏幕高的 60%，再多就滚动。第一次定位在鼠标旁边。"""
        screen = (QGuiApplication.screenAt(self._cursor) or QGuiApplication.primaryScreen()).availableGeometry()
        cap = int(screen.height() * MAX_SCREEN_FRACTION)

        def doc_h(edit: QTextEdit) -> int:
            edit.document().setTextWidth(WIDTH - 12)
            return int(edit.document().size().height()) + 12

        h = self.bar.sizeHint().height() + self.btn_original.sizeHint().height() + doc_h(self.view) + 4
        if self.original.isVisible():
            h += min(doc_h(self.original), cap // 3)
            self.original.setFixedHeight(min(doc_h(self.original), cap // 3))
        h = max(80, min(h, cap))
        self.setFixedHeight(h)
        if not self._placed:
            self._placed = True
            self.move(place_near(self._cursor, (WIDTH, h), screen))
        elif self.geometry().bottom() > screen.top() + screen.height():  # 长高后超出屏幕底部：往上挪
            self.move(self.x(), max(screen.top(), screen.top() + screen.height() - h))

    # ---------- 翻译 ----------
    def _start(self) -> None:
        self._cancel.set()
        self._cancel = cancel = threading.Event()
        self.generation += 1
        self.results = {}
        self._error = None
        self._refresh()
        threading.Thread(target=_translate_worker, args=(self._router, list(self.segments), self.direction,
                                                         self.generation, cancel, self._inbox), daemon=True).start()

    def _drain(self) -> None:
        changed = False
        while True:
            try:
                kind, gen, payload = self._inbox.get_nowait()
            except queue.Empty:
                break
            if gen != self.generation:
                continue  # 换了方向之前那一轮的，丢掉
            if kind == "result":
                self.results[payload.idx] = payload
                changed = True
            elif kind == "failed":
                self._error = payload
                changed = True
            elif kind == "finished":
                self._save_history()
        if changed:
            self._refresh()

    def _save_history(self) -> None:
        if self._history is None:
            return
        entry = {
            "session": self.session_id,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "direction": self.direction,
            "service": self.service_status(),
            "source": "clipboard",
            "segments": [{"src": s.text, "dst": self.results[s.idx].text if s.idx in self.results else None}
                         for s in self.segments],
        }
        try:
            self._history.add(entry)
        except Exception:
            log.exception("写历史失败")

    # ---------- 操作 ----------
    def flip_direction(self) -> None:
        self.direction = "en2zh" if self.direction == "zh2en" else "zh2en"
        self._start()

    def copy_translation(self) -> None:
        self._clipboard.setText(self.translation_text())
        self._toast(t("clip.copied"))

    def _on_pin(self, checked: bool) -> None:
        self.pinned = checked

    def _toast(self, text: str) -> None:
        QToolTip.showText(self.mapToGlobal(self.btn_copy.geometry().bottomLeft()), text, self)

    def eventFilter(self, obj, e) -> bool:
        """文字框里：Esc 关窗；Ctrl+C 有选中照常复制选中的，没选中复制全部译文（原文框里没选中时复制全部原文）。"""
        if e.type() == QEvent.Type.KeyPress:
            if e.key() == Qt.Key.Key_Escape:
                self.close()
                return True
            if e.matches(QKeySequence.StandardKey.Copy):
                cur = obj.textCursor()
                if cur.hasSelection():  # 自己写剪贴板（不交给 QTextEdit）：跨段选中时 Qt 给的是 U+2029，换回换行
                    self._clipboard.setText(cur.selectedText().replace(" ", "\n"))
                elif obj is self.view:
                    self.copy_translation()
                else:
                    self._clipboard.setText(obj.toPlainText())
                return True
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        elif e.matches(QKeySequence.StandardKey.Copy):
            self.copy_translation()
        else:
            super().keyPressEvent(e)

    # ---------- 拖动 ----------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = (e.globalPosition().toPoint(), self.pos())

    def mouseMoveEvent(self, e) -> None:
        if self._press is not None and e.buttons() & Qt.MouseButton.LeftButton:
            start, pos = self._press
            self.move(pos + e.globalPosition().toPoint() - start)

    def mouseReleaseEvent(self, e) -> None:
        self._press = None

    # ---------- 生命周期 ----------
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.view.setFocus()

    def changeEvent(self, e) -> None:
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            QTimer.singleShot(0, self._close_if_inactive)
        super().changeEvent(e)

    def _close_if_inactive(self) -> None:
        if self.isVisible() and not self.isActiveWindow() and not self.pinned:
            self.close()

    def closeEvent(self, e) -> None:
        self._cancel.set()
        self._pump.stop()
        self.closed.emit(self)
        super().closeEvent(e)

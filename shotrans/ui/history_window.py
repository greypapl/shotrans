"""历史记录窗口：列表倒序（时间、方向、原文首行），搜索原文和译文（只搜文字）。
选中后：上方看这条的截图（默认译文图，按钮切换原图/译文图；比窗口宽时按宽度等比缩小，太高可滚动），
下方左右并排看完整原文/译文。没存截图的记录（旧记录、关了「保存截图图片」、图片写失败）图片区只给一句说明。"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import i18n
from ..i18n import N_, t

DIR_KEYS = {"en2zh": N_("history.dir.en2zh"), "zh2en": N_("history.dir.zh2en")}
FIRST_LINE_MAX = 80
SAVING_RECHECK_MS = 300


def _valid(e) -> bool:
    return (isinstance(e, dict) and isinstance(e.get("segments"), list)
            and all(isinstance(s, dict) and isinstance(s.get("src"), str) for s in e["segments"]))


def original_text(e: dict) -> str:
    return "\n\n".join(s["src"] for s in e["segments"])


def translation_text(e: dict) -> str:
    return "\n\n".join(s["dst"] if isinstance(s.get("dst"), str) else s["src"] for s in e["segments"])


def first_line(e: dict) -> str:
    src = e["segments"][0]["src"] if e["segments"] else ""
    lines = src.splitlines()
    return (lines[0] if lines else "")[:FIRST_LINE_MAX]


class HistoryWindow(QWidget):
    def __init__(self, app, confirm: Callable[[str], bool] | None = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._confirm = confirm or self._confirm_dialog
        self._entries: list[dict] = []
        self.search = QLineEdit(self)
        self.search.textChanged.connect(self._apply_filter)
        self.btn_refresh = QPushButton(self)
        self.btn_refresh.clicked.connect(self.reload)
        self.table = QTableWidget(0, 3, self)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.src_title, self.dst_title = QLabel(self), QLabel(self)
        self.src_view, self.dst_view = QPlainTextEdit(self), QPlainTextEdit(self)
        for v in (self.src_view, self.dst_view):
            v.setReadOnly(True)
        self.btn_copy_src, self.btn_copy_dst = QPushButton(self), QPushButton(self)
        self.btn_copy_src.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.src_view.toPlainText()))
        self.btn_copy_dst.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.dst_view.toPlainText()))
        self.btn_delete, self.btn_clear = QPushButton(self), QPushButton(self)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_clear.clicked.connect(self.clear_all)
        # 截图区：选中记录的两张图（kind -> QPixmap，原尺寸），当前看哪张
        self._pixmaps: dict[str, QPixmap] = {}
        self.image_kind = "rendered"
        self._image_saving = False  # 选中的这条图还在后台写：说明改成「保存中」，过一会儿再看
        self.btn_image_toggle = QPushButton(self)
        self.btn_image_toggle.clicked.connect(self.toggle_image)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_scroll = QScrollArea(self)
        self.image_scroll.setWidget(self.image_label)
        self.image_scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.image_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # 总是按宽度缩放
        self.image_scroll.installEventFilter(self)
        self.image_note = QLabel(self)
        self.image_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_note.setStyleSheet("color:#6b7280")

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.btn_refresh)
        panes = QWidget(self)
        pl = QHBoxLayout(panes)
        for title, view, btn in ((self.src_title, self.src_view, self.btn_copy_src),
                                 (self.dst_title, self.dst_view, self.btn_copy_dst)):
            col = QVBoxLayout()
            col.addWidget(title)
            col.addWidget(view, 1)
            col.addWidget(btn)
            pl.addLayout(col)
        image_box = QWidget(self)
        il = QVBoxLayout(image_box)
        il.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn_image_toggle)
        il.addLayout(row)
        il.addWidget(self.image_scroll, 1)
        il.addWidget(self.image_note)
        split = QSplitter(Qt.Orientation.Vertical, self)
        split.addWidget(self.table)
        split.addWidget(image_box)
        split.addWidget(panes)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 2)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.btn_delete)
        bottom.addWidget(self.btn_clear)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)
        self.resize(900, 760)
        i18n.hub().language_changed.connect(self.retranslate)
        self.retranslate()
        self.reload()

    def retranslate(self, *_) -> None:
        self.setWindowTitle(t("history.title"))
        self.table.setHorizontalHeaderLabels([t("history.col_time"), t("history.col_dir"), t("history.col_text")])
        self.search.setPlaceholderText(t("history.search"))
        self.src_title.setText(t("history.original"))
        self.dst_title.setText(t("history.translation"))
        self.btn_copy_src.setText(t("history.copy_src"))
        self.btn_copy_dst.setText(t("history.copy_dst"))
        self.btn_delete.setText(t("history.delete"))
        self.btn_clear.setText(t("history.clear"))
        self.btn_refresh.setText(t("history.refresh"))
        self._update_toggle_text()
        self._update_note_text()
        for i, e in enumerate(self._entries):
            self.table.setItem(i, 1, QTableWidgetItem(self._dir_text(e)))

    @staticmethod
    def _dir_text(e: dict) -> str:
        key = DIR_KEYS.get(e.get("direction"))
        return t(key) if key else str(e.get("direction", ""))

    def entries(self) -> list[dict]:
        return list(self._entries)

    def reload(self) -> None:
        self.table.clearSelection()  # 先清选中：旧行号对不上新列表，面板也随之清空
        self._entries = [e for e in reversed(self._app.history.load()) if _valid(e)]  # 新的在上
        self.table.setRowCount(len(self._entries))
        for i, e in enumerate(self._entries):
            self.table.setItem(i, 0, QTableWidgetItem(str(e.get("time", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(self._dir_text(e)))
            self.table.setItem(i, 2, QTableWidgetItem(first_line(e)))
        self.src_view.clear()
        self.dst_view.clear()
        self._load_images(None)
        self._apply_filter()

    def _apply_filter(self, *_) -> None:
        q = self.search.text().strip().lower()
        for i, e in enumerate(self._entries):
            hay = (original_text(e) + "\n" + translation_text(e)).lower()
            hidden = bool(q) and q not in hay
            self.table.setRowHidden(i, hidden)
            if hidden and self.table.selectionModel().isRowSelected(i, self.table.rootIndex()):
                self.table.clearSelection()  # 被过滤掉的行不能留着选中（面板和删除都看选中）

    def select(self, row: int) -> None:
        self.table.selectRow(row)

    def _selected(self) -> dict | None:
        rows = {ix.row() for ix in self.table.selectedIndexes()}
        return self._entries[min(rows)] if rows else None

    def _on_select(self) -> None:
        e = self._selected()
        self.src_view.setPlainText(original_text(e) if e else "")
        self.dst_view.setPlainText(translation_text(e) if e else "")
        self._load_images(e)

    # ---------- 截图 ----------
    def _load_images(self, e: dict | None) -> None:
        """选中变了：读这条的两张图（读不出来的当没有），默认看译文图。"""
        self._pixmaps = {}
        history = self._app.history
        if e is not None:
            e = history.find(e) or e  # 窗口里的副本可能比文件旧（后台刚补上 images）
        for kind in ("rendered", "original"):
            path = history.image_path(e, kind)  # 没选中（None）、没存图、文件不在都返回 None
            if path is not None:
                pm = QPixmap(str(path))
                if not pm.isNull():
                    self._pixmaps[kind] = pm
        self.image_kind = "rendered" if "rendered" in self._pixmaps or not self._pixmaps else "original"
        has = bool(self._pixmaps)
        self.image_scroll.setVisible(has)
        self.btn_image_toggle.setVisible(len(self._pixmaps) == 2)
        self.image_note.setVisible(e is not None and not has)
        self._image_saving = e is not None and not has and history.images_pending(e)
        self._update_note_text()
        self._update_toggle_text()
        self._show_image()
        if self._image_saving:
            QTimer.singleShot(SAVING_RECHECK_MS, self, self._recheck_images)

    def _recheck_images(self) -> None:
        """图还在写时定时再看一次：写完就显示出来，写失败就换成「没有保存截图」。"""
        if self._image_saving:
            self._load_images(self._selected())

    def _update_note_text(self) -> None:
        self.image_note.setText(t("history.image_saving") if self._image_saving else t("history.no_image"))

    def toggle_image(self) -> None:
        if len(self._pixmaps) < 2:
            return
        self.image_kind = "original" if self.image_kind == "rendered" else "rendered"
        self._update_toggle_text()
        self._show_image()

    def _update_toggle_text(self) -> None:
        key = "history.show_original_image" if self.image_kind == "rendered" else "history.show_rendered_image"
        self.btn_image_toggle.setText(t(key))

    def _show_image(self) -> None:
        """按可用宽度等比缩小（不放大）。可用宽度预留竖滚动条：出不出滚动条都用同一个宽度，不会来回抖。"""
        pm = self._pixmaps.get(self.image_kind)
        if pm is None:
            self.image_label.clear()
            self.image_label.resize(0, 0)
            return
        avail = self.image_scroll.contentsRect().width() - self.image_scroll.verticalScrollBar().sizeHint().width()
        if avail > 0 and pm.width() > avail:
            pm = pm.scaledToWidth(avail, Qt.TransformationMode.SmoothTransformation)
        self.image_label.setPixmap(pm)
        self.image_label.resize(pm.size())

    def eventFilter(self, obj, event) -> bool:
        if obj is self.image_scroll and event.type() == QEvent.Type.Resize:
            self._show_image()
        return super().eventFilter(obj, event)

    def delete_selected(self) -> bool:
        e = self._selected()
        if e is None:
            return False
        ok = self._app.history.delete(e)
        self.reload()
        return ok

    def clear_all(self) -> bool:
        if not self._confirm(t("history.clear_confirm")):
            return False
        self._app.history.clear()
        self.reload()
        return True

    def _confirm_dialog(self, text: str) -> bool:
        return QMessageBox.question(self, t("history.title"), text) == QMessageBox.StandardButton.Yes

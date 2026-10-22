"""设置窗口的「术语表」页：两列表格（原文、译法），注释行灰色只读、原位保留，空行隐藏但保留；
运行时会忽略的箭头行（「a =>」「=> b」）同样灰色只读、带提示、原样保留。
表格第 i 行 ↔ self.doc.rows[i]（含注释、空行），编辑直接改 Row；保存前检查，问题行标黄。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import i18n
from ..glossary_edit import (GlossaryDoc, Issue, Row, check_rows, export_doc, has_blocking, import_text, load_doc,
                             save_doc)
from ..i18n import N_, t

WARN_BG = QColor("#fef3c7")
COMMENT_FG = QColor("#9ca3af")
ISSUE_KEYS = {"empty_src": N_("glossary.issue.empty_src"), "bad_src": N_("glossary.issue.bad_src"),
              "bad_newline": N_("glossary.issue.bad_newline"),
              "dup_src": N_("glossary.issue.dup_src"),
              "dup_dst": N_("glossary.issue.dup_dst"), "single_hanzi": N_("glossary.issue.single_hanzi")}


class GlossaryPage(QWidget):
    changed = Signal()

    def __init__(self, path: Path, confirm: Callable[[list[Issue]], bool] | None = None,
                 ask_open: Callable[[], str | None] | None = None,
                 ask_save: Callable[[], str | None] | None = None, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self._confirm = confirm or self._confirm_dialog
        self._ask_open = ask_open or self._open_dialog
        self._ask_save = ask_save or self._save_dialog
        self.doc = GlossaryDoc()
        self.load_error = False
        self._error_key: str | None = None  # 不是 UTF-8 / 读取失败，分开提示
        self._dirty = False
        self._quiet = False      # 程序自己改表格（加载、标黄）时不算用户编辑
        self._query = ""
        self._issues: list[Issue] = []
        self.search = QLineEdit(self)
        self.search.textChanged.connect(self.set_search)
        self.table = QTableWidget(0, 2, self)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        self.btn_add = QPushButton(self)
        self.btn_add.clicked.connect(self.add_row)
        self.btn_del = QPushButton(self)
        self.btn_del.clicked.connect(self.delete_selected)
        self.btn_import = QPushButton(self)
        self.btn_import.clicked.connect(self._on_import)
        self.btn_export = QPushButton(self)
        self.btn_export.clicked.connect(self._on_export)
        self.issues_label = QLabel(self)
        self.issues_label.setWordWrap(True)
        self.error_label = QLabel(self)
        self.error_label.setStyleSheet("color:#ef4444")
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        for b in (self.btn_add, self.btn_del, self.btn_import, self.btn_export):
            bar.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.error_label)
        lay.addLayout(bar)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.issues_label)
        i18n.hub().language_changed.connect(self.retranslate)
        self.retranslate()
        self.load()

    # ---------- 文字 ----------
    def retranslate(self, *_) -> None:
        self.table.setHorizontalHeaderLabels([t("glossary.col_src"), t("glossary.col_dst")])
        self.search.setPlaceholderText(t("glossary.search"))
        self.btn_add.setText(t("glossary.add"))
        self.btn_del.setText(t("glossary.delete"))
        self.btn_import.setText(t("glossary.import"))
        self.btn_export.setText(t("glossary.export"))
        self.error_label.setText(t(self._error_key, path=self.path) if self._error_key else "")
        self._quiet = True
        try:
            for i, r in enumerate(self.doc.rows):
                if r.kind == "ignored" and self.table.item(i, 0) is not None:
                    self.table.item(i, 0).setToolTip(t("glossary.ignored_tip"))
        finally:
            self._quiet = False
        self._show_issues(self._issues)

    # ---------- 加载与显示 ----------
    def load(self) -> None:
        self._error_key = None
        try:
            self.doc = load_doc(self.path)
        except UnicodeDecodeError:
            self.doc, self._error_key = GlossaryDoc(), N_("glossary.load_error")
        except OSError:
            self.doc, self._error_key = GlossaryDoc(), N_("glossary.read_failed")
        self.load_error = self._error_key is not None
        self._issues = []
        self._fill()
        self._dirty = False
        for w in (self.table, self.btn_add, self.btn_del, self.btn_import, self.btn_export, self.search):
            w.setEnabled(not self.load_error)
        self.error_label.setVisible(self.load_error)
        self.retranslate()

    def _fill(self) -> None:
        self._quiet = True
        try:
            self.table.clearSpans()
            self.table.setRowCount(0)
            for r in self.doc.rows:
                self._append(r)
        finally:
            self._quiet = False
        self._apply_filter()

    def _append(self, r: Row) -> None:
        i = self.table.rowCount()
        self.table.insertRow(i)
        if r.kind == "entry":
            self.table.setItem(i, 0, QTableWidgetItem(r.src))
            self.table.setItem(i, 1, QTableWidgetItem(r.dst))
        else:
            item = QTableWidgetItem(r.raw)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # 只读、不可选
            item.setForeground(QBrush(COMMENT_FG))
            if r.kind == "ignored":
                item.setToolTip(t("glossary.ignored_tip"))
            self.table.setItem(i, 0, item)
            self.table.setSpan(i, 0, 1, 2)

    def _apply_filter(self) -> None:
        q = self._query
        for i, r in enumerate(self.doc.rows):
            if r.kind == "blank":
                hidden = True
            elif r.kind == "comment":
                hidden = bool(q)
            elif r.kind == "ignored":
                hidden = bool(q) and q not in r.raw.lower()
            else:
                hidden = bool(q) and q not in r.src.lower() and q not in r.dst.lower()
            self.table.setRowHidden(i, hidden)

    # ---------- 编辑 ----------
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._quiet:
            return
        r = self.doc.rows[item.row()]
        if r.kind != "entry":
            return
        if item.column() == 0:
            r.src = item.text()
        else:
            r.dst = item.text()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.changed.emit()

    def is_dirty(self) -> bool:
        return self._dirty

    def add_row(self) -> int:
        r = Row.entry("")
        self.doc.rows.append(r)
        self._quiet = True
        try:
            self._append(r)
        finally:
            self._quiet = False
        i = self.table.rowCount() - 1
        self.table.setCurrentCell(i, 0)
        self._mark_dirty()
        return i

    def delete_selected(self) -> int:
        rows = sorted({ix.row() for ix in self.table.selectedIndexes()}, reverse=True)
        n = 0
        for i in rows:
            if self.doc.rows[i].kind == "entry":  # 注释（分组标题）不能在这里删
                del self.doc.rows[i]
                n += 1
        if n:
            self._fill()
            self._mark_dirty()
        return n

    def set_search(self, text: str) -> None:
        self._query = text.strip().lower()
        self._apply_filter()

    # ---------- 检查与保存 ----------
    def check(self) -> list[Issue]:
        issues = check_rows(self.doc.rows)
        bad = {x.row for x in issues}
        self._quiet = True  # 改背景色也会触发 itemChanged
        try:
            for i, r in enumerate(self.doc.rows):
                if r.kind != "entry":
                    continue
                for c in (0, 1):
                    it = self.table.item(i, c)
                    if it is not None:
                        it.setBackground(QBrush(WARN_BG) if i in bad else QBrush())
        finally:
            self._quiet = False
        self._issues = issues
        self._show_issues(issues)
        return issues

    def _show_issues(self, issues: list[Issue]) -> None:
        self.issues_label.setText("\n".join(t(ISSUE_KEYS[x.code], row=x.row + 1) for x in issues))

    def save(self) -> bool:
        if self.load_error:
            return False
        issues = self.check()
        if has_blocking(issues):
            return False
        if issues and not self._confirm(issues):
            return False
        save_doc(self.doc, self.path)
        self.load()  # 重新解析：刚保存的行成为新的「原行」
        return True

    # ---------- 导入导出 ----------
    def import_file(self, path) -> int:
        n = import_text(self.doc, Path(path).read_text(encoding="utf-8-sig"))
        self._fill()
        self._mark_dirty()
        self.check()  # 重复原文当场提示
        return n

    def export_file(self, path) -> None:
        export_doc(self.doc, Path(path))

    def _on_import(self) -> None:
        p = self._ask_open()
        if p:
            try:
                self.import_file(p)
            except (OSError, UnicodeDecodeError):
                self.issues_label.setText(t("glossary.import_failed"))

    def _on_export(self) -> None:
        p = self._ask_save()
        if p:
            self.export_file(p)

    # ---------- 默认对话框（测试里注入替身） ----------
    def _confirm_dialog(self, issues: list[Issue]) -> bool:
        return QMessageBox.question(self, t("glossary.confirm_title"),
                                    t("glossary.confirm_text", n=len(issues))) == QMessageBox.StandardButton.Yes

    def _open_dialog(self) -> str | None:
        path, _ = QFileDialog.getOpenFileName(self, t("glossary.import"), "", t("glossary.file_filter"))
        return path or None

    def _save_dialog(self) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, t("glossary.export"), self.path.name,
                                              t("glossary.file_filter"))
        return path or None

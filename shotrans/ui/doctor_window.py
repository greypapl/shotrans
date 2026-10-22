"""自检窗口：打开即在后台线程逐项检查（复用 doctor.build_items），每行 ⏳/✅/❌；
失败行带修复按钮；「重新检查」「复制结果」（key 打码）。
线程规则同浮窗：工作线程只往 queue 里放 (种类, 轮次, 行号, ok, msg)，不碰 Qt 对象；主线程定时器取。"""
from __future__ import annotations

import os
import queue
import threading
from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

import shotrans

from .. import doctor, i18n
from ..doctor import FIX_LANG, LANG_SETTINGS_URL, CheckItem
from ..i18n import N_, t

POLL_MS = 30
FIX_KEYS = {FIX_LANG: N_("doctor.fix.lang"), "settings:service": N_("doctor.fix.settings"),
            "settings:hotkey": N_("doctor.fix.hotkey")}


def _check_worker(fns, run_id: int, cancel: threading.Event, inbox: queue.Queue) -> None:
    for i, fn in enumerate(fns):
        if cancel.is_set():
            return
        ok, msg = doctor.run_one(fn)
        if cancel.is_set():
            return
        inbox.put(("row", run_id, i, ok, msg))
    inbox.put(("done", run_id, -1, None, None))


class DoctorWindow(QWidget):
    def __init__(self, app, items_factory: Callable[[], list[CheckItem]] | None = None,
                 open_url: Callable[[str], None] | None = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._items_factory = items_factory or (
            lambda: doctor.build_items(app.home, None, app.own_hotkey()))  # 只查数据目录的配置；旧文件只给命令行 doctor 用
        self._open_url = open_url or (lambda url: os.startfile(url))
        self._items: list[CheckItem] = []
        self._results: list[tuple[bool, str] | None] = []
        self._inbox: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_id = 0
        self.table = QTableWidget(0, 4, self)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.btn_rerun = QPushButton(self)
        self.btn_rerun.clicked.connect(self.run_checks)
        self.btn_copy = QPushButton(self)
        self.btn_copy.clicked.connect(self.copy_report)
        self.note = QLabel(self)
        bar = QHBoxLayout()
        bar.addWidget(self.note, 1)
        bar.addWidget(self.btn_rerun)
        bar.addWidget(self.btn_copy)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table, 1)
        lay.addLayout(bar)
        self.resize(760, 360)
        self._pump = QTimer(self)
        self._pump.setInterval(POLL_MS)
        self._pump.timeout.connect(self._drain)
        self._pump.start()
        i18n.hub().language_changed.connect(self.retranslate)
        self.retranslate()
        self.run_checks()

    def retranslate(self, *_) -> None:
        self.setWindowTitle(t("doctor.title"))
        self.table.setHorizontalHeaderLabels([t("doctor.col_status"), t("doctor.col_name"),
                                              t("doctor.col_detail"), t("doctor.col_fix")])
        self.btn_rerun.setText(t("doctor.rerun"))
        self.btn_copy.setText(t("doctor.copy"))
        self._render()

    def run_checks(self) -> None:
        self._cancel.set()
        self._cancel = cancel = threading.Event()
        self._run_id += 1
        self._items = self._items_factory()
        self._results = [None] * len(self._items)
        self.note.setText("")
        self._render()
        fns = [it.fn for it in self._items]
        self._thread = threading.Thread(target=_check_worker, args=(fns, self._run_id, cancel, self._inbox),
                                        daemon=True, name="shotrans-doctor")
        self._thread.start()

    def _drain(self) -> None:
        changed = False
        while True:
            try:
                kind, run_id, i, ok, msg = self._inbox.get_nowait()
            except queue.Empty:
                break
            if run_id != self._run_id or kind != "row":
                continue  # 上一轮的结果、结束标记：不用
            self._results[i] = (ok, msg)
            changed = True
        if changed:
            self._render()

    def _failed_ids(self) -> set[str]:
        return {it.id for it, r in zip(self._items, self._results) if r is not None and not r[0]}

    def _render(self) -> None:
        self.table.setRowCount(len(self._items))
        failed = self._failed_ids()
        for i, it in enumerate(self._items):
            res = self._results[i] if i < len(self._results) else None
            mark = "⏳" if res is None else "✅" if res[0] else "❌"
            self.table.setItem(i, 0, QTableWidgetItem(mark))
            self.table.setItem(i, 1, QTableWidgetItem(doctor.item_label(it.id) if it.id in doctor.ITEM_LABEL_KEYS
                                                      else it.label))
            self.table.setItem(i, 2, QTableWidgetItem(t("doctor.pending") if res is None else res[1]))
            self.table.removeCellWidget(i, 3)
            blocked = it.depends_on in failed  # 依赖的那一行已带按钮：先修那一行
            if res is not None and not res[0] and it.fix in FIX_KEYS and not blocked:
                b = QPushButton(t(FIX_KEYS[it.fix]), self.table)
                b.clicked.connect(lambda _=False, fix=it.fix: self._fix(fix))
                self.table.setCellWidget(i, 3, b)

    def row_state(self, i: int) -> str:
        res = self._results[i]
        return "pending" if res is None else "ok" if res[0] else "fail"

    def fix_button(self, i: int):
        return self.table.cellWidget(i, 3)

    def _fix(self, fix: str) -> None:
        if fix == FIX_LANG:
            self._open_url(LANG_SETTINGS_URL)
            return
        kind, _, page = fix.partition(":")
        self._app.open_window(kind, page or None)

    def report_text(self) -> str:
        rows = [(doctor.item_label(it.id) if it.id in doctor.ITEM_LABEL_KEYS else it.label,
                 None if r is None else r[0], "" if r is None else r[1])
                for it, r in zip(self._items, self._results)]
        return doctor.format_report(rows, self._app.config, getattr(shotrans, "__version__", "?"))

    def copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self.report_text())
        self.note.setText(t("doctor.copied"))

    def closeEvent(self, e) -> None:
        self._cancel.set()
        self._pump.stop()  # 之后线程再放进来的结果没人取，随队列回收
        super().closeEvent(e)

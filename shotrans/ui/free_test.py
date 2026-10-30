"""引导里「免费：微软翻译」下面的测试连接：真翻一句，测通才能下一步。
测试在工作线程里跑，线程只往队列里放结果，界面线程的定时器取出来再改控件（同 ServiceForm）。"""
from __future__ import annotations

import queue
import threading
from typing import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..i18n import t


def _worker(tester, gen: int, inbox: queue.Queue) -> None:
    try:
        ok, msg = tester()
    except Exception as e:  # 测试函数自己出错也要交回结果，不能让按钮一直灰着
        ok, msg = False, repr(e)
    inbox.put((gen, bool(ok), str(msg)))


class FreeTest(QWidget):
    tested = Signal(bool)

    def __init__(self, tester: Callable[[], tuple[bool, str]], parent=None):
        super().__init__(parent)
        self._tester = tester
        self.test_ok = False
        self._gen = 0
        self._inbox: queue.Queue = queue.Queue()
        self.btn_test = QPushButton()
        self.btn_test.clicked.connect(self.start_test)
        self.result = QLabel()
        self.result.setWordWrap(True)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.btn_test)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(row)
        lay.addWidget(self.result)
        self._pump = QTimer(self)
        self._pump.setInterval(50)
        self._pump.timeout.connect(self._drain)
        self.retranslate()

    def retranslate(self) -> None:
        self.btn_test.setText(t("wizard.free.test"))

    def start_test(self) -> None:
        self._gen += 1
        self.test_ok = False
        self.result.setText(t("wizard.free.testing"))
        self.result.setStyleSheet("")
        self.btn_test.setEnabled(False)
        self._pump.start()
        threading.Thread(target=_worker, args=(self._tester, self._gen, self._inbox), daemon=True,
                         name="shotrans-freetest").start()

    def _drain(self) -> None:
        while True:
            try:
                gen, ok, msg = self._inbox.get_nowait()
            except queue.Empty:
                return
            if gen != self._gen:  # 早先那次测试的结果，作废
                continue
            self._pump.stop()
            self.btn_test.setEnabled(True)
            self.test_ok = ok
            self.result.setText(t("wizard.free.ok", msg=msg) if ok else t("wizard.free.bad", msg=msg))
            self.result.setStyleSheet("color:#16a34a" if ok else "color:#ef4444")
            self.tested.emit(ok)

    def shutdown(self) -> None:
        self._gen += 1  # 关窗后才回来的结果作废
        self._pump.stop()

"""压力测试：后台线程还在跑时反复关浮窗，进程不能崩、线程里不能抛异常。

旧设计（工作线程经挂在窗口上的 QObject 发 Qt 信号）在这里约一半的运行会 access violation 整个进程退出。
只用浮窗的公开接口（构造、show、close），不碰内部实现，所以新旧设计都能拿它来比。"""
import gc
import random
import threading
import time
import weakref

import shiboken6
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.overlay import OverlayWindow
from shotrans.session import Session

ROUNDS = 50
FLOOD = 20


class _Counter:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = 0
        self.done = 0

    def begin(self):
        with self.lock:
            self.started += 1

    def end(self):
        with self.lock:
            self.done += 1


class SleepyRouter:
    """睡一小会儿再交结果；不看 cancel，故意在关窗之后也照样把结果交出去。"""

    def __init__(self, counter, rng):
        self.counter, self.rng = counter, rng

    def vllm_down(self):
        return False

    def translate(self, segments, direction, on_result, cancel=None):
        self.counter.begin()
        try:
            time.sleep(self.rng.choice([0, 0, 0.001, 0.01]))
            for _ in range(FLOOD):  # 连续交很多次结果，拉长「窗口正被销毁时线程还在交结果」的重叠时间
                for s in segments:
                    if not s.skip:
                        on_result(SegmentResult(s.idx, f"译{s.idx}", "vllm"))
        finally:
            self.counter.end()

    def retranslate_one(self, seg, direction, current):
        self.counter.begin()
        try:
            time.sleep(self.rng.uniform(0, 0.02))
            return SegmentResult(seg.idx, "新译", "fallback")
        finally:
            self.counter.end()


def test_closing_while_workers_run_never_crashes(qtbot):
    rng = random.Random(20261009)
    counter = _Counter()
    thread_errors = []
    old_hook = threading.excepthook
    threading.excepthook = lambda args: thread_errors.append(args.exc_value)
    try:
        windows = []
        for i in range(ROUNDS):
            # 识别多数时候立刻返回：最危险的就是「线程交结果」和「窗口释放」挤在同一瞬间
            def ocr(image, _d=rng.choice([0, 0, 0, 0, 0.001, 0.005, 0.02])):
                counter.begin()
                try:
                    time.sleep(_d)
                    return [OcrLine("Hello world", Box(10, 10, 150, 20)), OcrLine("Second para", Box(10, 60, 150, 20))]
                finally:
                    counter.end()

            s = Session(Image.new("RGB", (300, 120), "white"))
            w = OverlayWindow(s, QRect(100, 200, 300, 120), ocr, SleepyRouter(counter, rng), None)
            w.show()
            delay = rng.choice([0, 0, 1, 3, 8, 15, 25])  # 毫秒；0 = 刚建好就关
            if delay:
                qtbot.wait(delay)
            if i % 3 == 0 and s.segments:  # 有段了就顺手发一次单段重翻，让重翻线程也参与竞争
                w._retranslate(s.segments[0])
            w.close()
            # 和托盘程序一样：关窗后主线程不再持有窗口（app 会把它从 overlays 里摘掉）。
            # 旧设计里工作线程的闭包捕获了 self，于是最后一个引用在工作线程里释放 -> 崩溃。
            windows.append(weakref.ref(w))
            del w
            QApplication.processEvents()

        # 等所有后台线程都收工（它们会在窗口释放之后才交结果），期间一直处理事件
        qtbot.waitUntil(lambda: counter.done == counter.started, timeout=10000)
        qtbot.wait(100)
        qtbot.waitUntil(lambda: counter.done == counter.started, timeout=10000)
        QApplication.processEvents()
        gc.collect()
        assert all(r() is None or not shiboken6.isValid(r()) for r in windows)  # 窗口真的都释放了
        assert counter.started >= ROUNDS  # 每轮至少跑过识别线程
    finally:
        threading.excepthook = old_hook
    assert thread_errors == []

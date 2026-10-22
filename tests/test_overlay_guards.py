"""浮窗的守卫专属测试：每个测试只触发一个守卫，期望值全部写死。

窗口带 WA_DeleteOnClose，所以不用 qtbot.addWidget，而用 track 自己收尾。"""
import gc
import threading
import weakref

import pytest
import shiboken6  # noqa: F401  (测试里用 isValid 检查窗口已释放)
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QContextMenuEvent, QGuiApplication, QMouseEvent

from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.overlay import OverlayWindow
from shotrans.session import Session


class Router:
    """可控的假路由：auto=True 时立刻给每个非跳过段 vllm 译文；auto=False 时什么都不回。"""

    def __init__(self, auto=True, down=False, boom=None):
        self.auto, self.down, self.boom = auto, down, boom
        self.last_kind = None  # 熔断原因；只在 down 时有意义（和真路由一致）
        self.calls = []
        self.cancels = []
        self.retranslate_calls = []
        self.retranslate_result = "default"

    def vllm_down(self):
        return self.down

    def vllm_last_error_kind(self):
        return self.last_kind if self.down else None

    def translate(self, segments, direction, on_result, cancel=None):
        self.calls.append(([s.idx for s in segments], direction))
        self.cancels.append(cancel)
        if self.boom:
            raise RuntimeError(self.boom)
        if self.auto:
            for s in segments:
                if not s.skip:
                    on_result(SegmentResult(s.idx, f"译{s.idx}", "vllm"))

    def retranslate_one(self, seg, direction, current):
        self.retranslate_calls.append((seg.idx, direction, current))
        if self.retranslate_result == "default":
            return SegmentResult(seg.idx, "新译", "fallback")
        return self.retranslate_result


class History:
    def __init__(self, boom=False):
        self.entries, self.boom = [], boom

    def add(self, e):
        if self.boom:
            raise OSError("磁盘满了")
        self.entries.append(e)


TWO_LINES = [OcrLine("Hello world", Box(10, 10, 150, 20)), OcrLine("Second para", Box(10, 60, 150, 20))]


@pytest.fixture
def make(qtbot, track):
    def _make(lines=TWO_LINES, router=None, history=None, rect=QRect(100, 200, 300, 120), image_size=(300, 120),
              wait=True, ocr=None):
        router = router if router is not None else Router()
        s = Session(Image.new("RGB", image_size, "white"))
        w = OverlayWindow(s, rect, ocr or (lambda img: list(lines)), router, history)
        track(w)
        w.show()
        if wait:
            qtbot.waitUntil(lambda: bool(s.segments) or not lines, timeout=3000)  # 识别完成
            if any(not seg.skip for seg in s.segments):  # 有要翻译的段 -> 等翻译线程真的开跑
                qtbot.waitUntil(lambda: bool(router.calls), timeout=3000)
        return w

    return _make


def _settle(qtbot, w, n=2):
    qtbot.waitUntil(lambda: len(w.session.results) == n, timeout=3000)


def _mouse(kind, local, glob, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, QPointF(local), QPointF(glob), button, buttons, Qt.KeyboardModifier.NoModifier)


# ---------- 过期结果的守卫（任务书变异 2、3） ----------
def test_stale_result_is_dropped(qtbot, make):
    w = make()
    _settle(qtbot, w)
    old = w.session.generation
    w.toggle_mode()
    qtbot.waitUntil(lambda: len(w._router.calls) == 2 and len(w.session.results) == 2, timeout=3000)
    w._on_result(old, SegmentResult(1, "旧", "vllm"))
    assert w.session.results[1].text == "译1"


def test_current_result_is_applied(qtbot, make):
    w = make(router=Router(auto=False))
    qtbot.waitUntil(lambda: len(w._router.calls) == 1, timeout=3000)
    w._on_result(w.session.generation, SegmentResult(1, "新来的", "vllm"))
    assert w.session.results[1].text == "新来的"


def test_stale_finished_does_not_write_history(qtbot, make):
    h = History()
    w = make(history=h)
    qtbot.waitUntil(lambda: len(h.entries) == 1, timeout=3000)
    w._on_finished(w.session.generation - 1)
    assert len(h.entries) == 1


def test_current_finished_writes_history(qtbot, make):
    h = History()
    w = make(history=h)
    qtbot.waitUntil(lambda: len(h.entries) == 1, timeout=3000)
    w._on_finished(w.session.generation)
    assert len(h.entries) == 2


def test_history_failure_is_swallowed(qtbot, make):
    h = History(boom=True)
    w = make(history=h)
    _settle(qtbot, w)
    w._on_finished(w.session.generation)  # 不抛即通过
    assert h.entries == []


def test_history_none_is_fine(qtbot, make, caplog):
    w = make(history=None)
    _settle(qtbot, w)
    w._on_finished(w.session.generation)
    assert w.isVisible()
    assert "写历史失败" not in caplog.text


def test_cancelled_translation_does_not_emit_finished(qtbot, make):
    """路由返回时 cancel 已被置位（同一代）：不能写历史。"""
    h = History()

    class Cancelling(Router):
        def translate(self, segments, direction, on_result, cancel=None):
            self.calls.append(([s.idx for s in segments], direction))
            for s in segments:
                on_result(SegmentResult(s.idx, f"译{s.idx}", "vllm"))
            cancel.set()

    w = make(router=Cancelling(), history=h)
    _settle(qtbot, w)
    qtbot.wait(300)
    assert h.entries == []


def test_history_not_written_after_flip_until_new_results(qtbot, make):
    h = History()
    w = make(history=h)
    qtbot.waitUntil(lambda: len(h.entries) == 1, timeout=3000)
    w.flip_direction()
    qtbot.waitUntil(lambda: len(h.entries) == 2, timeout=3000)
    assert h.entries[1]["direction"] == "zh2en"


def test_real_history_keeps_one_entry_per_screenshot(qtbot, make, tmp_path):
    """切 中⇄英、行/段 会重翻多次：历史文件里同一张截图只留最新一条。"""
    from shotrans.history import History as RealHistory

    h = RealHistory(tmp_path / "history.json")
    w = make(history=h)
    qtbot.waitUntil(lambda: len(h.load()) == 1, timeout=3000)
    w.flip_direction()
    qtbot.waitUntil(lambda: h.load()[-1]["direction"] == "zh2en", timeout=3000)
    w.toggle_mode()
    qtbot.waitUntil(lambda: len(w._router.calls) == 3, timeout=3000)
    qtbot.wait(150)
    items = h.load()
    assert len(items) == 1 and items[0]["direction"] == "zh2en"
    other = make(history=h)  # 另一张截图：另起一条
    qtbot.waitUntil(lambda: len(h.load()) == 2, timeout=3000)
    assert other.session.id != w.session.id


# ---------- 布局 ----------
def test_bar_above_when_room(make):
    w = make(rect=QRect(100, 200, 300, 120))
    assert w._img_rect.y() == 28
    assert w._bar.geometry().y() == 0
    assert w.geometry() == QRect(100, 172, 460, 148)


def test_bar_below_when_selection_touches_screen_top(make):
    top = QGuiApplication.primaryScreen().geometry().top()
    w = make(rect=QRect(100, top, 300, 120))
    assert w._img_rect.y() == 0
    assert w._bar.geometry().y() == 120
    assert w.geometry() == QRect(100, top, 460, 148)
    assert w.mapToGlobal(w._img_rect.topLeft()) == QPoint(100, top)


def test_bar_below_when_only_27px_room(make):
    top = QGuiApplication.primaryScreen().geometry().top()
    w = make(rect=QRect(100, top + 27, 300, 120))
    assert w._img_rect.y() == 0


def test_bar_above_when_exactly_28px_room(make):
    top = QGuiApplication.primaryScreen().geometry().top()
    w = make(rect=QRect(100, top + 28, 300, 120))
    assert w._img_rect.y() == 28


def test_narrow_selection_at_right_edge_keeps_toolbar_on_screen(make):
    g = QGuiApplication.primaryScreen().geometry()
    rect = QRect(g.left() + g.width() - 100, g.top() + 200, 100, 120)
    w = make(rect=rect, image_size=(100, 120))
    assert w.geometry().left() + w.geometry().width() == g.left() + g.width()  # 往左伸，没出屏幕
    assert w.mapToGlobal(w._img_rect.topLeft()) == rect.topLeft()  # 截图仍盖在原选区上


def test_window_width_is_selection_width_when_wide(make):
    w = make(rect=QRect(100, 200, 700, 120), image_size=(700, 120))
    assert w.width() == 700
    assert w._bar.width() == 700
    assert w._img_rect.width() == 700


def test_window_width_has_minimum_when_narrow(make):
    w = make(rect=QRect(100, 200, 100, 120), image_size=(100, 120))
    assert w.width() == 460
    assert w._img_rect.width() == 100


# ---------- 键盘 ----------
def test_plain_d_flips_but_ctrl_d_does_not(qtbot, make):
    w = make()
    _settle(qtbot, w)
    qtbot.keyClick(w, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert w.session.direction == "en2zh"
    assert len(w._router.calls) == 1


def test_plain_c_does_not_copy(qtbot, make):
    w = make()
    _settle(qtbot, w)
    QGuiApplication.clipboard().setText("原样")
    qtbot.keyClick(w, Qt.Key.Key_C)
    assert QGuiApplication.clipboard().text() == "原样"


def test_toggle_mode_back_to_para(qtbot, make):
    w = make()
    _settle(qtbot, w)
    w.toggle_mode()
    w.toggle_mode()
    assert w.session.mode == "para"


# ---------- 状态栏文字 ----------
@pytest.fixture
def quiet(make):
    """不自动出译文的窗口，状态由测试手动设置。"""
    return make(router=Router(auto=False))


def test_status_pending_text(quiet):
    assert "翻译中…" in quiet._status.text()
    assert "原文" not in quiet._status.text()


def test_status_skipped_when_all_skip(make):
    # 改动说明：原先断言「没有识别到文字」；只有公式时现在如实显示「只有公式/数字，未翻译」
    w = make(lines=[OcrLine("E = mc^2", Box(10, 10, 150, 20))])
    assert "只有公式/数字，未翻译" in w._status.text()
    assert "没有识别到文字" not in w._status.text()


def test_status_vllm_not_marked_down_even_if_router_down(quiet):
    quiet._router.down = True
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "a", "vllm"))
    quiet._refresh()
    assert "主服务 · 译文" in quiet._status.text()
    assert "掉线" not in quiet._status.text()


def test_status_fallback_when_vllm_up(quiet):
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "a", "fallback"))
    quiet._refresh()
    assert "备用 · 译文" in quiet._status.text()
    assert "掉线" not in quiet._status.text()
    assert "#3b82f6" in quiet._status.text()


def test_status_fallback_when_vllm_down(quiet):
    quiet._router.down = True
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "a", "fallback"))
    quiet._refresh()
    assert "备用（主服务掉线中） · 译文" in quiet._status.text()


def test_status_fallback_when_vllm_key_rejected(quiet):
    quiet._router.down, quiet._router.last_kind = True, "auth"
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "a", "fallback"))
    quiet._refresh()
    assert "备用（主服务 key 无效或未填） · 译文" in quiet._status.text()
    assert "掉线" not in quiet._status.text()


def test_status_fallback_when_vllm_down_for_connect_says_offline(quiet):
    quiet._router.down, quiet._router.last_kind = True, "connect"
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "a", "fallback"))
    quiet._refresh()
    assert "备用（主服务掉线中） · 译文" in quiet._status.text()


def test_status_none_when_vllm_key_rejected(quiet):
    quiet._router.down, quiet._router.last_kind = True, "auth"
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "Hello world", "none", note="翻译服务不可用"))
    quiet._refresh()
    assert "翻译服务不可用（主服务 key 无效或未填）" in quiet._status.text()
    assert "#ef4444" in quiet._status.text()


def test_status_none_without_auth_has_plain_text(quiet):
    quiet._router.down, quiet._router.last_kind = True, "connect"
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "Hello world", "none", note="翻译服务不可用"))
    quiet._refresh()
    assert "key" not in quiet._status.text()


def test_status_none_with_router_none_does_not_crash(quiet):
    quiet._router = None
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "Hello world", "none", note="翻译服务不可用"))
    quiet._refresh()
    assert "翻译服务不可用 · 译文" in quiet._status.text()


def test_status_fallback_with_router_none_does_not_crash(quiet):
    quiet._router = None
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "a", "fallback"))
    quiet._refresh()
    assert "备用 · 译文" in quiet._status.text()


def test_status_none_service(quiet):
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "Hello world", "none", note="服务不可用"))
    quiet._refresh()
    assert "翻译服务不可用" in quiet._status.text()
    assert "#ef4444" in quiet._status.text()


def test_status_shows_original_view(quiet):
    quiet.session.apply(quiet.session.generation, SegmentResult(1, "a", "vllm"))
    quiet.toggle_view()
    assert "主服务 · 原文" in quiet._status.text()


def test_on_failed_shows_message_and_survives_refresh(quiet):
    quiet._on_failed(quiet.session.generation, "炸了")  # 传进来的是原始错误，前缀由状态栏自己加
    assert "翻译出错：炸了" in quiet._status.text()
    assert quiet._status.text().count("翻译出错") == 1  # 前缀只加一次（和识别失败那条对应）
    assert "#ef4444" in quiet._status.text()
    quiet.toggle_view()
    assert "翻译出错：炸了" in quiet._status.text()


class FlakyRouter(Router):
    """第一次翻译抛错，之后正常出译文。"""

    def translate(self, segments, direction, on_result, cancel=None):
        if not self.calls:
            self.calls.append(([s.idx for s in segments], direction))
            raise RuntimeError("第一次炸")
        super().translate(segments, direction, on_result, cancel)


@pytest.mark.parametrize("action", ["flip_direction", "toggle_mode"])
def test_translate_error_cleared_after_successful_retranslation(qtbot, make, action):
    w = make(router=FlakyRouter())
    qtbot.waitUntil(lambda: "翻译出错：第一次炸" in w._status.text(), timeout=3000)
    getattr(w, action)()
    qtbot.waitUntil(lambda: len(w.session.results) == 2, timeout=3000)
    assert "翻译出错" not in w._status.text()
    assert "主服务" in w._status.text()


def test_error_cleared_on_start_translate_even_before_results(qtbot, make):
    w = make(router=Router(auto=False))
    w._on_failed(w.session.generation, "旧的")
    w.flip_direction()
    assert w._error is None
    assert "翻译出错" not in w._status.text()


def test_stale_translate_failure_is_ignored(qtbot, make):
    w = make(router=Router(auto=False))
    old = w.session.generation
    w.flip_direction()
    w._on_failed(old, "翻译出错：过期线程")
    assert w._error is None
    assert "翻译出错" not in w._status.text()


def test_cancelled_run_does_not_report_failure(qtbot, make):
    """被取消（关窗/重翻）的那一轮线程晚到抛错，不能把错误带上来。"""
    release = threading.Event()

    class LateBoom(Router):
        def translate(self, segments, direction, on_result, cancel=None):
            self.calls.append(([s.idx for s in segments], direction))
            if len(self.calls) == 1:
                release.wait(3)
                raise RuntimeError("晚到的炸")
            for s in segments:
                if not s.skip:
                    on_result(SegmentResult(s.idx, f"译{s.idx}", "vllm"))

    w = make(router=LateBoom())
    w.flip_direction()  # 取消第一轮；第二轮正常
    _settle(qtbot, w)
    release.set()
    qtbot.wait(300)
    assert w._error is None
    assert "翻译出错" not in w._status.text()


def test_ocr_error_stays_after_later_translate_start(qtbot, track):
    def bad_ocr(image):
        raise RuntimeError("坏了")

    s = Session(Image.new("RGB", (300, 120), "white"))
    w = OverlayWindow(s, QRect(100, 200, 300, 120), bad_ocr, Router(), None)
    track(w)
    w.show()
    qtbot.waitUntil(lambda: "识别失败：坏了" in w._status.text(), timeout=3000)
    w._start_translate()
    assert "识别失败：坏了" in w._status.text()


# ---------- 后台消息队列（线程只往队列放元组，界面定时器取） ----------
@pytest.fixture
def thread_errors():
    """收集工作线程里没接住的异常（默认只会打印，测试看不到）。"""
    got = []
    old = threading.excepthook
    threading.excepthook = lambda args: got.append(args.exc_value)
    yield got
    threading.excepthook = old


def test_queue_stale_result_dropped(qtbot, make):
    w = make()
    _settle(qtbot, w)
    old = w.session.generation
    w.toggle_mode()
    qtbot.waitUntil(lambda: len(w._router.calls) == 2 and len(w.session.results) == 2, timeout=3000)
    w._inbox.put(("result", old, SegmentResult(1, "队列里的旧结果", "vllm")))
    w._drain()
    assert w.session.results[1].text == "译1"


def test_queue_current_result_applied_by_timer(qtbot, make):
    w = make(router=Router(auto=False))
    qtbot.waitUntil(lambda: len(w._router.calls) == 1, timeout=3000)
    w._inbox.put(("result", w.session.generation, SegmentResult(1, "定时器送来的", "vllm")))
    qtbot.waitUntil(lambda: 1 in w.session.results, timeout=3000)  # 不手动 _drain：靠定时器
    assert w.session.results[1].text == "定时器送来的"


def test_queue_stale_retranslated_dropped(qtbot, make):
    w = make()
    _settle(qtbot, w)
    old = w.session.generation
    w.flip_direction()
    qtbot.waitUntil(lambda: len(w._router.calls) == 2 and len(w.session.results) == 2, timeout=3000)
    w._inbox.put(("retranslated", old, SegmentResult(1, "队列里的旧重翻", "fallback")))
    w._drain()
    assert w.session.results[1].text == "译1"


def test_queue_stale_finished_does_not_write_history(qtbot, make):
    h = History()
    w = make(history=h)
    qtbot.waitUntil(lambda: len(h.entries) == 1, timeout=3000)
    w._inbox.put(("finished", w.session.generation - 1, None))
    w._drain()
    assert len(h.entries) == 1


def test_history_written_exactly_once_per_finished_generation(qtbot, make):
    h = History()
    w = make(history=h)
    qtbot.waitUntil(lambda: len(h.entries) >= 1, timeout=3000)
    qtbot.waitUntil(lambda: w._inbox.empty(), timeout=3000)  # 队列已被取空：没有还没处理的 finished
    for _ in range(3):  # 再主动处理几轮，看会不会冒出第二条
        w._drain()
        qtbot.wait(60)
    assert len(h.entries) == 1


def test_pump_timer_runs_while_open_and_stops_on_close(qtbot, make):
    w = make()
    pump = w._pump
    assert pump.isActive()
    assert pump.interval() == 30
    w.close()
    assert not pump.isActive()


def test_late_ocr_put_after_window_freed_is_harmless(qtbot, track, thread_errors):
    """识别线程在窗口已经释放之后才交结果：只是往没人读的队列里放，不报错、不崩。"""
    release, done = threading.Event(), threading.Event()

    def slow_ocr(image):
        release.wait(3)
        return list(TWO_LINES)

    s = Session(Image.new("RGB", (300, 120), "white"))
    w = OverlayWindow(s, QRect(100, 200, 300, 120), slow_ocr, Router(), None)
    track(w)
    w.show()
    inbox = w._inbox
    w.close()
    qtbot.waitUntil(lambda: not shiboken6.isValid(w), timeout=3000)
    release.set()
    qtbot.waitUntil(lambda: not inbox.empty(), timeout=3000)
    kind, gen, payload = inbox.get_nowait()
    assert (kind, gen, len(payload)) == ("lines", -1, 2)
    qtbot.wait(100)
    assert thread_errors == []


def test_late_translate_and_retranslate_put_after_window_freed_is_harmless(qtbot, make, thread_errors):
    release = threading.Event()

    class Slow(Router):
        def translate(self, segments, direction, on_result, cancel=None):
            self.calls.append(([s.idx for s in segments], direction))
            release.wait(3)
            on_result(SegmentResult(1, "晚到", "vllm"))

        def retranslate_one(self, seg, direction, current):
            self.retranslate_calls.append(seg.idx)
            release.wait(3)
            return SegmentResult(seg.idx, "晚到的重翻", "fallback")

    r = Slow()
    w = make(router=r)
    w._retranslate(w.session.segments[0])
    qtbot.waitUntil(lambda: r.retranslate_calls == [1], timeout=3000)
    inbox = w._inbox
    w.close()
    qtbot.waitUntil(lambda: not shiboken6.isValid(w), timeout=3000)
    release.set()
    qtbot.waitUntil(lambda: inbox.qsize() == 2, timeout=3000)
    got = sorted(inbox.get_nowait()[0] for _ in range(2))
    assert got == ["result", "retranslated"]  # 关窗已置 cancel，所以没有 finished
    qtbot.wait(100)
    assert thread_errors == []


def test_worker_thread_does_not_keep_window_alive(qtbot):
    """线程还卡在翻译里时，关窗 + 主线程放掉引用 -> 窗口对象能被回收（线程没捕获 self）。

    旧设计的线程闭包捕获了 self，窗口最后一个引用会在工作线程里释放，这正是崩溃的根源。"""
    release = threading.Event()

    class Blocking(Router):
        def translate(self, segments, direction, on_result, cancel=None):
            self.calls.append(1)
            release.wait(5)

    r = Blocking()
    s = Session(Image.new("RGB", (300, 120), "white"))
    w = OverlayWindow(s, QRect(100, 200, 300, 120), lambda img: list(TWO_LINES), r, None)
    w.show()
    qtbot.waitUntil(lambda: r.calls == [1], timeout=3000)
    ref = weakref.ref(w)
    w.close()
    del w
    try:
        qtbot.wait(50)  # 让 deleteLater 跑完
        gc.collect()
        assert ref() is None
    finally:
        release.set()


@pytest.mark.real_clipboard
def test_real_clipboard_roundtrip():
    """唯一碰真剪贴板的冒烟测试：直接读写，不经事件派发。"""
    import ctypes
    import time as _time

    user32 = ctypes.windll.user32
    for _ in range(5):  # 别的程序（剪贴板历史、远程桌面）可能正占着：每 100 毫秒试一次，共 5 次
        if user32.OpenClipboard(0):
            user32.CloseClipboard()
            break
        _time.sleep(0.1)
    else:
        pytest.skip("剪贴板被其他程序占用")
    cb = QGuiApplication.clipboard()
    old = cb.text()
    try:
        cb.setText("shotrans-smoke")
        assert cb.text() == "shotrans-smoke"
    finally:
        cb.setText(old)


def test_translate_exception_shown_and_no_history(qtbot, make):
    h = History()
    w = make(router=Router(boom="路由炸了"), history=h)
    qtbot.waitUntil(lambda: "翻译出错：路由炸了" in w._status.text(), timeout=3000)
    qtbot.wait(100)
    assert h.entries == []


# ---------- 线程与取消 ----------
def test_no_translate_thread_when_all_segments_skip(qtbot, make):
    h = History()
    r = Router()
    w = make(lines=[OcrLine("E = mc^2", Box(10, 10, 150, 20))], router=r, history=h)
    qtbot.waitUntil(lambda: len(w.session.segments) == 1, timeout=3000)
    qtbot.wait(200)
    assert r.calls == []
    assert h.entries == []


def test_ocr_returning_nothing_is_empty_status(qtbot, make):
    r = Router()
    w = make(lines=[], router=r)
    qtbot.waitUntil(lambda: "没有识别到文字" in w._status.text(), timeout=3000)
    assert r.calls == []


def test_close_sets_cancel(qtbot, make):
    w = make()
    _settle(qtbot, w)
    cancel = w._cancel
    assert not cancel.is_set()
    w.close()
    assert cancel.is_set()


def test_new_translation_cancels_previous_and_passes_fresh_event(qtbot, make):
    w = make()
    _settle(qtbot, w)
    first = w._router.cancels[0]
    assert not first.is_set()
    w.toggle_mode()
    qtbot.waitUntil(lambda: len(w._router.cancels) == 2, timeout=3000)
    second = w._router.cancels[1]
    assert first.is_set()
    assert second is not first and not second.is_set()


def test_cancelled_translation_does_not_write_history(qtbot, make):
    h = History()

    class Slow(Router):
        def translate(self, segments, direction, on_result, cancel=None):
            self.calls.append(([s.idx for s in segments], direction))
            self.started = True
            cancel.wait(3)

    r = Slow()
    w = make(router=r, history=h)
    qtbot.waitUntil(lambda: getattr(r, "started", False), timeout=3000)
    w.close()
    qtbot.wait(300)
    assert h.entries == []


def test_closed_signal_carries_window_once(qtbot, make):
    w = make()
    got = []
    w.closed.connect(got.append)
    w.close()
    assert len(got) == 1 and got[0] is w


# ---------- 重翻 ----------
def test_retranslate_result_none_leaves_results_untouched(qtbot, make, tip):
    r = Router()
    r.retranslate_result = None
    w = make(router=r)
    _settle(qtbot, w)
    w._retranslate(w.session.segments[0])
    qtbot.waitUntil(lambda: len(r.retranslate_calls) == 1, timeout=3000)
    qtbot.waitUntil(lambda: "没有可换的翻译服务，或重翻失败" in tip.shown, timeout=3000)
    assert w.session.results[1].text == "译1"
    assert w.session.can_undo(1) is False


def test_retranslate_stale_generation_dropped(qtbot, make):
    w = make()
    _settle(qtbot, w)
    old = w.session.generation
    w.toggle_mode()
    qtbot.waitUntil(lambda: len(w.session.results) == 2 and len(w._router.calls) == 2, timeout=3000)
    w._on_retranslated((old, SegmentResult(1, "过期的重翻", "fallback")))
    assert w.session.results[1].text == "译1"


def test_retranslate_current_generation_applied(qtbot, make):
    w = make()
    _settle(qtbot, w)
    w._on_retranslated((w.session.generation, SegmentResult(1, "新译", "fallback")))
    assert w.session.results[1].text == "新译"


def test_retranslate_without_result_uses_none_as_current(qtbot, make):
    r = Router(auto=False)
    w = make(router=r)
    qtbot.waitUntil(lambda: len(r.calls) == 1, timeout=3000)
    w._retranslate(w.session.segments[0])
    qtbot.waitUntil(lambda: r.retranslate_calls == [(1, "en2zh", "none")], timeout=3000)


def test_retranslate_exception_becomes_none(qtbot, make, tip):
    class Bad(Router):
        def retranslate_one(self, seg, direction, current):
            self.retranslate_calls.append(1)
            raise RuntimeError("坏了")

    r = Bad()
    w = make(router=r)
    _settle(qtbot, w)
    w._retranslate(w.session.segments[0])
    qtbot.waitUntil(lambda: len(r.retranslate_calls) == 1, timeout=3000)
    qtbot.waitUntil(lambda: "没有可换的翻译服务，或重翻失败" in tip.shown, timeout=3000)
    assert w.session.results[1].text == "译1"


# ---------- 鼠标 ----------
def test_double_click_uses_scale_and_offset(qtbot, make):
    # 截图是选区的 2 倍大：窗口坐标 (175, 28+60) 对应截图坐标 (350, 120)
    lines = [OcrLine("Hello world", Box(300, 100, 200, 40))]
    w = make(lines=lines, image_size=(600, 240))
    _settle(qtbot, w, 1)
    pos = QPoint(175, 28 + 60)
    QGuiApplication.clipboard().setText("原样")
    qtbot.mouseDClick(w, Qt.MouseButton.LeftButton, pos=pos)
    assert QGuiApplication.clipboard().text() == "译1"


def test_double_click_ignores_offset_mistakes(qtbot, make):
    lines = [OcrLine("Hello world", Box(300, 100, 200, 40))]
    w = make(lines=lines, image_size=(600, 240))
    _settle(qtbot, w, 1)
    QGuiApplication.clipboard().setText("原样")
    # 没加工具栏偏移时本该命中的位置：y=60 -> 截图 y=120 的话差 56 像素 -> 实际是 (175, 60-28)*2 = (350, 64) 未命中
    qtbot.mouseDClick(w, Qt.MouseButton.LeftButton, pos=QPoint(175, 60))
    assert QGuiApplication.clipboard().text() == "原样"


def test_double_click_on_blank_does_not_copy(qtbot, make):
    w = make()
    _settle(qtbot, w)
    QGuiApplication.clipboard().setText("原样")
    qtbot.mouseDClick(w, Qt.MouseButton.LeftButton, pos=QPoint(250, 28 + 100))
    assert QGuiApplication.clipboard().text() == "原样"


def test_double_click_on_toolbar_does_not_copy(qtbot, make):
    w = make()
    _settle(qtbot, w)
    QGuiApplication.clipboard().setText("原样")
    qtbot.mouseDClick(w, Qt.MouseButton.LeftButton, pos=QPoint(20, 15))  # 在工具栏范围、图片之外
    assert QGuiApplication.clipboard().text() == "原样"


def test_double_click_on_skip_segment_copies_original(qtbot, make):
    lines = [OcrLine("Hello world", Box(10, 10, 150, 20)), OcrLine("E = mc^2", Box(10, 60, 150, 20))]
    w = make(lines=lines)
    _settle(qtbot, w, 1)
    QGuiApplication.clipboard().setText("原样")
    qtbot.mouseDClick(w, Qt.MouseButton.LeftButton, pos=QPoint(20, 28 + 65))
    assert QGuiApplication.clipboard().text() == "E = mc^2"


def _drag_setup(make):
    w = make()
    w.move(300, 300)
    start = w.pos()
    w.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPoint(50, 50), QPoint(1000, 600)))
    return w, start


def test_small_jitter_does_not_drag(make):
    w, start = _drag_setup(make)
    # 曼哈顿距离正好 3：不算拖动
    w.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(52, 51), QPoint(1002, 601), Qt.MouseButton.NoButton))
    assert w.pos() == start
    assert w._moved is False


def test_move_past_threshold_drags_window(make):
    w, start = _drag_setup(make)
    # 曼哈顿距离 4：开始拖动，窗口跟着整个位移走
    w.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(53, 51), QPoint(1003, 601), Qt.MouseButton.NoButton))
    assert w._moved is True
    assert w.pos() == start + QPoint(3, 1)
    w.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(80, 80), QPoint(1030, 630), Qt.MouseButton.NoButton))
    assert w.pos() == start + QPoint(30, 30)


def test_drag_is_sticky_once_started(make):
    w, start = _drag_setup(make)
    w.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(60, 50), QPoint(1010, 600), Qt.MouseButton.NoButton))
    # 又回到阈值内：仍然跟手，不跳回
    w.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(51, 50), QPoint(1001, 600), Qt.MouseButton.NoButton))
    assert w.pos() == start + QPoint(1, 0)


def test_release_ends_drag_and_move_starts_hover_timer(make):
    w, start = _drag_setup(make)
    w.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, QPoint(50, 50), QPoint(1000, 600)))
    assert w._press_global is None
    w.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(70, 70), QPoint(1020, 620),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton))
    assert w.pos() == start
    assert w._hover_timer.isActive()
    assert w._hover_pos == QPoint(70, 70)


def test_right_press_does_not_start_drag(make):
    w = make()
    start = w.pos()
    w.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPoint(50, 50), QPoint(1000, 600),
                             Qt.MouseButton.RightButton, Qt.MouseButton.RightButton))
    assert w._press_global is None
    w.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(90, 90), QPoint(1040, 640),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton))
    assert w.pos() == start


# ---------- 悬停 ----------
class _Tip:
    """QToolTip 的替身：真的提示框有延迟隐藏，text() 不可靠，这里只记录 showText 的文字。"""
    shown = []

    @staticmethod
    def showText(pos, text, widget=None):
        _Tip.shown.append(text)

    @staticmethod
    def hideText():
        pass


@pytest.fixture(autouse=True)
def tip(monkeypatch):
    _Tip.shown = []
    monkeypatch.setattr("shotrans.overlay.QToolTip", _Tip)
    return _Tip


def test_hover_shows_original_in_translation_view(qtbot, make, tip):
    w = make()
    _settle(qtbot, w)
    w._hover_pos = QPoint(20, 28 + 15)
    w._show_hover()
    assert tip.shown == ["Hello world"]


def test_hover_shows_translation_in_original_view(qtbot, make, tip):
    w = make()
    _settle(qtbot, w)
    w.toggle_view()
    w._hover_pos = QPoint(20, 28 + 15)
    w._show_hover()
    assert tip.shown == ["译1"]


def test_hover_on_blank_shows_nothing(qtbot, make, tip):
    w = make()
    _settle(qtbot, w)
    tip.shown.clear()
    w._hover_pos = QPoint(250, 28 + 100)
    w._show_hover()
    assert tip.shown == []


def test_hover_on_skip_segment_in_original_view_shows_nothing(qtbot, make, tip):
    lines = [OcrLine("Hello world", Box(10, 10, 150, 20)), OcrLine("E = mc^2", Box(10, 60, 150, 20))]
    w = make(lines=lines)
    _settle(qtbot, w, 1)
    w.toggle_view()
    tip.shown.clear()
    w._hover_pos = QPoint(20, 28 + 65)
    w._show_hover()
    assert tip.shown == []


def test_hover_on_none_segment_shows_original(qtbot, make, tip):
    r = Router(auto=False)
    w = make(router=r)
    qtbot.waitUntil(lambda: len(r.calls) == 1, timeout=3000)
    w.session.apply(w.session.generation, SegmentResult(1, "Hello world", "none", note="不可用"))
    w._hover_pos = QPoint(20, 28 + 15)
    w._show_hover()
    # 改动说明：显示原文的段现在在悬停里附上原因（规格 §5.3），原先只有 "Hello world"
    assert tip.shown == ["Hello world\n（不可用）"]


def test_hover_timer_fires_show_hover_after_mouse_move(qtbot, make, tip):
    w = make()
    _settle(qtbot, w)
    w.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, QPoint(20, 28 + 15), QPoint(1020, 615),
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton))
    assert tip.shown == []
    qtbot.waitUntil(lambda: tip.shown == ["Hello world"], timeout=3000)


def test_leave_stops_hover_timer_and_hides_bar_enter_shows(qtbot, make):
    w = make()
    _settle(qtbot, w)
    w.enterEvent(None)
    assert w._bar.isVisible()
    w._hover_timer.start()
    w.leaveEvent(None)
    assert not w._bar.isVisible()
    assert not w._hover_timer.isActive()


def test_close_stops_hover_timer(qtbot, make):
    w = make()
    _settle(qtbot, w)
    timer = w._hover_timer
    timer.start()
    w.close()
    assert not timer.isActive()


# ---------- 右键菜单 ----------
class _Action:
    def __init__(self, text):
        self._text, self._enabled = text, True

    def text(self):
        return self._text

    def isEnabled(self):
        return self._enabled

    def setEnabled(self, v):
        self._enabled = v


@pytest.fixture
def menu(monkeypatch):
    """QMenu 的替身：真菜单 exec 会阻塞测试。"""
    state = {"pick": None, "calls": 0, "open_during": None, "items": None, "owner": None, "raise": False}

    class FakeMenu:
        def __init__(self, parent=None):
            self.acts = []

        def addAction(self, text):
            a = _Action(text)
            self.acts.append(a)
            return a

        def addSeparator(self):
            pass

        def exec(self, pos):
            state["calls"] += 1
            state["items"] = {a.text(): a.isEnabled() for a in self.acts}
            state["open_during"] = state["owner"]._menu_open
            if state["raise"]:
                raise RuntimeError("x")
            return next((a for a in self.acts if a.text() == state["pick"]), None)

    monkeypatch.setattr("shotrans.overlay.QMenu", FakeMenu)
    return state


def _ctx(w, pos):
    w.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, w.mapToGlobal(pos)))


def test_context_menu_flag_set_during_and_cleared_after(qtbot, make, menu):
    w = make()
    _settle(qtbot, w)
    menu["owner"] = w
    menu["pick"] = None
    _ctx(w, QPoint(20, 28 + 15))
    assert menu["calls"] == 1
    assert menu["open_during"] is True
    assert w._menu_open is False


def test_context_menu_flag_cleared_when_exec_raises(qtbot, make, menu):
    w = make()
    _settle(qtbot, w)
    menu["owner"] = w
    menu["raise"] = True
    with pytest.raises(RuntimeError):
        _ctx(w, QPoint(20, 28 + 15))
    assert w._menu_open is False


def test_context_menu_not_shown_on_blank_or_skip(qtbot, make, menu):
    lines = [OcrLine("Hello world", Box(10, 10, 150, 20)), OcrLine("E = mc^2", Box(10, 60, 150, 20))]
    w = make(lines=lines)
    _settle(qtbot, w, 1)
    menu["owner"] = w
    _ctx(w, QPoint(250, 28 + 100))
    assert menu["calls"] == 0
    _ctx(w, QPoint(20, 28 + 65))
    assert menu["calls"] == 0
    _ctx(w, QPoint(20, 28 + 15))
    assert menu["calls"] == 1


def test_context_menu_undo_disabled_until_retranslated(qtbot, make, menu):
    w = make()
    _settle(qtbot, w)
    menu["owner"] = w
    menu["pick"] = None
    _ctx(w, QPoint(20, 28 + 15))
    assert menu["items"]["撤销重翻"] is False
    assert menu["items"]["重翻这一段"] is True
    w.session.apply_retranslation(SegmentResult(1, "新译", "fallback"))
    _ctx(w, QPoint(20, 28 + 15))
    assert menu["items"]["撤销重翻"] is True


def test_context_menu_retranslate(qtbot, make, menu):
    w = make()
    _settle(qtbot, w)
    menu["owner"] = w
    menu["pick"] = "重翻这一段"
    _ctx(w, QPoint(20, 28 + 15))
    qtbot.waitUntil(lambda: w.session.results[1].text == "新译", timeout=3000)
    assert w._router.retranslate_calls == [(1, "en2zh", "vllm")]


def test_context_menu_undo(qtbot, make, menu):
    w = make()
    _settle(qtbot, w)
    w.session.apply_retranslation(SegmentResult(1, "新译", "fallback"))
    menu["owner"] = w
    menu["pick"] = "撤销重翻"
    _ctx(w, QPoint(20, 28 + 15))
    assert w.session.results[1].text == "译1"


def test_context_menu_copy_original_and_translation(qtbot, make, menu):
    w = make()
    _settle(qtbot, w)
    menu["owner"] = w
    menu["pick"] = "复制原文"
    _ctx(w, QPoint(20, 28 + 15))
    assert QGuiApplication.clipboard().text() == "Hello world"
    menu["pick"] = "复制译文"
    _ctx(w, QPoint(20, 28 + 15))
    assert QGuiApplication.clipboard().text() == "译1"


def test_context_menu_cancel_does_nothing(qtbot, make, menu):
    w = make()
    _settle(qtbot, w)
    QGuiApplication.clipboard().setText("原样")
    menu["owner"] = w
    menu["pick"] = None
    _ctx(w, QPoint(20, 28 + 15))
    assert QGuiApplication.clipboard().text() == "原样"
    assert w._router.retranslate_calls == []


# ---------- 失焦关闭 ----------
def test_active_window_does_not_close(qtbot, make, monkeypatch):
    w = make()
    monkeypatch.setattr(w, "isActiveWindow", lambda: True)
    w._close_if_inactive()
    assert w.isVisible()


def test_open_menu_prevents_close(qtbot, make, monkeypatch):
    w = make()
    monkeypatch.setattr(w, "isActiveWindow", lambda: False)
    w._menu_open = True
    w._close_if_inactive()
    assert w.isVisible()


def test_hidden_window_not_closed_on_deactivation(qtbot, make, monkeypatch):
    """截屏期间浮窗被藏起来、焦点给了框选窗口：不能当成「点到外面」关掉。"""
    w = make()
    monkeypatch.setattr(w, "isActiveWindow", lambda: False)
    w.hide()
    closed = []
    w.closed.connect(closed.append)
    w._close_if_inactive()
    assert closed == []
    w.show()
    assert w.isVisible()


def test_activation_change_when_inactive_closes_window(qtbot, make, monkeypatch):
    w = make()
    monkeypatch.setattr(w, "isActiveWindow", lambda: False)
    with qtbot.waitSignal(w.closed, timeout=2000):
        w.changeEvent(QEvent(QEvent.Type.ActivationChange))


def test_activation_change_when_active_keeps_window(qtbot, make, monkeypatch):
    w = make()
    monkeypatch.setattr(w, "isActiveWindow", lambda: True)
    w.changeEvent(QEvent(QEvent.Type.ActivationChange))
    qtbot.wait(100)
    assert w.isVisible()


def test_pin_button_toggles_pinned(qtbot, make):
    w = make()
    w._on_pin(True)
    assert w.pinned is True
    w._on_pin(False)
    assert w.pinned is False


# ---------- 复制按钮 ----------
def test_copy_buttons_text(qtbot, make):
    w = make()
    _settle(qtbot, w)
    w.copy_original()
    assert QGuiApplication.clipboard().text() == "Hello world\n\nSecond para"
    w.copy_translation()
    assert QGuiApplication.clipboard().text() == "译1\n\n译2"


def test_construct_pixmap_matches_selection_size(make):
    w = make()
    assert (w._pixmap.width(), w._pixmap.height()) == (300, 120)

"""capture 各守卫/分支的专属测试。真实屏幕相关部分用假屏幕代替。"""
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap

from shotrans import capture
from shotrans.capture import Capturer, SelectionWindow, crop_to_image

LEFT = Qt.MouseButton.LeftButton
RIGHT = Qt.MouseButton.RightButton


def _pix(w=200, h=100, color="white"):
    p = QPixmap(w, h)
    p.fill(QColor(color))
    return p


def _split_pix():
    """左半红、右半蓝（200x100）。"""
    p = _pix()
    q = QPainter(p)
    q.fillRect(QRect(0, 0, 100, 100), QColor(255, 0, 0))
    q.fillRect(QRect(100, 0, 100, 100), QColor(0, 0, 255))
    q.end()
    return p


def _window(qtbot, pix=None, geom=None):
    w = SelectionWindow(pix or _pix(), geom or QRect(0, 0, 200, 100))
    qtbot.addWidget(w)
    w.show()
    return w


# ---------- SelectionWindow ----------

def test_window_basics(qtbot):
    pix = _pix()
    w = _window(qtbot, pix, QRect(30, 40, 200, 100))
    assert w.pixmap.toImage() == pix.toImage()
    assert w.geometry() == QRect(30, 40, 200, 100)
    assert w.cursor().shape() == Qt.CursorShape.CrossCursor
    assert w.hasMouseTracking()
    flags = w.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint


def test_right_click_cancels(qtbot):
    w = _window(qtbot)
    with qtbot.waitSignal(w.cancelled):
        qtbot.mouseClick(w, RIGHT, pos=QPoint(20, 20))


def test_middle_click_does_nothing(qtbot):
    w = _window(qtbot)
    with qtbot.assertNotEmitted(w.cancelled), qtbot.assertNotEmitted(w.selected):
        qtbot.mouseClick(w, Qt.MouseButton.MiddleButton, pos=QPoint(20, 20))
    assert w._start is None


def test_release_without_press_emits_nothing(qtbot):
    w = _window(qtbot)
    with qtbot.assertNotEmitted(w.cancelled), qtbot.assertNotEmitted(w.selected):
        qtbot.mouseRelease(w, LEFT, pos=QPoint(60, 40))


def test_non_left_release_ignored_during_drag(qtbot):
    w = _window(qtbot)
    qtbot.mousePress(w, LEFT, pos=QPoint(10, 10))
    with qtbot.assertNotEmitted(w.cancelled), qtbot.assertNotEmitted(w.selected):
        qtbot.mouseRelease(w, RIGHT, pos=QPoint(60, 40))


def test_second_release_after_selection_emits_nothing(qtbot):
    w = _window(qtbot)
    with qtbot.waitSignal(w.selected):
        qtbot.mousePress(w, LEFT, pos=QPoint(10, 10))
        qtbot.mouseRelease(w, LEFT, pos=QPoint(60, 40))
    assert w._start is None and w._end is None
    with qtbot.assertNotEmitted(w.cancelled), qtbot.assertNotEmitted(w.selected):
        qtbot.mouseRelease(w, LEFT, pos=QPoint(90, 90))


def test_min_size_boundary_selects(qtbot):
    w = _window(qtbot)
    with qtbot.waitSignal(w.selected) as b:
        qtbot.mousePress(w, LEFT, pos=QPoint(10, 10))
        qtbot.mouseRelease(w, LEFT, pos=QPoint(14, 14))
    assert b.args[0] == QRect(10, 10, 4, 4)


def test_narrow_selection_cancels(qtbot):
    w = _window(qtbot)
    with qtbot.waitSignal(w.cancelled), qtbot.assertNotEmitted(w.selected):
        qtbot.mousePress(w, LEFT, pos=QPoint(10, 10))
        qtbot.mouseRelease(w, LEFT, pos=QPoint(13, 60))  # 宽 3 高 50


def test_short_selection_cancels(qtbot):
    w = _window(qtbot)
    with qtbot.waitSignal(w.cancelled), qtbot.assertNotEmitted(w.selected):
        qtbot.mousePress(w, LEFT, pos=QPoint(10, 10))
        qtbot.mouseRelease(w, LEFT, pos=QPoint(100, 13))  # 宽 90 高 3


def test_other_key_does_not_cancel(qtbot):
    w = _window(qtbot)
    with qtbot.assertNotEmitted(w.cancelled):
        qtbot.keyClick(w, Qt.Key.Key_A)


def test_mouse_move_updates_rect(qtbot):
    w = _window(qtbot)
    qtbot.mousePress(w, LEFT, pos=QPoint(10, 10))
    qtbot.mouseMove(w, QPoint(60, 40))
    assert w._rect() == QRect(10, 10, 50, 30)


def _px(w, x, y):
    img = w.grab().toImage()
    dpr = img.devicePixelRatio()
    return img.pixelColor(round(x * dpr), round(y * dpr))


def test_paint_dims_outside_and_reveals_selection(qtbot):
    w = _window(qtbot, _split_pix())
    qtbot.mousePress(w, LEFT, pos=QPoint(120, 10))
    qtbot.mouseMove(w, QPoint(160, 40))
    # 选区内：原样（纯蓝）；选区外：被压暗，不再是纯蓝/纯红
    assert _px(w, 140, 25) == QColor(0, 0, 255)
    outside_blue = _px(w, 180, 80)
    assert outside_blue.blue() < 255 and outside_blue.blue() > 0
    outside_red = _px(w, 20, 80)
    assert outside_red.red() < 255 and outside_red.red() > 0


def test_paint_selection_border(qtbot):
    w = _window(qtbot, _split_pix())
    qtbot.mousePress(w, LEFT, pos=QPoint(120, 10))
    qtbot.mouseMove(w, QPoint(160, 40))
    border = {_px(w, 119, 25).name(), _px(w, 120, 25).name()}
    assert "#3b82f6" in border


def test_paint_without_selection_is_all_dimmed(qtbot):
    w = _window(qtbot, _split_pix())
    c = _px(w, 140, 25)
    assert c.blue() < 255 and c.blue() > 0


def _dpr2_split_pix():
    """物理 400x200、dpr 2（逻辑 200x100），左半红右半蓝。"""
    p = QPixmap(400, 200)
    p.fill(QColor(255, 0, 0))
    q = QPainter(p)
    q.fillRect(QRect(200, 0, 200, 200), QColor(0, 0, 255))
    q.end()
    p.setDevicePixelRatio(2.0)
    return p


def test_paint_selection_uses_pixmap_dpr(qtbot):
    w = _window(qtbot, _dpr2_split_pix())
    qtbot.mousePress(w, LEFT, pos=QPoint(120, 10))
    qtbot.mouseMove(w, QPoint(160, 40))
    # 逻辑 (140,25) 在右半（蓝）；若源矩形没按 dpr 换算会取到物理 x=140 的红色
    assert _px(w, 140, 25) == QColor(0, 0, 255)


# ---------- crop_to_image ----------

def test_crop_offset_and_mode(qapp):
    img = crop_to_image(_split_pix(), QRect(110, 10, 20, 20))
    assert img.mode == "RGB"
    assert img.size == (20, 20)
    assert img.getpixel((0, 0)) == (0, 0, 255)


def test_crop_dpr_one_size(qapp):
    assert crop_to_image(_pix(), QRect(10, 10, 50, 30)).size == (50, 30)


def test_crop_dpr_two_offset(qapp):
    p = QPixmap(400, 200)
    p.fill(QColor(255, 0, 0))
    q = QPainter(p)
    q.fillRect(QRect(200, 0, 200, 200), QColor(0, 0, 255))  # 物理像素 x>=200 为蓝
    q.end()
    p.setDevicePixelRatio(2.0)
    # 逻辑 x=100 对应物理 x=200
    assert crop_to_image(p, QRect(100, 10, 20, 20)).getpixel((0, 0)) == (0, 0, 255)
    assert crop_to_image(p, QRect(90, 10, 5, 5)).getpixel((0, 0)) == (255, 0, 0)


def test_crop_fractional_dpr_rounds_edges(qapp):
    p = QPixmap(300, 150)  # dpr 1.5 → 逻辑 200x100
    p.fill(QColor(255, 0, 0))
    p.setDevicePixelRatio(1.5)
    # 逻辑 (1,1,1,1)：左/上 round(1.5)=2，右/下 round(3.0)=3，宽高应为 1；
    # 若宽高各自取整则 round(1.5)=2
    assert crop_to_image(p, QRect(1, 1, 1, 1)).size == (1, 1)
    # 逻辑 (3,3,3,3)：左 round(4.5)=4，右 round(9.0)=9，宽高应为 5（各自取整会是 round(4.5)=4）
    assert crop_to_image(p, QRect(3, 3, 3, 3)).size == (5, 5)


# ---------- Capturer ----------

class FakeScreen:
    def __init__(self, geom, color, events=None, fail=False):
        self._g = geom
        self._c = color
        self._events = events
        self._fail = fail

    def geometry(self):
        return self._g

    def grabWindow(self, _):
        if self._fail:
            raise RuntimeError("grab 失败")
        if self._events is not None:
            self._events.append("grab")
        return _pix(self._g.width(), self._g.height(), self._c)


def _use_screens(monkeypatch, screens, set_log=None):
    """换上假屏幕；假屏幕不是 QScreen，setScreen 也换成记录用的桩。"""
    monkeypatch.setattr(capture.QGuiApplication, "screens", staticmethod(lambda: screens))
    monkeypatch.setattr(SelectionWindow, "setScreen",
                        lambda self, s: set_log.append(s) if set_log is not None else None)


def _cap(**kw):
    calls = []
    cap = Capturer(**{k + "_grab": (lambda n=k: calls.append(n)) for k in kw})
    return cap, calls


def _stub_timer(monkeypatch):
    timers = []
    monkeypatch.setattr(capture.QTimer, "singleShot", staticmethod(lambda ms, fn: timers.append((ms, fn))))
    return timers


def test_start_busy_guard(qapp, monkeypatch):
    timers = _stub_timer(monkeypatch)
    cap, calls = _cap(before=1)
    cap.start()
    cap.start()
    assert calls == ["before"]
    assert len(timers) == 1
    assert timers[0][0] == 80


def test_start_without_before_callback(qapp, monkeypatch):
    timers = _stub_timer(monkeypatch)
    cap = Capturer()
    cap.start()
    assert len(timers) == 1


def test_finish_clears_busy_so_start_works_again(qapp, monkeypatch):
    timers = _stub_timer(monkeypatch)
    cap, calls = _cap(before=1)
    cap.start()
    cap._finish(None, None)
    cap.start()
    assert calls == ["before", "before"]
    assert len(timers) == 2


def _finish_setup(qtbot):
    w1 = SelectionWindow(_split_pix(), QRect(100, 200, 200, 100))
    w2 = SelectionWindow(_pix(), QRect(-300, 50, 200, 100))
    for w in (w1, w2):
        w.show()
    return w1, w2


def test_finish_emits_translated_rect_and_crops(qtbot):
    cap = Capturer()
    w1, w2 = _finish_setup(qtbot)
    cap._windows = [w1, w2]
    got = []
    cap.captured.connect(lambda img, r: got.append((img, r)))
    cap._finish(w1, QRect(110, 10, 50, 30))
    assert len(got) == 1
    img, rect = got[0]
    assert rect == QRect(210, 210, 50, 30)
    assert img.size == (50, 30)
    assert img.getpixel((0, 0)) == (0, 0, 255)


def test_finish_closes_and_deletes_windows(qtbot):
    cap = Capturer()
    w1, w2 = _finish_setup(qtbot)
    cap._windows = [w1, w2]
    cap._finish(w1, QRect(10, 10, 50, 30))
    assert cap._windows == []
    assert not w1.isVisible() and not w2.isVisible()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(w1)
    assert not shiboken6.isValid(w2)


def test_finish_cancel_emits_nothing_but_cleans_up(qtbot):
    cap, calls = _cap(after=1)
    w1, w2 = _finish_setup(qtbot)
    cap._windows = [w1, w2]
    cap._busy = True
    got = []
    cap.captured.connect(lambda *a: got.append(a))
    cap._finish(None, None)
    assert got == []
    assert calls == ["after"]
    assert cap._busy is False
    assert cap._windows == []


def test_finish_cancel_emits_cancelled_after_after_callback(qtbot):
    order = []
    cap = Capturer(after_grab=lambda: order.append("after"))
    cap.cancelled.connect(lambda: order.append("cancelled"))
    w1, w2 = _finish_setup(qtbot)
    cap._windows = [w1, w2]
    cap._finish(None, None)
    assert order == ["after", "cancelled"]


def test_finish_success_does_not_emit_cancelled(qtbot):
    cap = Capturer()
    got = []
    cap.cancelled.connect(lambda: got.append(1))
    w1, w2 = _finish_setup(qtbot)
    cap._windows = [w1, w2]
    cap._finish(w1, QRect(10, 10, 50, 30))
    assert got == []


def test_finish_window_without_rect_emits_nothing(qtbot):
    cap = Capturer()
    w1, w2 = _finish_setup(qtbot)
    cap._windows = [w1, w2]
    got = []
    cap.captured.connect(lambda *a: got.append(a))
    cap._finish(w1, None)
    assert got == []


def test_finish_rect_without_window_emits_nothing(qtbot):
    cap = Capturer()
    got = []
    cap.captured.connect(lambda *a: got.append(a))
    cap._finish(None, QRect(1, 1, 10, 10))
    assert got == []


def test_finish_without_after_callback_ok(qtbot):
    cap = Capturer()  # after_grab=None
    w1, w2 = _finish_setup(qtbot)
    cap._windows = [w1, w2]
    cap._finish(w1, QRect(10, 10, 50, 30))  # 不抛异常即可


def test_after_runs_before_captured_emitted(qtbot):
    order = []
    cap = Capturer(after_grab=lambda: order.append("after"))
    cap.captured.connect(lambda *a: order.append("captured"))
    w1, w2 = _finish_setup(qtbot)
    cap._windows = [w1, w2]
    cap._finish(w1, QRect(10, 10, 50, 30))
    assert order == ["after", "captured"]


def test_grab_with_fake_screens_end_to_end(qtbot, monkeypatch):
    """用两块假屏幕走完整条 _grab → 框选 → captured 链路（副屏在主屏左边，坐标为负）。"""
    screens = [FakeScreen(QRect(0, 0, 200, 100), "red"),
               FakeScreen(QRect(-300, 50, 200, 100), "blue")]
    set_log = []
    _use_screens(monkeypatch, screens, set_log)
    cap = Capturer()
    cap.start()
    qtbot.waitUntil(lambda: len(cap._windows) == 2, timeout=2000)
    w_main, w_second = cap._windows
    assert set_log == screens
    assert w_main.geometry() == QRect(0, 0, 200, 100)
    assert w_second.geometry() == QRect(-300, 50, 200, 100)
    with qtbot.waitSignal(cap.captured) as b:
        qtbot.mousePress(w_main, LEFT, pos=QPoint(10, 10))
        qtbot.mouseRelease(w_main, LEFT, pos=QPoint(60, 40))
    img, rect = b.args
    assert rect == QRect(10, 10, 50, 30)
    assert img.getpixel((0, 0)) == (255, 0, 0)
    assert cap._windows == [] and cap._busy is False

    cap.start()
    qtbot.waitUntil(lambda: len(cap._windows) == 2, timeout=2000)
    w_main, w_second = cap._windows
    with qtbot.waitSignal(cap.captured) as b:
        qtbot.mousePress(w_second, LEFT, pos=QPoint(10, 10))
        qtbot.mouseRelease(w_second, LEFT, pos=QPoint(60, 40))
    img, rect = b.args
    assert rect == QRect(-290, 60, 50, 30)
    assert img.getpixel((0, 0)) == (0, 0, 255)


def test_escape_on_one_window_closes_all(qtbot, monkeypatch):
    screens = [FakeScreen(QRect(0, 0, 200, 100), "red"),
               FakeScreen(QRect(-300, 50, 200, 100), "blue")]
    _use_screens(monkeypatch, screens)
    after = []
    cap = Capturer(after_grab=lambda: after.append(1))
    got = []
    cap.captured.connect(lambda *a: got.append(a))
    cap.start()
    qtbot.waitUntil(lambda: len(cap._windows) == 2, timeout=2000)
    ws = list(cap._windows)
    qtbot.keyClick(ws[1], Qt.Key.Key_Escape)
    assert cap._windows == []
    assert not any(w.isVisible() for w in ws)
    assert after == [1] and got == [] and cap._busy is False


def test_grab_all_screens_before_showing_any_window(qtbot, monkeypatch):
    events = []
    screens = [FakeScreen(QRect(0, 0, 200, 100), "red", events),
               FakeScreen(QRect(-300, 50, 200, 100), "blue", events)]
    _use_screens(monkeypatch, screens)
    monkeypatch.setattr(SelectionWindow, "show", lambda self: events.append("show"))
    cap = Capturer()
    cap.start()
    qtbot.waitUntil(lambda: len(cap._windows) == 2, timeout=2000)
    assert events == ["grab", "grab", "show", "show"]


def test_grab_with_no_screens_recovers(qapp, monkeypatch):
    timers = _stub_timer(monkeypatch)
    _use_screens(monkeypatch, [])
    cap, calls = _cap(before=1, after=1)
    cap.start()
    timers[0][1]()
    assert cap._busy is False
    assert cap._windows == []
    assert calls == ["before", "after"]


def test_grab_exception_recovers(qapp, monkeypatch):
    timers = _stub_timer(monkeypatch)
    _use_screens(monkeypatch, [FakeScreen(QRect(0, 0, 200, 100), "red", fail=True)])
    cap, calls = _cap(before=1, after=1)
    got = []
    cap.captured.connect(lambda *a: got.append(a))
    cap.start()
    timers[0][1]()
    assert cap._busy is False
    assert cap._windows == []
    assert calls == ["before", "after"]
    assert got == []
    cap.start()  # 热键可再次使用
    assert len(timers) == 2

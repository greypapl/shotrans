"""浮窗右键：点在截图里面的空白、跳过的段（公式）上 = 立即关窗（钉住也关）；点在段上照旧弹菜单；
截图外面（工具栏、截图右侧多出的窗口宽度）不管；键盘菜单键不关窗。"""
import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QContextMenuEvent

from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.overlay import OverlayWindow
from shotrans.session import Session


class Router:
    def vllm_down(self):
        return False

    def vllm_last_error_kind(self):
        return None

    def translate(self, segments, direction, on_result, cancel=None):
        for s in segments:
            if not s.skip:
                on_result(SegmentResult(s.idx, f"译{s.idx}", "vllm"))


LINES = [OcrLine("Hello world", Box(10, 10, 150, 20)), OcrLine("E = mc^2", Box(10, 60, 150, 20))]
BAR = 28  # 工具栏在截图上方，截图在窗口里的 y 偏移
ON_SEG = QPoint(20, BAR + 15)
ON_SKIP = QPoint(20, BAR + 65)
BLANK = QPoint(250, BAR + 100)
MARGIN = QPoint(400, BAR + 50)  # 窗口最少 460 宽，截图只有 300：右边多出来的部分不属于截图
TOOLBAR = QPoint(250, 10)  # 截图上方的工具栏


@pytest.fixture
def menu(monkeypatch):
    state = {"calls": 0}

    class FakeMenu:
        def __init__(self, parent=None):
            pass

        def addAction(self, text):
            class A:
                def setEnabled(self, v):
                    pass
            return A()

        def addSeparator(self):
            pass

        def exec(self, pos):
            state["calls"] += 1
            return None

    monkeypatch.setattr("shotrans.overlay.QMenu", FakeMenu)
    return state


@pytest.fixture
def make(qtbot, track):
    def _make():
        s = Session(Image.new("RGB", (300, 120), "white"))
        w = OverlayWindow(s, QRect(100, 200, 300, 120), lambda img: list(LINES), Router())
        track(w)
        w.show()
        qtbot.waitUntil(lambda: len(s.segments) == 2 and len(s.results) == 1, timeout=3000)
        assert s.segments[1].skip and not s.segments[0].skip
        return w
    return _make


def _ctx(w, pos, reason=QContextMenuEvent.Reason.Mouse):
    w.contextMenuEvent(QContextMenuEvent(reason, pos, w.mapToGlobal(pos)))


@pytest.mark.parametrize("pos", [BLANK, ON_SKIP], ids=["blank", "skip"])
def test_right_click_off_segment_closes(qtbot, make, menu, pos):
    w = make()
    _ctx(w, pos)
    assert not w.isVisible() and menu["calls"] == 0


@pytest.mark.parametrize("pos", [TOOLBAR, MARGIN], ids=["toolbar", "outside-image"])
def test_right_click_outside_image_does_nothing(qtbot, make, menu, pos):
    w = make()
    w._img_rect.contains(pos) and pytest.fail("夹具位置应在截图外")
    _ctx(w, pos)
    assert w.isVisible() and menu["calls"] == 0


def test_right_click_blank_closes_even_when_pinned(qtbot, make, menu):
    w = make()
    w._on_pin(True)
    _ctx(w, BLANK)
    assert not w.isVisible()


def test_right_click_on_segment_shows_menu_and_stays(qtbot, make, menu):
    w = make()
    _ctx(w, ON_SEG)
    assert menu["calls"] == 1 and w.isVisible()


@pytest.mark.parametrize("pos", [BLANK, ON_SKIP], ids=["blank", "skip"])
def test_keyboard_menu_key_off_segment_does_not_close(qtbot, make, menu, pos):
    w = make()
    _ctx(w, pos, QContextMenuEvent.Reason.Keyboard)
    assert w.isVisible() and menu["calls"] == 0


def test_keyboard_menu_key_on_segment_still_shows_menu(qtbot, make, menu):
    w = make()
    _ctx(w, ON_SEG, QContextMenuEvent.Reason.Keyboard)
    assert menu["calls"] == 1 and w.isVisible()


# ---------- 选区几乎满屏高：工具栏叠在截图顶上（审查 I1） ----------
@pytest.fixture
def make_full(qtbot, track):
    from PySide6.QtGui import QGuiApplication

    def _make():
        g = QGuiApplication.primaryScreen().geometry()
        s = Session(Image.new("RGB", (300, g.height()), "white"))
        w = OverlayWindow(s, QRect(g.left() + 50, g.top(), 300, g.height()),
                          lambda img: [OcrLine("Hello world", Box(10, 300, 150, 20))], Router())
        track(w)
        w.show()
        w._bar.show()  # 鼠标进入时才显示；右键时鼠标一定在窗口里
        qtbot.waitUntil(lambda: len(s.results) == 1, timeout=3000)
        assert w._bar.geometry().top() == w._img_rect.top() == 0  # 确实叠在截图上
        return w
    return _make


def _send_to_child(child, local=QPoint(3, 3)):
    from PySide6.QtWidgets import QApplication
    ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, local, child.mapToGlobal(local))
    QApplication.sendEvent(child, ev)  # 子控件不接，冒泡到浮窗


def test_overlapping_toolbar_button_right_click_does_not_close(qtbot, make_full, menu):
    from PySide6.QtWidgets import QToolButton
    w = make_full()
    w._on_pin(True)
    btn = w._bar.findChildren(QToolButton)[0]
    assert w._img_rect.contains(btn.mapTo(w, QPoint(3, 3)))
    _send_to_child(btn)
    qtbot.wait(50)
    assert w.isVisible() and menu["calls"] == 0


def test_overlapping_status_label_right_click_does_not_close(qtbot, make_full, menu):
    w = make_full()
    _send_to_child(w._status)
    qtbot.wait(50)
    assert w.isVisible() and menu["calls"] == 0


def test_overlapping_layout_blank_below_toolbar_still_closes(qtbot, make_full, menu):
    w = make_full()
    _ctx(w, QPoint(200, 100))
    assert not w.isVisible()
import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication

from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.overlay import OverlayWindow
from shotrans.session import Session


class FakeRouter:
    def __init__(self):
        self.calls = []
        self.retranslate_calls = []

    def vllm_down(self):
        return False

    def translate(self, segments, direction, on_result, cancel=None):
        self.calls.append(([s.idx for s in segments], direction))
        for s in segments:
            if not s.skip:
                on_result(SegmentResult(s.idx, f"译{s.idx}", "vllm"))

    def retranslate_one(self, seg, direction, current):
        self.retranslate_calls.append((seg.idx, direction, current))
        return SegmentResult(seg.idx, "新译", "fallback")


class FakeHistory:
    def __init__(self):
        self.entries = []

    def add(self, e):
        self.entries.append(e)


def fake_ocr(image):
    return [OcrLine("Hello world", Box(10, 10, 150, 20)), OcrLine("Second para", Box(10, 60, 150, 20))]


@pytest.fixture
def overlay(qtbot, track):
    s = Session(Image.new("RGB", (300, 120), "white"))
    w = OverlayWindow(s, QRect(100, 200, 300, 120), fake_ocr, FakeRouter(), FakeHistory())
    track(w)
    w.show()
    qtbot.waitUntil(lambda: len(s.results) == 2, timeout=5000)
    return w


def test_results_arrive(overlay):
    assert overlay.session.service_status() == "vllm"
    assert overlay._router.calls == [([1, 2], "en2zh")]


def test_history_written_when_done(qtbot, overlay):
    qtbot.waitUntil(lambda: len(overlay._history.entries) == 1, timeout=3000)
    assert overlay._history.entries[0]["service"] == "vllm"


def test_image_placed_exactly_at_selection(overlay):
    assert overlay.mapToGlobal(overlay._img_rect.topLeft()) == QPoint(100, 200)
    assert overlay._img_rect.size() == QRect(0, 0, 300, 120).size()


def test_space_toggles_view(qtbot, overlay):
    qtbot.keyClick(overlay, Qt.Key.Key_Space)
    assert overlay.session.show_original is True
    qtbot.keyClick(overlay, Qt.Key.Key_Space)
    assert overlay.session.show_original is False


def test_ctrl_c_copies_translation(qtbot, overlay):
    qtbot.keyClick(overlay, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QGuiApplication.clipboard().text() == "译1\n\n译2"


def test_ctrl_shift_c_copies_original(qtbot, overlay):
    qtbot.keyClick(overlay, Qt.Key.Key_C,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert QGuiApplication.clipboard().text() == "Hello world\n\nSecond para"


def test_d_flips_direction_and_retranslates(qtbot, overlay):
    qtbot.keyClick(overlay, Qt.Key.Key_D)
    qtbot.waitUntil(lambda: len(overlay._router.calls) == 2, timeout=3000)
    assert overlay._router.calls[1][1] == "zh2en"


def test_mode_toggle_regroups_and_retranslates(qtbot, overlay):
    overlay.toggle_mode()
    assert overlay.session.mode == "line"
    qtbot.waitUntil(lambda: len(overlay._router.calls) == 2, timeout=3000)


def test_double_click_copies_segment(qtbot, overlay):
    pos = overlay._img_rect.topLeft() + QPoint(20, 15)
    qtbot.mouseDClick(overlay, Qt.MouseButton.LeftButton, pos=pos)
    assert QGuiApplication.clipboard().text() == "译1"


def test_retranslate_applies_and_undo(qtbot, overlay):
    seg = overlay.session.segments[0]
    overlay._retranslate(seg)
    qtbot.waitUntil(lambda: overlay.session.results[1].text == "新译", timeout=3000)
    assert overlay._router.retranslate_calls == [(1, "en2zh", "vllm")]
    assert overlay.session.undo_retranslation(1)
    assert overlay.session.results[1].text == "译1"


def test_escape_closes(qtbot, overlay):
    with qtbot.waitSignal(overlay.closed):
        qtbot.keyClick(overlay, Qt.Key.Key_Escape)


def test_pinned_survives_deactivation(qtbot, overlay, monkeypatch):
    monkeypatch.setattr(overlay, "isActiveWindow", lambda: False)
    overlay.pinned = True
    overlay._close_if_inactive()
    assert overlay.isVisible()


def test_unpinned_closes_when_inactive(qtbot, overlay, monkeypatch):
    monkeypatch.setattr(overlay, "isActiveWindow", lambda: False)
    with qtbot.waitSignal(overlay.closed):
        overlay._close_if_inactive()


def test_ocr_failure_shown_in_status(qtbot, track):
    def bad_ocr(image):
        raise RuntimeError("坏了")

    s = Session(Image.new("RGB", (300, 120), "white"))
    w = OverlayWindow(s, QRect(100, 200, 300, 120), bad_ocr, FakeRouter(), None)
    track(w)
    w.show()
    qtbot.waitUntil(lambda: "识别失败" in w._status.text(), timeout=3000)

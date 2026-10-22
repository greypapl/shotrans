"""「引用/术语可能未保持原样」：text 是译文，照常画，同时画琥珀角标、悬停附原因。
别的 note（占位符丢失、服务不可用）仍然显示原文（见 test_session.py）。"""
import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QPoint

from shotrans.models import Box, OcrLine, Segment, SegmentResult
from shotrans.render import render
from shotrans.session import Session
from tests.test_overlay_guards import Router, make  # noqa: F401  (make 是夹具)

UNPROT = "引用/术语可能未保持原样"
AMBER = (245, 158, 11)
WHITE = (255, 255, 255)


def _session(lines=None):
    s = Session(Image.new("RGB", (300, 120), "white"))
    s.set_lines(lines or [OcrLine("Hello world", Box(10, 10, 150, 20)), OcrLine("Second para", Box(10, 60, 150, 20))])
    return s


def _box_changed_outside_marker(img, src):
    out = np.asarray(img).copy()
    out[10:16, 154:160] = np.asarray(src)[10:16, 154:160]  # 段 1 框 (10,10,150,20) 的右上角 6x6 是角标
    return (out[10:30, 10:160] != np.asarray(src)[10:30, 10:160]).any()


# ---------- SegmentResult.is_translation ----------
@pytest.mark.parametrize("service,note,expected", [
    ("tencent", None, True),
    ("tencent", UNPROT, True),
    ("vllm", UNPROT, True),
    ("tencent", "占位符丢失，显示原文", False),
    ("none", "翻译服务不可用", False),
    ("none", None, False),
    ("none", UNPROT, False),
])
def test_is_translation(service, note, expected):
    assert SegmentResult(1, "x", service, note).is_translation is expected


# ---------- Session ----------
def test_unprotected_result_is_drawn():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "你好世界", "tencent", UNPROT))
    assert _box_changed_outside_marker(s.display_image(), s.image)


def test_unprotected_result_gets_marker():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "你好世界", "tencent", UNPROT))
    img = s.display_image()
    assert img.getpixel((159, 10)) == AMBER
    assert img.getpixel((154, 10)) == AMBER


def test_lost_note_still_not_drawn():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "Hello world", "tencent", "占位符丢失，显示原文"))
    assert not _box_changed_outside_marker(s.display_image(), s.image)


def test_unprotected_hover_in_translation_view():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "你好世界", "tencent", UNPROT))
    assert s.hover_text(s.segments[0]) == f"Hello world\n（{UNPROT}）"


def test_unprotected_hover_in_original_view():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "你好世界", "tencent", UNPROT))
    s.toggle_view()
    assert s.hover_text(s.segments[0]) == f"你好世界\n（{UNPROT}）"


def test_unprotected_hover_when_truncated_keeps_note():
    s = _session([OcrLine("Hi", Box(10, 10, 30, 12))])
    long = "很长的译文" * 100   # Task 19：译文会延伸到框外的空白，原来的 20 遍已能放下，加到 100 遍才截断
    s.apply(s.generation, SegmentResult(1, long, "tencent", UNPROT))
    assert s.hover_text(s.segments[0]) == f"{long}\n——\nHi\n（{UNPROT}）"


def test_unprotected_copy_gives_translation():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "你好世界", "tencent", UNPROT))
    assert s.translation_of(s.segments[0]) == "你好世界"


# ---------- render：同一段既画译文又画角标 ----------
def test_render_translation_and_marker_on_same_segment():
    img = Image.new("RGB", (200, 100), WHITE)
    seg = Segment(1, "x", [OcrLine("x", Box(20, 30, 100, 20))])
    r = render(img, [seg], {1: "你好"}, "zh", marked={1})
    assert 1 in r.placements  # 译文画了
    assert r.image.getpixel((119, 30)) == AMBER  # 角标画在译文之上


# ---------- 浮窗 ----------
@pytest.fixture
def quiet_u(make):  # noqa: F811
    return make(router=Router(auto=False))


@pytest.fixture
def tip(monkeypatch):
    shown = []

    class _Tip:
        @staticmethod
        def showText(pos, text, widget=None):
            shown.append(text)

        @staticmethod
        def hideText():
            pass

    monkeypatch.setattr("shotrans.overlay.QToolTip", _Tip)
    return shown


def test_overlay_draws_unprotected_translation_with_marker(quiet_u):
    w = quiet_u
    w.session.apply(w.session.generation, SegmentResult(1, "你好世界", "tencent", UNPROT))
    w._refresh()
    qimg = w._pixmap.toImage()
    c = qimg.pixelColor(159, 10)
    assert (c.red(), c.green(), c.blue()) == AMBER
    dark = [qimg.pixelColor(x, y).lightness() < 128 for x in range(10, 150) for y in range(10, 30)]
    assert any(dark)  # 框里画了字（白底上原先一个深色像素都没有）


def test_overlay_hover_shows_unprotected_note(quiet_u, tip):
    w = quiet_u
    w.session.apply(w.session.generation, SegmentResult(1, "你好世界", "tencent", UNPROT))
    w._refresh()
    w._hover_pos = QPoint(20, 28 + 15)
    w._show_hover()
    assert tip == [f"Hello world\n（{UNPROT}）"]

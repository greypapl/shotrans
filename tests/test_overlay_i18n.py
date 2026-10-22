"""浮窗：切换语言后按钮与状态栏变英文；只装一个 OCR 语言包时状态栏注明；附注按语言显示。"""
import pytest
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QToolButton

from shotrans import i18n
from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.ocr import OcrLines
from shotrans.overlay import OverlayWindow
from shotrans.session import Session
from shotrans.translate.router import NOTE_DOWN


class Router:
    def vllm_down(self):
        return False

    def vllm_last_error_kind(self):
        return None

    def translate(self, segments, direction, on_result, cancel=None):
        for s in segments:
            if not s.skip:
                on_result(SegmentResult(s.idx, f"译{s.idx}", "vllm"))


LINES = [OcrLine("Hello world", Box(10, 10, 150, 20))]


@pytest.fixture
def make(qtbot, track):
    def _make(ocr):
        s = Session(Image.new("RGB", (300, 120), "white"))
        w = OverlayWindow(s, QRect(100, 200, 300, 120), ocr, Router(), None)
        track(w)
        w.show()
        qtbot.waitUntil(lambda: len(s.results) == 1, timeout=3000)
        return w
    return _make


def _buttons(w):
    return [b.text() for b in w._bar.findChildren(QToolButton)]


def test_switch_language_updates_bar_and_status(make):
    w = make(lambda img: list(LINES))
    assert "主服务 · 译文" in w._status.text() and "复制译文" in _buttons(w)
    i18n.set_language("en")
    assert "Primary · Translation" in w._status.text()
    assert "Copy translation" in _buttons(w) and "复制译文" not in _buttons(w)


def test_partial_ocr_noted_in_status(make):
    w = make(lambda img: OcrLines(LINES, ("zh-Hans-CN",)))
    assert "（未装简体中文 OCR 语言包）" in w._status.text()


def test_full_ocr_has_no_partial_note(make):
    w = make(lambda img: OcrLines(LINES, ()))
    assert "OCR 语言包" not in w._status.text()


def test_hover_note_follows_language():
    s = Session(Image.new("RGB", (300, 120), "white"))
    s.set_lines(LINES)
    s.apply(s.generation, SegmentResult(1, "Hello world", "none", NOTE_DOWN))
    assert s.hover_text(s.segments[0]) == "Hello world\n（翻译服务不可用）"
    i18n.set_language("en")
    assert s.hover_text(s.segments[0]) == "Hello world\n(Translation unavailable)"


def _boom(img):
    raise RuntimeError("坏了")


def test_ocr_error_prefixed_once_in_current_language(qtbot, track):
    s = Session(Image.new("RGB", (300, 120), "white"))
    w = OverlayWindow(s, QRect(100, 200, 300, 120), _boom, Router(), None)
    track(w)
    w.show()
    qtbot.waitUntil(lambda: "坏了" in w._status.text(), timeout=3000)
    assert "识别失败：坏了" in w._status.text() and "识别失败：识别失败" not in w._status.text()
    i18n.set_language("en")
    assert "OCR failed: 坏了" in w._status.text()


class BoomRouter(Router):
    def translate(self, segments, direction, on_result, cancel=None):
        raise RuntimeError("炸了")


def test_translate_error_prefixed_once_in_current_language(qtbot, track):
    s = Session(Image.new("RGB", (300, 120), "white"))
    w = OverlayWindow(s, QRect(100, 200, 300, 120), lambda img: list(LINES), BoomRouter(), None)
    track(w)
    w.show()
    qtbot.waitUntil(lambda: "炸了" in w._status.text(), timeout=3000)
    assert "翻译出错：炸了" in w._status.text() and w._status.text().count("翻译出错") == 1
    i18n.set_language("en")
    assert "Translation error: 炸了" in w._status.text() and w._status.text().count("Translation error") == 1

"""浮窗状态栏：服务不可用 / key 无效旁有「设置」链接，识别失败旁有「自检」链接；没有回调时不显示链接。"""
import pytest
from PIL import Image
from PySide6.QtCore import QRect

from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.overlay import OverlayWindow
from shotrans.session import Session


class Router:
    def __init__(self):
        self.down, self.last_kind = False, None

    def vllm_down(self):
        return self.down

    def vllm_last_error_kind(self):
        return self.last_kind if self.down else None

    def translate(self, segments, direction, on_result, cancel=None):
        pass


LINES = [OcrLine("Hello world", Box(10, 10, 150, 20))]


@pytest.fixture
def make(qtbot, track):
    opened = []

    def _make(ocr=lambda img: list(LINES), with_cb=True):
        s = Session(Image.new("RGB", (300, 120), "white"))
        w = OverlayWindow(s, QRect(100, 200, 300, 120), ocr, Router(), None,
                          open_window=(lambda kind, page=None: opened.append((kind, page))) if with_cb else None)
        track(w)
        w.show()
        qtbot.waitUntil(lambda: bool(s.segments) or "识别失败" in w._status.text(), timeout=3000)
        return w
    _make.opened = opened
    return _make


def test_unavailable_has_settings_link(make):
    w = make()
    w.session.apply(w.session.generation, SegmentResult(1, "Hello world", "none", "翻译服务不可用"))
    w._refresh()
    assert '<a href="settings">设置</a>' in w._status.text()
    w._status.linkActivated.emit("settings")
    assert make.opened == [("settings", "service")]


def test_key_invalid_has_settings_link(make):
    w = make()
    w._router.down, w._router.last_kind = True, "auth"
    w.session.apply(w.session.generation, SegmentResult(1, "好", "tencent"))
    w._refresh()
    assert '<a href="settings">' in w._status.text()


def test_normal_status_has_no_link(make):
    w = make()
    w.session.apply(w.session.generation, SegmentResult(1, "好", "vllm"))
    w._refresh()
    assert "<a " not in w._status.text()


def test_ocr_failure_has_doctor_link(make):
    def bad(img):
        raise RuntimeError("没有安装 OCR 语言包")

    w = make(ocr=bad)
    assert '<a href="doctor">自检</a>' in w._status.text()
    w._status.linkActivated.emit("doctor")
    assert make.opened == [("doctor", None)]


def test_no_callback_no_links(make):
    w = make(with_cb=False)
    w.session.apply(w.session.generation, SegmentResult(1, "Hello world", "none", "翻译服务不可用"))
    w._refresh()
    assert "<a " not in w._status.text()
    w._status.linkActivated.emit("settings")  # 不报错

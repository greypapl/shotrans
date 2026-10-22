from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QRect

from shotrans import autostart
from shotrans.app import ShotTransApp, ensure_glossary
from shotrans.models import Box, OcrLine

CONFIG = """
[vllm]
base_url = "http://127.0.0.1:9/v1"
api_key = "k"
[hotkey]
capture = "Ctrl+Alt+Shift+F10"
"""


def test_ensure_glossary_creates_template(tmp_path):
    p = tmp_path / "术语表.txt"
    ensure_glossary(p)
    text = p.read_text(encoding="utf-8")
    assert "=>" in text and text.lstrip().startswith("#")


def test_ensure_glossary_keeps_existing(tmp_path):
    p = tmp_path / "术语表.txt"
    p.write_text("PHQ-8\n", encoding="utf-8")
    ensure_glossary(p)
    assert p.read_text(encoding="utf-8") == "PHQ-8\n"


class QuietRouter:
    """不联网的路由替身：测试里绝不能真的去调 vLLM 或备用接口。"""

    def __init__(self):
        self.glossary = None

    def vllm_down(self):
        return False

    def translate(self, segments, direction, on_result, cancel=None):
        pass

    def retranslate_one(self, seg, direction, current):
        return None


@pytest.fixture
def app(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    (tmp_path / "config.toml").write_text(CONFIG, encoding="utf-8")
    a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [OcrLine("Hi", Box(1, 1, 20, 10))],
                     router=QuietRouter())
    yield a
    a.shutdown()


def test_captured_creates_overlay(qtbot, app):
    app.on_captured(Image.new("RGB", (100, 50), "white"), QRect(300, 300, 100, 50))
    assert len(app.overlays) == 1


def test_new_capture_replaces_unpinned(qtbot, app):
    app.on_captured(Image.new("RGB", (100, 50), "white"), QRect(300, 300, 100, 50))
    first = app.overlays[0]
    app.on_captured(Image.new("RGB", (100, 50), "white"), QRect(320, 320, 100, 50))
    assert len(app.overlays) == 1 and app.overlays[0] is not first


def test_pinned_overlays_stay(qtbot, app):
    app.on_captured(Image.new("RGB", (100, 50), "white"), QRect(300, 300, 100, 50))
    app.overlays[0].pinned = True
    app.on_captured(Image.new("RGB", (100, 50), "white"), QRect(320, 320, 100, 50))
    assert len(app.overlays) == 2


def test_hide_and_show_pinned(qtbot, app):
    app.on_captured(Image.new("RGB", (100, 50), "white"), QRect(300, 300, 100, 50))
    w = app.overlays[0]
    w.pinned = True
    app._hide_overlays()
    assert not w.isVisible()
    app._show_pinned()
    assert w.isVisible()


def test_glossary_reloaded_on_capture(qtbot, app, tmp_path):
    (tmp_path / "术语表.txt").write_text("PHQ-8\n", encoding="utf-8")
    app.on_captured(Image.new("RGB", (100, 50), "white"), QRect(300, 300, 100, 50))
    assert app.router.glossary.keep == ("PHQ-8",)

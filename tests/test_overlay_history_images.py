"""浮窗翻译完成时：历史记录连同原图、译文图一起存（图在后台线程写），重翻同一张截图时替换旧的那对。"""
import threading

import pytest
from PIL import Image
from PySide6.QtCore import QRect

from shotrans import overlay
from shotrans.history import History
from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.overlay import OverlayWindow
from shotrans.session import Session


class Router:
    def __init__(self):
        self.calls = []

    def vllm_down(self):
        return False

    def vllm_last_error_kind(self):
        return None

    def translate(self, segments, direction, on_result, cancel=None):
        self.calls.append(direction)
        for s in segments:
            if not s.skip:
                on_result(SegmentResult(s.idx, f"译{s.idx}", "vllm"))


LINES = [OcrLine("Hello world", Box(10, 10, 150, 20))]


@pytest.fixture
def make(qtbot, track):
    def _make(history):
        s = Session(Image.new("RGB", (300, 120), (200, 10, 10)))
        w = OverlayWindow(s, QRect(100, 200, 300, 120), lambda img: list(LINES), Router(), history)
        track(w)
        w.show()
        return w
    return _make


def _h(tmp_path, save=True):
    return History(tmp_path / "history.json", images_dir=tmp_path / "history_images", save_images=save)


def _bound(h):
    items = h.load()
    return len(items) == 1 and "images" in items[0]


def test_finished_saves_original_and_rendered_png(qtbot, make, tmp_path):
    h = _h(tmp_path)
    w = make(h)
    qtbot.waitUntil(lambda: _bound(h), timeout=5000)
    e = h.load()[0]
    with Image.open(h.image_path(e, "original")) as im:
        assert im.size == (300, 120) and im.getpixel((299, 119)) == (200, 10, 10)
    with Image.open(h.image_path(e, "rendered")) as im:
        rendered = im.convert("RGB")
    assert rendered.tobytes() == w.session._rendered().image.tobytes()
    assert rendered.tobytes() != w.session.image.tobytes()  # 译文确实盖上去了


def test_flip_replaces_old_pair(qtbot, make, tmp_path):
    h = _h(tmp_path)
    w = make(h)
    qtbot.waitUntil(lambda: _bound(h), timeout=5000)
    first = h.load()[0]["image_id"]
    w.flip_direction()
    qtbot.waitUntil(lambda: _bound(h) and h.load()[0]["image_id"] != first, timeout=5000)
    e = h.load()[0]
    assert e["direction"] == "zh2en"
    names = sorted(x.name for x in (tmp_path / "history_images").iterdir())
    assert names == [f"{e['image_id']}-original.png", f"{e['image_id']}-rendered.png"]


def test_save_images_off_writes_text_only(qtbot, make, tmp_path):
    h = _h(tmp_path, save=False)
    make(h)
    qtbot.waitUntil(lambda: len(h.load()) == 1, timeout=5000)
    qtbot.wait(200)
    assert "image_id" not in h.load()[0] and not (tmp_path / "history_images").exists()


def test_png_written_off_ui_thread(qtbot, make, tmp_path, monkeypatch):
    seen = []
    real = Image.Image.save

    def spy(self, *a, **k):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(self, *a, **k)

    monkeypatch.setattr(Image.Image, "save", spy)
    h = _h(tmp_path)
    make(h)
    qtbot.waitUntil(lambda: _bound(h), timeout=5000)
    assert seen == [False, False]


def test_image_write_failure_keeps_text_entry(qtbot, make, tmp_path, monkeypatch):
    def boom(self, *a, **k):
        raise OSError("磁盘满了")

    monkeypatch.setattr(Image.Image, "save", boom)
    h = _h(tmp_path)
    w = make(h)
    qtbot.waitUntil(lambda: len(h.load()) == 1, timeout=5000)
    qtbot.wait(300)
    e = h.load()[0]
    assert e["segments"] == [{"src": "Hello world", "dst": "译1"}] and "images" not in e
    assert w.isVisible()


def test_building_image_copies_failure_still_writes_text(qtbot, make, tmp_path, monkeypatch, caplog):
    h = _h(tmp_path)
    w = make(h)
    qtbot.waitUntil(lambda: _bound(h), timeout=5000)
    first = h.load()[0]["image_id"]
    monkeypatch.setattr(w.session, "_rendered", lambda: (_ for _ in ()).throw(RuntimeError("画坏了")))
    w._on_finished(w.session.generation)  # 同一代再完成一次：这次取图失败
    [e] = h.load()
    assert "image_id" not in e and e["segments"] == [{"src": "Hello world", "dst": "译1"}]
    assert first not in "".join(x.name for x in (tmp_path / "history_images").iterdir())  # 旧图随旧记录删掉
    assert "历史截图准备失败" in caplog.text


def test_fake_history_without_image_support_still_works(qtbot, make):
    class Plain:
        def __init__(self):
            self.entries = []

        def add(self, e):
            self.entries.append(e)

    h = Plain()
    make(h)
    qtbot.waitUntil(lambda: len(h.entries) == 1, timeout=5000)


def test_worker_gets_plain_values_only(qtbot, make, tmp_path, monkeypatch):
    """线程规则：图片写入线程拿到的是 PIL 副本和写入函数，不是窗口或 session。"""
    got = []
    monkeypatch.setattr(overlay, "_history_images_worker", lambda *a: got.append(a))
    h = _h(tmp_path)
    w = make(h)
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    write, iid, original, rendered = got[0]
    assert write == h.write_images and isinstance(iid, str)
    assert isinstance(original, Image.Image) and original is not w.session.image
    assert isinstance(rendered, Image.Image) and rendered is not w.session._rendered().image


def test_no_worker_when_history_declines_images(qtbot, make, tmp_path, monkeypatch):
    """wants_images 为真、add 却没给 image_id（比如刚好被关掉）：不起写图线程。"""
    got = []
    monkeypatch.setattr(overlay, "_history_images_worker", lambda *a: got.append(a))
    h = _h(tmp_path)
    real_add = h.add
    monkeypatch.setattr(h, "add", lambda e, with_images=False: real_add(e) and None)
    make(h)
    qtbot.waitUntil(lambda: len(h.load()) == 1, timeout=5000)
    qtbot.wait(200)
    assert got == []
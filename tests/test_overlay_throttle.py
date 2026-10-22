"""译文陆续到达时整图重画的节流：一次取队列只画一次；两次重画至少隔 120 毫秒；最后的状态一定画出来。"""
import time

import pytest
from PIL import Image
from PySide6.QtCore import QRect

from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.overlay import OverlayWindow
from shotrans.session import Session


class SilentRouter:
    def vllm_down(self):
        return False

    def translate(self, segments, direction, on_result, cancel=None):
        pass   # 结果由测试手动塞进收件箱


def _ocr(image):
    return [OcrLine("Hello world", Box(10, 10, 150, 20)), OcrLine("Second para", Box(10, 60, 150, 20))]


@pytest.fixture
def win(qtbot, track):
    s = Session(Image.new("RGB", (300, 120), "white"))
    w = OverlayWindow(s, QRect(100, 200, 300, 120), _ocr, SilentRouter(), None)
    track(w)
    w.show()
    qtbot.waitUntil(lambda: len(s.segments) == 2, timeout=5000)
    return w


def _spy(win, monkeypatch):
    """每次整图重画记下当时 session 里第 1 段的译文。"""
    seen = []
    real = win.session.display_image

    def spy():
        r = win.session.results.get(1)
        seen.append(r.text if r else None)
        return real()

    monkeypatch.setattr(win.session, "display_image", spy)
    return seen


def _put(win, n, tag):
    for i in range(n):
        win._inbox.put(("result", win.session.generation, SegmentResult(1 + i % 2, f"{tag}{i}", "vllm")))


def test_many_queued_results_render_once_per_drain(win, monkeypatch):
    seen = _spy(win, monkeypatch)
    win._last_render = float("-inf")
    _put(win, 40, "译")
    win._drain()
    assert seen == ["译38"]   # 恰好一次，且画的是全部到齐后的状态（第 1 段最后一个结果是 i=38）
    assert win.session.results[2].text == "译39" and win._dirty is False


def test_drain_without_results_does_not_render(win, monkeypatch):
    seen = _spy(win, monkeypatch)
    win._last_render = float("-inf")
    win._drain()
    assert seen == []


def test_burst_across_drains_is_rate_limited_and_last_state_rendered(qtbot, win, monkeypatch):
    seen = _spy(win, monkeypatch)
    win._last_render = float("-inf")
    for k in range(6):                       # 6 次取队列挤在 120 毫秒之内
        _put(win, 5, f"批{k}-")
        win._drain()
    assert len(seen) == 1 and win._dirty is True   # 只有第一批画了，其余攒着
    qtbot.waitUntil(lambda: len(seen) == 2, timeout=2000)
    assert seen[-1] == "批5-4"
    assert win.session.results[1].text == seen[-1] and win._dirty is False
    qtbot.wait(300)
    assert len(seen) == 2                    # 补画一次就停，不空转


def test_second_drain_inside_interval_waits_then_renders_once(qtbot, win, monkeypatch):
    seen = _spy(win, monkeypatch)
    win._refresh()
    seen.clear()
    _put(win, 1, "甲")
    win._drain()
    assert seen == []                        # 刚画过，不到 120 毫秒
    t0 = time.monotonic()
    qtbot.waitUntil(lambda: len(seen) == 1, timeout=2000)
    assert seen == ["甲0"] and time.monotonic() - t0 >= 0.05


def test_drain_after_interval_renders_immediately(win, monkeypatch):
    seen = _spy(win, monkeypatch)
    win._refresh()
    win._last_render -= 0.5                  # 假装上次画的是半秒前
    seen.clear()
    _put(win, 1, "乙")
    win._drain()
    assert seen == ["乙0"]


def test_slow_render_interval_counts_from_render_end(qtbot, win, monkeypatch):
    # 整图重画本身要 0.2 秒（> 120 毫秒）：间隔从画完算起，紧接着到的结果走定时器，不立即再画
    seen = []
    real = win.session.display_image

    def slow():
        time.sleep(0.2)
        r = win.session.results.get(1)
        seen.append(r.text if r else None)
        return real()

    monkeypatch.setattr(win.session, "display_image", slow)
    win._refresh()
    seen.clear()
    _put(win, 1, "慢")
    win._drain()
    assert seen == [] and win._render_timer.isActive()
    qtbot.waitUntil(lambda: len(seen) == 1, timeout=2000)
    assert seen == ["慢0"]


def test_close_stops_pending_render_timer(qtbot, win, monkeypatch):
    seen = _spy(win, monkeypatch)
    win._refresh()
    seen.clear()
    _put(win, 1, "丙")
    win._drain()
    assert win._render_timer.isActive()
    win.close()
    assert not win._render_timer.isActive()
    qtbot.wait(300)
    assert seen == []


def test_status_text_updates_immediately_even_when_render_is_deferred(win, monkeypatch):
    seen = _spy(win, monkeypatch)
    win._refresh()
    seen.clear()
    assert "翻译中" in win._status.text()
    _put(win, 1, "丁")
    win._drain()
    assert seen == [] and "主服务" in win._status.text() and "翻译中" not in win._status.text()

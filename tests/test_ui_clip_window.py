"""「复制后翻译」小窗：方向自动判断、逐段显示、换方向重翻（旧一轮的结果丢掉）、复制、Esc、历史、摆放位置。"""
import threading

import pytest
from PySide6.QtCore import QPoint, QRect, Qt

from shotrans.models import SegmentResult
from shotrans.ui.clip_window import ClipWindow, place_near


class Router:
    def __init__(self, gate=None):
        self.calls = []
        self.gate = gate  # 给了就等它放行再交结果

    def translate(self, segments, direction, on_result, cancel=None):
        self.calls.append(([s.text for s in segments], direction))
        if self.gate is not None:
            self.gate.wait(3)
        tag = "EN" if direction == "zh2en" else "中"
        for s in segments:
            if cancel is not None and cancel.is_set():
                return
            on_result(SegmentResult(s.idx, f"{tag}:{s.text}", "vllm"))


class History:
    def __init__(self):
        self.entries = []

    def add(self, entry, with_images=False):
        self.entries.append(entry)


@pytest.fixture
def make(qtbot, track, fake_clipboard):
    def _make(paras, router=None, history=None):
        w = ClipWindow(paras, router or Router(), history, cursor=QPoint(200, 200), clipboard=fake_clipboard)
        track(w)
        w.show()
        return w
    return _make


def test_chinese_goes_to_english_and_shows_all(make, qtbot):
    h = History()
    w = make(["抑郁症是一种常见的精神障碍。", "本研究提出新方法。"], history=h)
    assert w.direction == "zh2en" and w.btn_flip.text() == "中→英"
    qtbot.waitUntil(lambda: len(h.entries) == 1, timeout=3000)
    assert w.view.toPlainText() == "EN:抑郁症是一种常见的精神障碍。\n\nEN:本研究提出新方法。"
    e = h.entries[0]
    assert e["source"] == "clipboard" and e["direction"] == "zh2en" and e["service"] == "vllm"
    assert e["segments"][1] == {"src": "本研究提出新方法。", "dst": "EN:本研究提出新方法。"}


def test_english_goes_to_chinese(make, qtbot):
    w = make(["Depression is common."])
    assert w.direction == "en2zh"
    qtbot.waitUntil(lambda: w.view.toPlainText() == "中:Depression is common.", timeout=3000)


def test_pending_placeholder_until_result(make, qtbot):
    gate = threading.Event()
    w = make(["a b c", "d e f"], router=Router(gate))
    assert w.view.toPlainText() == "…\n\n…" and "翻译中" in w.status.text()
    gate.set()
    qtbot.waitUntil(lambda: "…" not in w.view.toPlainText(), timeout=3000)
    assert "主服务" in w.status.text()


def test_flip_retranslates_and_drops_old_round(make, qtbot):
    gate = threading.Event()
    h = History()
    router = Router(gate)
    w = make(["Depression is common."], router=router, history=h)
    w.flip_direction()  # 第一轮还卡着就换方向
    gate.set()
    qtbot.waitUntil(lambda: len(h.entries) == 1, timeout=3000)
    qtbot.wait(100)
    assert [d for _, d in router.calls] == ["en2zh", "zh2en"]
    assert w.view.toPlainText() == "EN:Depression is common." and len(h.entries) == 1
    w.flip_direction()
    qtbot.waitUntil(lambda: len(h.entries) == 2, timeout=3000)
    assert h.entries[0]["session"] == h.entries[1]["session"]  # 同一条历史（History.add 按 session 合并）


def test_copy_button_and_ctrl_c(make, qtbot, fake_clipboard):
    w = make(["Depression is common.", "Second."])
    qtbot.waitUntil(lambda: "…" not in w.view.toPlainText(), timeout=3000)
    w.copy_translation()
    assert fake_clipboard.text() == "中:Depression is common.\n\n中:Second."
    fake_clipboard.setText("")
    qtbot.keyClick(w.view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)  # 没选中：复制全部
    assert fake_clipboard.text() == "中:Depression is common.\n\n中:Second."
    c = w.view.textCursor()
    c.setPosition(0)
    c.setPosition(2, c.MoveMode.KeepAnchor)
    w.view.setTextCursor(c)
    qtbot.keyClick(w.view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)  # 有选中：照常复制选中的
    assert fake_clipboard.text() == "中:"


def test_escape_closes(make, qtbot):
    w = make(["x y"])
    closed = []
    w.closed.connect(closed.append)
    qtbot.keyClick(w.view, Qt.Key.Key_Escape)
    assert closed == [w]


def test_original_toggle(make):
    w = make(["Depression is common.", "Second."])
    assert not w.original.isVisible() and w.btn_original.text() == "原文 ▸"
    w.btn_original.setChecked(True)
    assert w.original.isVisible() and w.original.toPlainText() == "Depression is common.\n\nSecond."
    assert w.btn_original.text() == "原文 ▾"


def test_failure_shown(make, qtbot):
    class Boom(Router):
        def translate(self, *a, **k):
            raise RuntimeError("网络断了")
    w = make(["x y"], router=Boom())
    qtbot.waitUntil(lambda: "网络断了" in w.status.text(), timeout=3000)


def test_unpinned_closes_when_inactive_pinned_stays(make, monkeypatch):
    w = make(["x y"])
    monkeypatch.setattr(w, "isActiveWindow", lambda: False)
    w.btn_pin.click()
    w._close_if_inactive()
    assert w.isVisible()
    w.btn_pin.click()
    w._close_if_inactive()
    assert not w.isVisible()


@pytest.mark.parametrize("cursor,expect", [
    (QPoint(100, 100), QPoint(114, 114)),                          # 右下方放得下
    (QPoint(1900, 100), QPoint(1900 - 14 - 520, 114)),             # 右边放不下：翻到左边
    (QPoint(100, 1000), QPoint(114, 1000 - 14 - 300)),             # 下边放不下：翻到上面
    (QPoint(1910, 1070), QPoint(1910 - 14 - 520, 1070 - 14 - 300)),
])
def test_place_near(cursor, expect):
    assert place_near(cursor, (520, 300), QRect(0, 0, 1920, 1080)) == expect


def test_place_near_clamps_tall_window():
    assert place_near(QPoint(100, 500), (520, 2000), QRect(0, 0, 1920, 1080)).y() == 0

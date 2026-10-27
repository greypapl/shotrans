"""浮窗右键「添加术语…」：只有给了术语表路径才有这一项；对话框存成功后路由换用新术语表，
用到这个词的段（连同右键的段）照常重翻、能撤销；取消则什么都不动；对话框开着时失焦不关窗。"""
import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QDialog

from shotrans.glossary import Glossary
from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.overlay import OverlayWindow
from shotrans.session import Session


class Router:
    def __init__(self):
        self.glossary = Glossary()
        self.calls = []
        self.round = 0

    def vllm_down(self):
        return False

    def vllm_last_error_kind(self):
        return None

    def translate(self, segments, direction, on_result, cancel=None):
        self.calls.append(([s.idx for s in segments], self.glossary))
        self.round += 1
        for s in segments:
            if not s.skip:
                on_result(SegmentResult(s.idx, f"译{s.idx}-{self.round}", "vllm"))


# 三段，各隔开 30 像素以上：分成三段
LINES = [OcrLine("Anhedonia is a core symptom.", Box(10, 10, 280, 20)),
         OcrLine("Nothing relevant here at all.", Box(10, 60, 280, 20)),
         OcrLine("Measure anhedonia daily.", Box(10, 110, 280, 20))]
BAR = 28
ON_SEG2 = QPoint(20, BAR + 65)


class FakeDialog:
    def __init__(self, result, saved):
        self.result, self.saved, self.opened_while = result, saved, None

    def exec(self):
        return self.result


def menu_choosing(monkeypatch, label):
    texts = []

    class FakeMenu:
        def __init__(self, parent=None):
            self.items = []

        def addAction(self, text):
            class A:
                def setEnabled(self, v):
                    pass
            a = A()
            texts.append(text)
            self.items.append((text, a))
            return a

        def addSeparator(self):
            pass

        def exec(self, pos):
            return next((a for t, a in self.items if t == label), None)

    monkeypatch.setattr("shotrans.overlay.QMenu", FakeMenu)
    return texts


@pytest.fixture
def make(qtbot, track, tmp_path):
    def _make(with_path=True, saved_cb=None):
        router = Router()
        s = Session(Image.new("RGB", (300, 150), "white"))
        path = tmp_path / "术语表.txt"
        w = OverlayWindow(s, QRect(100, 200, 300, 150), lambda img: list(LINES), router,
                          glossary_path=path if with_path else None, on_glossary_saved=saved_cb)
        track(w)
        w.show()
        qtbot.waitUntil(lambda: len(s.results) == 3, timeout=3000)
        return w, s, router, path
    return _make


def _ctx(w, pos):
    w.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, w.mapToGlobal(pos)))


def test_menu_item_only_with_glossary_path(make, monkeypatch):
    w, *_ = make(with_path=False)
    texts = menu_choosing(monkeypatch, None)
    _ctx(w, ON_SEG2)
    assert "添加术语…" not in texts
    w2, *_ = make()
    texts.clear()
    _ctx(w2, ON_SEG2)
    assert "添加术语…" in texts


def test_saved_term_retranslates_mentions_and_clicked(make, monkeypatch, qtbot):
    got = []
    w, s, router, path = make(saved_cb=lambda en, zh: got.append((en, zh)))
    path.write_text("anhedonia => 快感缺失\n", encoding="utf-8")  # 对话框（替身）已经写好了文件
    monkeypatch.setattr(w, "_term_dialog", lambda seg: FakeDialog(QDialog.DialogCode.Accepted, ("anhedonia", "快感缺失")))
    menu_choosing(monkeypatch, "添加术语…")
    _ctx(w, ON_SEG2)  # 右键的是第 2 段（不含这个词），也要重翻
    qtbot.waitUntil(lambda: s.results[3].text.endswith("-2"), timeout=3000)
    idxs, glossary = router.calls[-1]
    assert idxs == [1, 2, 3]
    assert glossary.mappings == (("anhedonia", "快感缺失"),)  # 重翻前已换用新术语表
    assert got == [("anhedonia", "快感缺失")]
    assert all(s.can_undo(i) for i in (1, 2, 3))
    s.undo_retranslation(1)
    assert s.results[1].text == "译1-1"


def test_cancel_changes_nothing(make, monkeypatch, qtbot):
    got = []
    w, s, router, path = make(saved_cb=lambda en, zh: got.append((en, zh)))
    monkeypatch.setattr(w, "_term_dialog", lambda seg: FakeDialog(QDialog.DialogCode.Rejected, None))
    menu_choosing(monkeypatch, "添加术语…")
    n = len(router.calls)
    _ctx(w, ON_SEG2)
    qtbot.wait(100)
    assert len(router.calls) == n and got == [] and not s.can_undo(2)


def test_dialog_open_blocks_auto_close(make, monkeypatch):
    w, s, router, path = make()
    seen = {}

    class D(FakeDialog):
        def exec(self):
            seen["menu_open"] = w._menu_open
            w._close_if_inactive()  # 对话框抢走焦点时浮窗会走到这里
            seen["visible"] = w.isVisible()
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(w, "_term_dialog", lambda seg: D(None, None))
    menu_choosing(monkeypatch, "添加术语…")
    _ctx(w, ON_SEG2)
    assert seen == {"menu_open": True, "visible": True} and w._menu_open is False


def test_callback_error_does_not_block_retranslate(make, monkeypatch, qtbot):
    def boom(en, zh):
        raise RuntimeError("x")
    w, s, router, path = make(saved_cb=boom)
    path.write_text("Nothing => 无\n", encoding="utf-8")
    monkeypatch.setattr(w, "_term_dialog", lambda seg: FakeDialog(QDialog.DialogCode.Accepted, ("Nothing", "无")))
    menu_choosing(monkeypatch, "添加术语…")
    _ctx(w, ON_SEG2)
    qtbot.waitUntil(lambda: s.results[2].text.endswith("-2"), timeout=3000)
    assert router.calls[-1][0] == [2]

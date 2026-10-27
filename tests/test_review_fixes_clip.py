"""评审第 1 轮修复：老配置截图键是 Ctrl+Alt+C 仍能启动、Ctrl+C/V/X 不能当快捷键、引导让出键、
词条「同一个」按运行时大小写规则判断、首轮没翻完时加术语整轮重来、没翻成的段不顶掉译文、
模态对话框开着时快捷键只把它提到前面、框选中不开小窗、超长剪贴板先查长度。"""
import threading

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QDialog

from shotrans.config import Config, ConfigError, config_from_raw, validate
from shotrans.glossary_edit import dump_doc, parse_doc
from shotrans.hotkey import parse_hotkey, same_combo
from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.overlay import OverlayWindow
from shotrans.session import Session
from shotrans.term_add import check_term, put_term, same_term


# ---------- 配置 / 快捷键 ----------
def test_old_config_with_capture_ctrl_alt_c_still_loads():
    c = config_from_raw({"hotkey": {"capture": "ctrl+alt+c"}})
    assert c.hotkey == "ctrl+alt+c" and c.clip_hotkey == ""


def test_explicit_same_clipboard_still_an_error():
    with pytest.raises(ConfigError):
        config_from_raw({"hotkey": {"capture": "Ctrl+Alt+C", "clipboard": "Ctrl+Alt+C"}})


@pytest.mark.parametrize("spec", ["Ctrl+C", "ctrl+v", "Ctrl+X"])
def test_copy_paste_keys_rejected(spec):
    with pytest.raises(ValueError, match="复制"):
        parse_hotkey(spec)
    assert validate(Config(clip_hotkey=spec))[0].field == "[hotkey] clipboard"


def test_copy_with_more_modifiers_allowed():
    parse_hotkey("Ctrl+Shift+C")
    parse_hotkey("Ctrl+Alt+C")


def test_same_combo():
    assert same_combo("Ctrl+Alt+Q", "ctrl+alt+q") and not same_combo("Ctrl+Alt+Q", "")
    assert not same_combo("Q", "Q")


# ---------- 词条是不是同一个 ----------
@pytest.mark.parametrize("a,b,same", [
    ("depression", "Depression", True), ("US", "us", True), ("GPU", "Gpu", False),
    ("naive Bayes", "Naive Bayes", True), ("PyTorch", "pytorch", True), ("DSM-5", "DSM-IV", False),
])
def test_same_term(a, b, same):
    assert same_term(a, b) is same and same_term(b, a) is same


def test_case_variant_appended_not_replaced():
    doc = parse_doc("GPU => 图形处理器\n")
    assert check_term(doc, "Gpu", "显卡").existing is None
    put_term(doc, "Gpu", "显卡")
    assert dump_doc(doc) == "GPU => 图形处理器\nGpu => 显卡\n"


# ---------- 浮窗：加术语和首轮翻译的先后 ----------
class GatedRouter:
    """第 1 次调用（首轮）卡在 gate 上，之后的调用立即返回。"""

    def __init__(self, none_on_retry=False):
        self.gate = threading.Event()
        self.calls = []
        self.glossary = None
        self.none_on_retry = none_on_retry

    def vllm_down(self):
        return False

    def vllm_last_error_kind(self):
        return None

    def translate(self, segments, direction, on_result, cancel=None):
        n = len(self.calls) + 1
        self.calls.append([s.idx for s in segments])
        if n == 1:
            self.gate.wait(3)
        for s in segments:
            if cancel is not None and cancel.is_set():
                return
            if n > 1 and self.none_on_retry:
                on_result(SegmentResult(s.idx, s.text, "none", "翻译服务不可用"))
            else:
                on_result(SegmentResult(s.idx, f"译{s.idx}-{n}", "vllm"))


LINES = [OcrLine("Anhedonia is a core symptom.", Box(10, 10, 280, 20)),
         OcrLine("Nothing relevant here at all.", Box(10, 60, 280, 20))]
ON_SEG1 = QPoint(20, 28 + 15)


class Dlg:
    saved = ("anhedonia", "快感缺失")

    def exec(self):
        return QDialog.DialogCode.Accepted


class History:
    def __init__(self):
        self.entries = []
        self.wants_images = False

    def add(self, entry, with_images=False):
        self.entries.append(entry)


def menu_choose(monkeypatch, label):
    class FakeMenu:
        def __init__(self, parent=None):
            self.items = []

        def addAction(self, text):
            class A:
                def setEnabled(self, v):
                    pass
            a = A()
            self.items.append((text, a))
            return a

        def addSeparator(self):
            pass

        def exec(self, pos):
            return next((a for t, a in self.items if t == label), None)

    monkeypatch.setattr("shotrans.overlay.QMenu", FakeMenu)


@pytest.fixture
def overlay(qtbot, track, tmp_path, monkeypatch):
    def _make(router, history=None):
        path = tmp_path / "g.txt"
        path.write_text("anhedonia => 快感缺失\n", encoding="utf-8")
        s = Session(Image.new("RGB", (300, 100), "white"))
        w = OverlayWindow(s, QRect(100, 200, 300, 100), lambda img: list(LINES), router, history,
                          glossary_path=path)
        track(w)
        w.show()
        monkeypatch.setattr(w, "_term_dialog", lambda seg: Dlg())
        menu_choose(monkeypatch, "添加术语…")
        return w, s
    return _make


def _ctx(w, pos):
    w.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, w.mapToGlobal(pos)))


def test_add_term_during_first_round_restarts_whole_round(overlay, qtbot):
    router = GatedRouter()
    h = History()
    w, s = overlay(router, h)
    qtbot.waitUntil(lambda: len(s.segments) == 2 and bool(router.calls), timeout=3000)
    _ctx(w, ON_SEG1)  # 首轮还卡着
    router.gate.set()
    qtbot.waitUntil(lambda: len(h.entries) >= 1, timeout=3000)
    qtbot.wait(100)
    assert router.calls[1] == [1, 2]  # 整轮重来，不是只翻第 1 段
    assert s.results[1].text == "译1-2" and s.results[2].text == "译2-2"
    assert h.entries[-1]["segments"][0]["dst"] == "译1-2"


def test_subset_keeps_translation_when_service_down_and_writes_history(overlay, qtbot):
    router = GatedRouter(none_on_retry=True)
    router.gate.set()
    h = History()
    w, s = overlay(router, h)
    qtbot.waitUntil(lambda: len(h.entries) == 1, timeout=3000)
    _ctx(w, ON_SEG1)
    qtbot.waitUntil(lambda: len(h.entries) == 2, timeout=3000)  # 重翻（哪怕没翻成）结束后也记一次历史
    assert router.calls[1] == [1]
    assert s.results[1].text == "译1-1" and not s.can_undo(1)


def test_subset_success_updates_history(overlay, qtbot):
    router = GatedRouter()
    router.gate.set()
    h = History()
    w, s = overlay(router, h)
    qtbot.waitUntil(lambda: len(h.entries) == 1, timeout=3000)
    _ctx(w, ON_SEG1)
    qtbot.waitUntil(lambda: len(h.entries) == 2, timeout=3000)
    assert h.entries[-1]["segments"][0]["dst"] == "译1-2" and s.can_undo(1)

import json

import pytest
from PySide6.QtGui import QGuiApplication

from shotrans import i18n
from shotrans.ui.history_window import HistoryWindow, first_line, original_text, translation_text
from tests.ui_fakes import FakeApp


def _entry(n, src, dst, direction="en2zh"):
    return {"session": f"s{n}", "time": f"2026-10-13 10:00:0{n}", "direction": direction, "service": "vllm",
            "segments": [{"src": s, "dst": d} for s, d in zip(src, dst)]}


E1 = _entry(1, ["Hello world\nsecond line", "Para two"], ["你好世界", None])
E2 = _entry(2, ["抑郁症的早期识别"], ["Early detection of depression"], "zh2en")
E3 = _entry(3, ["Blood test"], ["血液检测"])


@pytest.fixture
def make(qtbot, tmp_path, track):
    def _make(entries=(E1, E2, E3), confirm=lambda text: True):
        (tmp_path / "history.json").write_text(json.dumps(list(entries), ensure_ascii=False), encoding="utf-8")
        app = FakeApp(tmp_path)
        w = HistoryWindow(app, confirm=confirm)
        track(w)
        w.show()
        return w, app
    return _make


def _visible(w):
    return [i for i in range(w.table.rowCount()) if not w.table.isRowHidden(i)]


def test_text_helpers():
    assert original_text(E1) == "Hello world\nsecond line\n\nPara two"
    assert translation_text(E1) == "你好世界\n\nPara two"  # 没译文的段用原文
    assert first_line(E1) == "Hello world"


def test_newest_first_with_columns(make):
    w, _ = make()
    assert [w.table.item(i, 0).text() for i in range(3)] == [
        "2026-10-13 10:00:03", "2026-10-13 10:00:02", "2026-10-13 10:00:01"]
    assert (w.table.item(1, 1).text(), w.table.item(1, 2).text()) == ("中→英", "抑郁症的早期识别")
    assert (w.table.item(2, 1).text(), w.table.item(2, 2).text()) == ("英→中", "Hello world")


def test_select_shows_full_texts(make):
    w, _ = make()
    w.select(2)
    assert w.src_view.toPlainText() == original_text(E1)
    assert w.dst_view.toPlainText() == translation_text(E1)


def test_copy_buttons(make):
    w, _ = make()
    w.select(0)
    w.btn_copy_src.click()
    assert QGuiApplication.clipboard().text() == "Blood test"
    w.btn_copy_dst.click()
    assert QGuiApplication.clipboard().text() == "血液检测"


def test_search_matches_original_and_translation(make):
    w, _ = make()
    w.search.setText("血液")
    assert _visible(w) == [0]
    w.search.setText("DEPRESSION")
    assert _visible(w) == [1]
    w.search.setText("")
    assert _visible(w) == [0, 1, 2]


def test_delete_selected_only_that_entry(make):
    w, app = make()
    w.select(1)
    assert w.delete_selected() is True
    assert app.history.load() == [E1, E3]
    assert w.table.rowCount() == 2


def test_delete_without_selection(make):
    w, app = make()
    w.table.clearSelection()
    assert w.delete_selected() is False and len(app.history.load()) == 3


def test_clear_needs_confirmation(make):
    asked = []
    w, app = make(confirm=lambda text: asked.append(text) or False)
    assert w.clear_all() is False and len(app.history.load()) == 3
    assert asked == ["确定要清空全部历史记录吗？此操作不能撤销。"]


def test_clear_confirmed(make):
    w, app = make(confirm=lambda text: True)
    assert w.clear_all() is True
    assert app.history.load() == [] and w.table.rowCount() == 0


def test_malformed_entries_skipped(make):
    w, _ = make(entries=(E1, {"time": "坏的"}, "不是字典", {"segments": [{"src": 1}]}, E3))
    assert w.table.rowCount() == 2


def test_language_switch(make):
    w, _ = make()
    i18n.set_language("en")
    assert w.windowTitle() == "History" and w.table.item(1, 1).text() == "ZH→EN"


def test_delete_leaves_no_stale_selection(make):
    w, app = make()
    w.select(1)
    assert w.delete_selected() is True
    assert w._selected() is None
    assert w.src_view.toPlainText() == "" and w.dst_view.toPlainText() == ""
    assert w.delete_selected() is False and app.history.load() == [E1, E3]


def test_filter_hiding_selected_row_clears_selection(make):
    w, app = make()
    w.select(0)
    assert w.src_view.toPlainText() == "Blood test"
    w.search.setText("抑郁")
    assert w._selected() is None and w.src_view.toPlainText() == ""
    assert w.delete_selected() is False and len(app.history.load()) == 3


def test_filter_keeps_visible_selection(make):
    w, _ = make()
    w.select(0)
    w.search.setText("blood")
    assert w._selected() == E3 and w.src_view.toPlainText() == "Blood test"

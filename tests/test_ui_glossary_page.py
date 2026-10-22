"""术语表页：注释灰色只读、空行隐藏、编辑后保存保留注释、搜索、检查标黄、警告确认、导入导出、编码坏了不能编辑。"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from shotrans import i18n
from shotrans.ui.glossary_page import GlossaryPage

SAMPLE = ("# 术语表：一行一条\n\n# —— 心理学 ——\ndepression => 抑郁\nPHQ-8\nanxiety=>焦虑\n\n"
          "# —— 统计 ——\n  p-value   =>  p 值  \n")
WARN = QColor("#fef3c7")


@pytest.fixture
def make(qtbot, tmp_path):
    def _make(text=SAMPLE, confirm=lambda issues: True, raw=None):
        p = tmp_path / "术语表.txt"
        if raw is not None:
            p.write_bytes(raw)
        else:
            p.write_text(text, encoding="utf-8")
        w = GlossaryPage(p, confirm=confirm)
        qtbot.addWidget(w)
        return w, p
    return _make


def _visible(w):
    return [i for i in range(w.table.rowCount()) if not w.table.isRowHidden(i)]


def test_rows_comments_readonly_blanks_hidden(make):
    w, _ = make()
    assert w.table.rowCount() == 9
    item = w.table.item(0, 0)
    assert item.text() == "# 术语表：一行一条" and not (item.flags() & Qt.ItemFlag.ItemIsEditable)
    assert w.table.columnSpan(0, 0) == 2
    assert w.table.isRowHidden(1) and w.table.isRowHidden(6)
    assert (w.table.item(3, 0).text(), w.table.item(3, 1).text()) == ("depression", "抑郁")


def test_not_dirty_after_load_dirty_after_edit(make):
    w, _ = make()
    assert w.is_dirty() is False
    w.table.item(5, 1).setText("焦虑症")
    assert w.is_dirty() is True


def test_edit_and_save_preserves_comments(make):
    w, p = make()
    w.table.item(5, 1).setText("焦虑症")
    assert w.save() is True
    assert p.read_text(encoding="utf-8") == SAMPLE.replace("anxiety=>焦虑", "anxiety => 焦虑症")
    assert w.is_dirty() is False


def test_add_row_then_save(make):
    w, p = make()
    i = w.add_row()
    w.table.item(i, 0).setText("GAD-7")
    w.table.item(i, 1).setText("广泛性焦虑")
    assert w.save() is True
    assert p.read_text(encoding="utf-8") == SAMPLE + "GAD-7 => 广泛性焦虑\n"


def test_delete_entry_row(make):
    w, p = make()
    w.table.selectRow(4)
    assert w.delete_selected() == 1
    w.save()
    assert "PHQ-8" not in p.read_text(encoding="utf-8")
    assert "# —— 心理学 ——" in p.read_text(encoding="utf-8")


def test_delete_ignores_comment_rows(make):
    w, _ = make()
    w.table.selectRow(0)
    assert w.delete_selected() == 0 and w.table.rowCount() == 9


def test_delete_skips_comment_even_if_selected(make):
    # 注释行本来选不中（上一条用例靠的是这个）；这里让它能选中，单独钉住 delete_selected 里的「只删条目」
    w, _ = make()
    w.table.item(0, 0).setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    w.table.selectRow(0)
    assert {ix.row() for ix in w.table.selectedIndexes()} == {0}
    assert w.delete_selected() == 0 and w.table.rowCount() == 9


def test_search_filters_both_columns(make):
    w, _ = make()
    w.set_search("焦虑")
    assert _visible(w) == [5]
    w.set_search("DEPRESSION")
    assert _visible(w) == [3]
    w.set_search("")
    assert _visible(w) == [0, 2, 3, 4, 5, 7, 8]


def test_search_box_drives_filter(make):
    w, _ = make()
    w.search.setText("p 值")
    assert _visible(w) == [8]


def test_empty_src_blocks_save_marks_yellow(make):
    w, p = make()
    i = w.add_row()
    w.table.item(i, 1).setText("抑郁二")
    assert w.save() is False
    assert p.read_text(encoding="utf-8") == SAMPLE
    assert w.table.item(i, 0).background().color() == WARN
    assert "原文为空" in w.issues_label.text()


def test_check_does_not_mark_dirty(make):
    w, _ = make()
    w.check()
    assert w.is_dirty() is False


def test_warning_declined_keeps_file(make):
    seen = []
    w, p = make(SAMPLE + "Depression => 忧郁\n", confirm=lambda issues: seen.append(issues) or False)
    w.table.item(5, 1).setText("焦虑症")
    assert w.save() is False
    assert p.read_text(encoding="utf-8") == SAMPLE + "Depression => 忧郁\n"
    assert [x.code for x in seen[0]] == ["dup_src", "dup_src"]
    assert w.table.item(9, 0).background().color() == WARN


def test_warning_confirmed_saves(make):
    w, p = make(SAMPLE + "Depression => 忧郁\n", confirm=lambda issues: True)
    w.table.item(5, 1).setText("焦虑症")
    assert w.save() is True
    assert "anxiety => 焦虑症" in p.read_text(encoding="utf-8")


def test_import_appends_and_flags_duplicates(make, tmp_path):
    w, _ = make()
    other = tmp_path / "别人的.txt"
    other.write_text("depression => 忧郁\n", encoding="utf-8")
    assert w.import_file(other) == 1
    assert w.table.rowCount() == 10 and w.is_dirty()
    assert {x.row for x in w.check() if x.code == "dup_src"} == {3, 9}


def test_export_writes_current_rows(make, tmp_path):
    w, _ = make()
    w.table.item(5, 1).setText("焦虑症")
    out = tmp_path / "导出.txt"
    w.export_file(out)
    assert out.read_text(encoding="utf-8") == SAMPLE.replace("anxiety=>焦虑", "anxiety => 焦虑症")


def test_bad_encoding_disables_editing(make):
    raw = "抑郁 => 忧郁\n".encode("gbk")
    w, p = make(raw=raw)
    assert w.load_error is True and not w.table.isEnabled()
    assert w.save() is False and p.read_bytes() == raw


def test_language_switch_updates_headers(make):
    w, _ = make()
    assert w.table.horizontalHeaderItem(0).text() == "原文"
    i18n.set_language("en")
    assert w.table.horizontalHeaderItem(0).text() == "Source"


# ---------- 评审第 1 轮 ----------
IGNORED = "a =>\n=> b\nc => d\n"


def test_ignored_rows_grey_readonly_with_tip(make):
    w, _ = make(IGNORED)
    for i in (0, 1):
        item = w.table.item(i, 0)
        assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)
        assert w.table.columnSpan(i, 0) == 2 and not w.table.isRowHidden(i)
        assert "运行时已忽略此行" in item.toolTip()
    assert w.table.item(0, 0).text() == "a =>"


def test_file_with_empty_src_arrow_still_saves(make):
    w, p = make(IGNORED)
    w.table.item(2, 1).setText("e")
    assert w.save() is True
    assert p.read_text(encoding="utf-8") == "a =>\n=> b\nc => e\n"


def test_imported_ignored_row_has_tip(make, tmp_path):
    # 导入不走 retranslate：提示必须在建行时就设上
    w, _ = make()
    other = tmp_path / "别人的.txt"
    other.write_text("x =>\n", encoding="utf-8")
    w.import_file(other)
    assert "运行时已忽略此行" in w.table.item(9, 0).toolTip()


def test_search_matches_ignored_raw(make):
    w, _ = make(IGNORED)
    w.set_search("a =")
    assert _visible(w) == [0]


def test_ignored_tip_follows_language(make):
    w, _ = make(IGNORED)
    i18n.set_language("en")
    assert "Ignored at run time" in w.table.item(0, 0).toolTip()


def test_typed_bad_src_blocks_save(make):
    w, p = make()
    i = w.add_row()
    w.table.item(i, 0).setText("#hashtag")
    w.table.item(i, 1).setText("井号")
    assert w.save() is False and p.read_text(encoding="utf-8") == SAMPLE
    assert "不能以 # 开头" in w.issues_label.text()


def test_pasted_newline_blocks_save(make):
    # 复审实测：QLineEdit 粘贴进来的换行会原样留在单元格里
    w, p = make()
    w.table.item(5, 1).setText("焦虑\ny => z")
    assert w.save() is False and p.read_text(encoding="utf-8") == SAMPLE
    assert w.table.item(5, 1).background().color() == WARN
    assert "有换行" in w.issues_label.text()


def test_mixed_newline_file_edit_keeps_other_entry(make):
    raw = "anhedonia => 快感缺失\nPHQ-8\r\n".encode("utf-8")
    w, p = make(raw=raw)
    assert (w.table.item(1, 0).text(), w.table.item(1, 1).text()) == ("PHQ-8", "")
    w.table.item(0, 1).setText("快感缺乏")
    assert w.save() is True
    assert p.read_bytes() == "anhedonia => 快感缺乏\nPHQ-8\r\n".encode("utf-8")


def test_read_failure_says_read_failed_not_utf8(qtbot, tmp_path):
    d = tmp_path / "术语表.txt"
    d.mkdir()  # 存在但读不了
    w = GlossaryPage(d, confirm=lambda issues: True)
    qtbot.addWidget(w)
    assert w.load_error is True and not w.table.isEnabled()
    assert "读取术语表失败" in w.error_label.text() and "UTF-8" not in w.error_label.text()
    assert w.save() is False


def test_bad_encoding_message_mentions_utf8(make):
    w, _ = make(raw="抑郁 => 忧郁\n".encode("gbk"))
    assert "UTF-8" in w.error_label.text()

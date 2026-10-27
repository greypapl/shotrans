"""添加术语对话框：选词填框、检查红字、替换/警告要问、写盘失败不 accept、勾「保持原样」存成保持原样词条。"""
from PySide6.QtGui import QTextCursor

from shotrans.ui.term_dialog import TermDialog


def make(qtbot, path, text="Anhedonia is a core symptom of 抑郁.", answers=()):
    asked, warned = [], []
    answers = list(answers)

    def ask(title, body):
        asked.append(body)
        return answers.pop(0)

    d = TermDialog(text, path, ask=ask, warn=lambda title, body: warned.append(body))
    qtbot.addWidget(d)
    return d, asked, warned


def select(d, start, end):
    c = d.source.textCursor()
    c.setPosition(start)
    c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    d.source.setTextCursor(c)


def test_selection_fills_matching_box(qtbot, tmp_path):
    d, *_ = make(qtbot, tmp_path / "g.txt")
    select(d, 0, 9)
    assert d.en.text() == "Anhedonia" and d.zh.text() == ""
    select(d, 31, 33)
    assert d.zh.text() == "抑郁" and d.en.text() == "Anhedonia"


def test_save_new_term(qtbot, tmp_path):
    p = tmp_path / "g.txt"
    p.write_text("# 注释\n", encoding="utf-8")
    d, asked, _ = make(qtbot, p)
    d.en.setText(" anhedonia ")
    d.zh.setText("快感缺失")
    d.accept()
    assert d.result() == d.DialogCode.Accepted and d.saved == ("anhedonia", "快感缺失") and asked == []
    assert p.read_text(encoding="utf-8") == "# 注释\nanhedonia => 快感缺失\n"


def test_keep_term(qtbot, tmp_path):
    p = tmp_path / "g.txt"
    d, *_ = make(qtbot, p)
    d.en.setText("DAIC-WOZ")
    d.zh.setText("无视")
    d.keep.setChecked(True)
    assert not d.zh.isEnabled()
    d.accept()
    assert d.saved == ("DAIC-WOZ", "") and p.read_text(encoding="utf-8") == "DAIC-WOZ\n"


def test_missing_chinese_shows_error(qtbot, tmp_path):
    p = tmp_path / "g.txt"
    d, *_ = make(qtbot, p)
    d.en.setText("anhedonia")
    d.accept()
    assert d.saved is None and "中文译法" in d.error.text() and not p.exists()


def test_bad_src_shows_error(qtbot, tmp_path):
    d, *_ = make(qtbot, tmp_path / "g.txt")
    d.en.setText("a => b")
    d.zh.setText("x")
    d.accept()
    assert d.saved is None and "=>" in d.error.text()


def test_replace_asks_and_no_keeps_file(qtbot, tmp_path):
    p = tmp_path / "g.txt"
    p.write_text("depression => 抑郁\n", encoding="utf-8")
    d, asked, _ = make(qtbot, p, answers=[False])
    d.en.setText("Depression")
    d.zh.setText("抑郁症")
    d.accept()
    assert d.saved is None and "depression => 抑郁" in asked[0] and "Depression => 抑郁症" in asked[0]
    assert p.read_text(encoding="utf-8") == "depression => 抑郁\n"


def test_replace_yes_rewrites_row(qtbot, tmp_path):
    p = tmp_path / "g.txt"
    p.write_text("depression => 抑郁\nPHQ-8\n", encoding="utf-8")
    d, asked, _ = make(qtbot, p, answers=[True])
    d.en.setText("depression")
    d.zh.setText("抑郁症")
    d.accept()
    assert d.saved == ("depression", "抑郁症") and len(asked) == 1
    assert p.read_text(encoding="utf-8") == "depression => 抑郁症\nPHQ-8\n"


def test_identical_existing_does_not_ask(qtbot, tmp_path):
    p = tmp_path / "g.txt"
    p.write_text("depression => 抑郁\n", encoding="utf-8")
    d, asked, _ = make(qtbot, p)
    d.en.setText("depression")
    d.zh.setText("抑郁")
    d.accept()
    assert d.saved == ("depression", "抑郁") and asked == []


def test_dup_dst_warning_can_decline(qtbot, tmp_path):
    p = tmp_path / "g.txt"
    p.write_text("depression => 抑郁\n", encoding="utf-8")
    d, asked, _ = make(qtbot, p, answers=[False])
    d.en.setText("major depression")
    d.zh.setText("抑郁")
    d.accept()
    assert d.saved is None and "抑郁" in asked[0] and p.read_text(encoding="utf-8") == "depression => 抑郁\n"


def test_unreadable_file_warns_and_stays(qtbot, tmp_path):
    p = tmp_path / "g.txt"
    raw = "depression => 抑郁\n".encode("gbk")
    p.write_bytes(raw)
    d, _, warned = make(qtbot, p)
    d.en.setText("anhedonia")
    d.zh.setText("快感缺失")
    d.accept()
    assert d.saved is None and len(warned) == 1 and p.read_bytes() == raw


def test_write_failure_warns(qtbot, tmp_path, monkeypatch):
    p = tmp_path / "g.txt"

    def boom(doc, path):
        raise PermissionError("denied")
    monkeypatch.setattr("shotrans.ui.term_dialog.save_doc", boom)
    d, _, warned = make(qtbot, p)
    d.en.setText("anhedonia")
    d.zh.setText("快感缺失")
    d.accept()
    assert d.saved is None and "denied" in warned[0]

"""术语表编辑的纯逻辑：注释与空行原样往返、改过的行按标准格式写、保存前检查、导入导出。"""
import pytest

from shotrans.glossary import parse_glossary
from shotrans.glossary_edit import (GlossaryDoc, Issue, Row, check_rows, dump_doc, export_doc, has_blocking,
                                    import_text, load_doc, parse_doc, save_doc)

SAMPLE = ("# 术语表：一行一条\n\n# —— 心理学 ——\ndepression => 抑郁\nPHQ-8\nanxiety=>焦虑\n\n"
          "# —— 统计 ——\n  p-value   =>  p 值  \n")


def test_round_trip_unchanged():
    assert dump_doc(parse_doc(SAMPLE)) == SAMPLE


def test_round_trip_crlf():
    s = SAMPLE.replace("\n", "\r\n")
    assert dump_doc(parse_doc(s)) == s


def test_crlf_used_for_edited_and_new_rows():
    # 只测原样往返时，按 \n 切也能把 \r 留在原行里蒙混过去；改过的行和新行必须也用 CRLF
    doc = parse_doc("a => b\r\nc => d\r\n")
    doc.rows[0].dst = "x"
    doc.rows.append(Row.entry("e", "f"))
    assert dump_doc(doc) == "a => x\r\nc => d\r\ne => f\r\n"


def test_round_trip_no_trailing_newline():
    assert dump_doc(parse_doc("a => b")) == "a => b"


def test_round_trip_empty():
    assert dump_doc(parse_doc("")) == ""


def test_round_trip_bom():
    s = "\ufeffa => b\n"
    doc = parse_doc(s)
    assert doc.bom is True and dump_doc(doc) == s and dump_doc(doc, with_bom=False) == "a => b\n"


def test_arrow_with_empty_dst_kept_raw():
    assert dump_doc(parse_doc("a =>\n")) == "a =>\n"


def test_row_kinds_and_entries():
    doc = parse_doc(SAMPLE)
    assert [r.kind for r in doc.rows] == ["comment", "blank", "comment", "entry", "entry", "entry", "blank",
                                          "comment", "entry"]
    assert [(r.src, r.dst) for r in doc.rows if r.kind == "entry"] == [
        ("depression", "抑郁"), ("PHQ-8", ""), ("anxiety", "焦虑"), ("p-value", "p 值")]


def test_edited_entry_reformatted_others_kept():
    doc = parse_doc(SAMPLE)
    doc.rows[5].dst = "焦虑症"
    out = dump_doc(doc)
    assert out == SAMPLE.replace("anxiety=>焦虑", "anxiety => 焦虑症")


def test_dst_cleared_becomes_keep_line():
    doc = parse_doc(SAMPLE)
    doc.rows[3].dst = ""
    assert "\ndepression\n" in dump_doc(doc)


def test_empty_new_rows_dropped():
    doc = parse_doc(SAMPLE)
    doc.rows.append(Row.entry("", ""))
    assert dump_doc(doc) == SAMPLE


def test_new_row_formatted():
    doc = parse_doc(SAMPLE)
    doc.rows.append(Row.entry(" GAD-7 ", " 广泛性焦虑 "))
    assert dump_doc(doc) == SAMPLE + "GAD-7 => 广泛性焦虑\n"


def test_semantics_match_runtime_parser():
    doc = parse_doc(SAMPLE)
    doc.rows[5].dst = "焦虑症"
    doc.rows.append(Row.entry("DAIC-WOZ"))
    g = parse_glossary(dump_doc(doc))
    assert g.mappings == (("depression", "抑郁"), ("anxiety", "焦虑症"), ("p-value", "p 值"))
    assert g.keep == ("PHQ-8", "DAIC-WOZ")


# ---------- 检查 ----------
def _rows(*pairs):
    return [Row.entry(s, d) for s, d in pairs]


def test_empty_src_blocks():
    issues = check_rows(_rows(("", "抑郁")))
    assert issues == [Issue(0, "empty_src", True)] and has_blocking(issues)


def test_dup_src_case_insensitive_warns():
    assert check_rows(_rows(("Depression", "抑郁"), ("depression", "忧郁"))) == [
        Issue(0, "dup_src", False), Issue(1, "dup_src", False)]


def test_dup_src_chinese():
    assert check_rows(_rows(("抑郁", "depression"), ("抑郁", "melancholy"))) == [
        Issue(0, "dup_src", False), Issue(1, "dup_src", False)]


def test_same_dst_for_two_src_warns():
    assert check_rows(_rows(("depression", "抑郁"), ("melancholia", "抑郁"))) == [
        Issue(0, "dup_dst", False), Issue(1, "dup_dst", False)]


def test_same_src_same_dst_is_only_dup_src():
    assert check_rows(_rows(("a", "x"), ("A", "x"))) == [Issue(0, "dup_src", False), Issue(1, "dup_src", False)]


def test_single_hanzi_warns():
    assert check_rows(_rows(("mood", "情"))) == [Issue(0, "single_hanzi", False)]


def test_two_hanzi_ok():
    assert check_rows(_rows(("mood", "情绪"))) == []


def test_single_latin_letter_ok():
    assert check_rows(_rows(("alpha", "a"))) == []


def test_keep_entries_not_dup_dst():
    assert check_rows(_rows(("PHQ-8", ""), ("GAD-7", ""))) == []


def test_fully_empty_rows_ignored():
    assert check_rows(_rows(("", ""), ("  ", " "))) == []


def test_row_index_counts_comments():
    rows = [Row.comment("# x"), Row.blank(), Row.entry("", "y")]
    assert check_rows(rows) == [Issue(2, "empty_src", True)]


def test_warnings_not_blocking():
    assert has_blocking([Issue(0, "dup_src", False)]) is False


# ---------- 导入导出 ----------
def test_import_appends():
    doc = parse_doc("a => b\n")
    assert import_text(doc, "# 导入\nc => d\n") == 1
    assert dump_doc(doc) == "a => b\n# 导入\nc => d\n"


def test_import_into_no_trailing_newline():
    doc = parse_doc("a => b")
    import_text(doc, "c\n")
    assert dump_doc(doc) == "a => b\nc"


def test_load_missing_is_empty(tmp_path):
    assert load_doc(tmp_path / "无.txt").rows == []


def test_load_save_bom(tmp_path):
    p = tmp_path / "术语表.txt"
    p.write_bytes(b"\xef\xbb\xbf" + "a => b\n".encode("utf-8"))
    doc = load_doc(p)
    doc.rows[0].dst = "c"
    save_doc(doc, p)
    assert p.read_bytes() == b"\xef\xbb\xbf" + "a => c\n".encode("utf-8")
    assert sorted(x.name for x in tmp_path.iterdir()) == ["术语表.txt"]


def test_load_not_utf8_raises(tmp_path):
    p = tmp_path / "术语表.txt"
    p.write_bytes("抑郁 => 忧郁\n".encode("gbk"))
    import pytest
    with pytest.raises(UnicodeDecodeError):
        load_doc(p)


def test_export_without_bom(tmp_path):
    doc = parse_doc("\ufeffa => b\n")
    out = tmp_path / "导出.txt"
    export_doc(doc, out)
    assert out.read_bytes() == "a => b\n".encode("utf-8")


# ---------- 评审第 1 轮：切行与运行时一致（C1） ----------
MIXED_COMMENT_FIRST = "# ===== 临床与症状 =====\nanhedonia => 快感缺失\nPHQ-8\r\nGAD-7 => 广泛性焦虑\r\n"
MIXED_ENTRY_FIRST = "anhedonia => 快感缺失\nPHQ-8\r\n"
LONE_CR = "a => b\rc => d\n"


def _entries(doc):
    rows = [r for r in doc.rows if r.kind == "entry"]
    return (tuple((r.src, r.dst) for r in rows if r.dst), tuple(r.src for r in rows if not r.dst))


@pytest.mark.parametrize("s", [MIXED_COMMENT_FIRST, MIXED_ENTRY_FIRST, LONE_CR])
def test_mixed_newlines_entries_match_runtime(s):
    g = parse_glossary(s)
    assert _entries(parse_doc(s)) == (g.mappings, g.keep)


@pytest.mark.parametrize("s", [MIXED_COMMENT_FIRST, MIXED_ENTRY_FIRST, LONE_CR])
def test_mixed_newlines_round_trip(s):
    assert dump_doc(parse_doc(s)).encode("utf-8") == s.encode("utf-8")


@pytest.mark.parametrize("s, mappings, keep", [
    (MIXED_COMMENT_FIRST, (("anhedonia", "改过"), ("GAD-7", "广泛性焦虑")), ("PHQ-8",)),
    (MIXED_ENTRY_FIRST, (("anhedonia", "改过"),), ("PHQ-8",)),
    (LONE_CR, (("a", "改过"), ("c", "d")), ()),
])
def test_mixed_newlines_edit_one_keeps_others(s, mappings, keep):
    doc = parse_doc(s)
    next(r for r in doc.rows if r.kind == "entry").dst = "改过"  # 改第一条，同一物理行附近的别的条目不能丢
    g = parse_glossary(dump_doc(doc))
    assert (g.mappings, g.keep) == (mappings, keep)


def test_unchanged_rows_keep_own_eol_edited_use_majority():
    doc = parse_doc("a => b\r\nc => d\r\ne => f\n")
    doc.rows[2].dst = "g"            # 改过的行用多数行尾 CRLF；没改的两行原样
    doc.rows.append(Row.entry("h"))  # 新行也用 CRLF
    assert dump_doc(doc) == "a => b\r\nc => d\r\ne => g\r\nh\r\n"


def test_unchanged_minority_eol_kept():
    doc = parse_doc("a => b\nc => d\r\ne => f\n")
    doc.rows[0].dst = "x"
    assert dump_doc(doc) == "a => x\nc => d\r\ne => f\n"


def test_majority_eol_for_new_rows_lf():
    doc = parse_doc("a => b\nc => d\ne => f\r\n")
    doc.rows.append(Row.entry("h"))
    assert dump_doc(doc) == "a => b\nc => d\ne => f\r\nh\n"


def test_empty_doc_new_row_uses_lf():
    doc = parse_doc("")
    doc.rows.append(Row.entry("h", "i"))
    assert dump_doc(doc) == "h => i\n"


def test_no_trailing_newline_kept_after_edit():
    doc = parse_doc("a => b\r\nc => d")
    doc.rows[1].dst = "x"
    assert dump_doc(doc) == "a => b\r\nc => x"


def test_import_uses_doc_newline():
    doc = parse_doc("a => b\n")
    import_text(doc, "c => d\r\ne\r\n")
    assert dump_doc(doc) == "a => b\nc => d\ne\n"


# ---------- 评审第 1 轮：原文写回会变义（I1） ----------
def test_bad_src_hash_blocks():
    assert check_rows(_rows(("#hashtag", "井号"))) == [Issue(0, "bad_src", True)]


def test_bad_src_hash_after_spaces_blocks():
    assert check_rows(_rows(("  #tag", "标签"))) == [Issue(0, "bad_src", True)]


def test_bad_src_arrow_keep_blocks():
    assert check_rows(_rows(("a=>b", ""))) == [Issue(0, "bad_src", True)]


def test_bad_src_arrow_spaced_blocks():
    assert check_rows(_rows(("x => y", "z"))) == [Issue(0, "bad_src", True)]


# ---------- 评审第 2 轮：粘贴进来的换行（I3） ----------
def test_bad_newline_in_src_blocks():
    assert check_rows(_rows(("x\ny => z", "w"))) == [Issue(0, "bad_newline", True)]


def test_bad_newline_in_dst_blocks():
    assert check_rows(_rows(("a", "b\u2028c"))) == [Issue(0, "bad_newline", True)]


def test_bad_newline_lone_cr_and_formfeed_in_dst():
    assert check_rows(_rows(("a", "b\rc"), ("d", "e\x0cf"))) == [Issue(0, "bad_newline", True),
                                                                 Issue(1, "bad_newline", True)]


def test_edge_newlines_are_stripped_ok():
    # 首尾的换行会被 strip 掉、写回时也不会出现：不算问题（开头的换行 splitlines 会多切出一段，必须先 strip）
    assert check_rows(_rows(("\na\n", "\r\n乙丙\r\n"))) == []


def test_hash_inside_src_ok():
    assert check_rows(_rows(("C#", "C 井"))) == []


# ---------- 评审第 1 轮：运行时忽略的箭头行（I2） ----------
def test_arrow_with_empty_side_is_ignored_kind():
    doc = parse_doc("a =>\n=> b\n  =>  \nc => d\n")
    assert [r.kind for r in doc.rows] == ["ignored", "ignored", "ignored", "entry"]
    assert parse_glossary("a =>\n=> b\n  =>  \n") == parse_glossary("")


def test_ignored_rows_not_checked_and_kept():
    doc = parse_doc("a =>\n=> b\nc => d\n")
    assert check_rows(doc.rows) == []
    doc.rows[2].dst = "e"
    assert dump_doc(doc) == "a =>\n=> b\nc => e\n"


def test_import_counts_only_real_entries():
    doc = parse_doc("")
    assert import_text(doc, "a =>\nb => c\n") == 1


# ---------- 评审第 1 轮：保存落盘与失败清理 ----------
def test_save_fsyncs_before_replace(tmp_path, monkeypatch):
    import os
    calls = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b))[1])
    p = tmp_path / "术语表.txt"
    save_doc(parse_doc("a => b\n"), p)
    assert calls == ["fsync", "replace"] and p.read_text(encoding="utf-8") == "a => b\n"


def test_save_failure_removes_tmp_keeps_original(tmp_path, monkeypatch):
    import os
    p = tmp_path / "术语表.txt"
    p.write_text("旧 => 内容\n", encoding="utf-8")

    def boom(a, b):
        raise PermissionError("被占用")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(PermissionError):
        save_doc(parse_doc("新 => 内容\n"), p)
    assert sorted(x.name for x in tmp_path.iterdir()) == ["术语表.txt"]
    assert p.read_text(encoding="utf-8") == "旧 => 内容\n"


def test_load_unreadable_raises_oserror_not_empty(tmp_path):
    d = tmp_path / "术语表.txt"
    d.mkdir()  # 存在但读不了：必须报错，不能当成空文件（否则保存时会覆盖）
    with pytest.raises(OSError):
        load_doc(d)

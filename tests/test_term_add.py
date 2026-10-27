"""浮窗「添加术语」的纯逻辑：检查、写回（注释/行尾/BOM 原样）、挑出用到词条的段。"""
import pytest

from shotrans.glossary import parse_glossary
from shotrans.glossary_edit import parse_doc
from shotrans.models import Box, OcrLine, Segment
from shotrans.term_add import check_term, mentions, put_term, save_term, side_of

DOC = "# 心理学\r\ndepression => 抑郁\r\nPHQ-8\r\n"


def seg(i, text, skip=False):
    return Segment(i, text, [OcrLine(text, Box(0, 0, 1, 1))], skip=skip)


@pytest.mark.parametrize("text,side", [("anhedonia", "en"), ("快感缺失", "zh"), ("PHQ-9 量表", "zh"), ("", "en")])
def test_side_of(text, side):
    assert side_of(text) == side


@pytest.mark.parametrize("en,zh,code", [
    ("", "抑郁", "empty_en"), ("   ", "", "empty_en"),
    ("a\nb", "x", "bad_newline"), ("a", "x\ny", "bad_newline"),
    ("# note", "x", "bad_src"), ("a => b", "x", "bad_src"), ("a", "x => y", "bad_src"),
])
def test_check_errors(en, zh, code):
    assert check_term(parse_doc(DOC), en, zh).error == code


def test_check_clean_new_term():
    c = check_term(parse_doc(DOC), " anhedonia ", " 快感缺失 ")
    assert c.error is None and c.existing is None and c.warnings == ()


def test_check_keep_term_needs_no_chinese():
    assert check_term(parse_doc(DOC), "DSM-5", "") == check_term(parse_doc(DOC), "DSM-5", "  ")
    assert check_term(parse_doc(DOC), "DSM-5", "").error is None


def test_check_existing_case_insensitive():
    c = check_term(parse_doc(DOC), "Depression", "抑郁症")
    assert c.existing == (1, "depression", "抑郁") and c.warnings == ()


def test_check_existing_keep_row():
    assert check_term(parse_doc(DOC), "phq-8", "").existing == (2, "PHQ-8", "")


def test_check_dup_dst_warns():
    assert check_term(parse_doc(DOC), "major depression", "抑郁").warnings == ("dup_dst",)


def test_replacing_own_translation_is_not_dup():
    # 改的就是「depression => 抑郁」那一行本身：不能因为旧行也写着「抑郁」就报重复
    assert check_term(parse_doc(DOC), "depression", "抑郁").warnings == ()


def test_check_single_hanzi_warns():
    assert check_term(parse_doc(DOC), "lung", "肺").warnings == ("single_hanzi",)


def test_put_term_appends_and_keeps_format():
    doc = parse_doc(DOC)
    put_term(doc, " anhedonia ", " 快感缺失 ")
    from shotrans.glossary_edit import dump_doc
    assert dump_doc(doc) == DOC + "anhedonia => 快感缺失\r\n"


def test_put_term_replaces_in_place():
    from shotrans.glossary_edit import dump_doc
    doc = parse_doc(DOC)
    put_term(doc, "Depression", "抑郁症")
    assert dump_doc(doc) == "# 心理学\r\nDepression => 抑郁症\r\nPHQ-8\r\n"


def test_put_keep_term():
    from shotrans.glossary_edit import dump_doc
    doc = parse_doc("a => b")
    put_term(doc, "DAIC-WOZ", "")
    assert dump_doc(doc) == "a => b\nDAIC-WOZ"  # 原来没有末尾换行：保持


def test_save_term_rereads_disk_and_keeps_bom(tmp_path):
    p = tmp_path / "术语表.txt"
    p.write_bytes(("﻿" + DOC).encode("utf-8"))
    save_term(p, "anhedonia", "快感缺失")
    text = p.read_bytes().decode("utf-8")
    assert text == "﻿" + DOC + "anhedonia => 快感缺失\r\n"
    g = parse_glossary(text.lstrip("﻿"))
    assert ("anhedonia", "快感缺失") in g.mappings and "PHQ-8" in g.keep


def test_save_term_missing_file_creates_it(tmp_path):
    p = tmp_path / "sub" / "术语表.txt"
    save_term(p, "anhedonia", "快感缺失")
    assert p.read_text(encoding="utf-8") == "anhedonia => 快感缺失\n"


def test_save_term_not_utf8_leaves_file(tmp_path):
    p = tmp_path / "术语表.txt"
    raw = "depression => 抑郁\n".encode("gbk")
    p.write_bytes(raw)
    with pytest.raises(UnicodeDecodeError):
        save_term(p, "anhedonia", "快感缺失")
    assert p.read_bytes() == raw


def test_mentions_case_rules_and_skip():
    segs = [seg(1, "Anhedonia is common."), seg(2, "no match here"), seg(3, "ANHEDONIA"),
            seg(4, "anhedonia formula", skip=True), seg(5, "表现为快感缺失。")]
    # 全小写词条不分大小写；中文那边也算；跳过的段不算
    assert [s.idx for s in mentions(segs, "anhedonia", "快感缺失")] == [1, 3, 5]


def test_mentions_whole_word_and_exact_case():
    segs = [seg(1, "US policy"), seg(2, "tell us"), seg(3, "USB")]
    assert [s.idx for s in mentions(segs, "US", "")] == [1]

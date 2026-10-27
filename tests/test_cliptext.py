"""剪贴板文字分段：空行分段；单个换行只在句末标点或列表项处分段，否则接起来。"""
from shotrans.cliptext import split_paragraphs, to_segments


def test_blank_lines_split():
    assert split_paragraphs("first para\n\nsecond para\r\n\r\n\r\nthird") == ["first para", "second para", "third"]


def test_pdf_hard_wraps_joined():
    text = "Depression is a common mental\ndisorder that affects many\npeople worldwide."
    assert split_paragraphs(text) == ["Depression is a common mental disorder that affects many people worldwide."]


def test_hyphen_break_joined():
    assert split_paragraphs("major depres-\nsion is common") == ["major depression is common"]


def test_sentence_end_splits_single_newline():
    assert split_paragraphs("First sentence.\nSecond one") == ["First sentence.", "Second one"]


def test_chinese_paragraphs_from_word():
    text = "抑郁症是一种常见的精神障碍。\n本研究提出了一种新方法。\n"
    assert split_paragraphs(text) == ["抑郁症是一种常见的精神障碍。", "本研究提出了一种新方法。"]


def test_chinese_wrapped_line_joined_without_space():
    assert split_paragraphs("抑郁症是一种常见的\n精神障碍。") == ["抑郁症是一种常见的精神障碍。"]


def test_list_items_split():
    assert split_paragraphs("Two goals\n1. reduce cost\n2. keep accuracy") == [
        "Two goals", "1. reduce cost", "2. keep accuracy"]


def test_whitespace_only():
    assert split_paragraphs("  \n\t\n") == [] and split_paragraphs("") == []


def test_to_segments():
    segs = to_segments(["a", "b"])
    assert [(s.idx, s.text, s.skip) for s in segs] == [(1, "a", False), (2, "b", False)]
    assert segs[0].box.w == 1

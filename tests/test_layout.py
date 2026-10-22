import pytest

from shotrans.layout import detect_direction, group_lines, is_formula, join_lines
from shotrans.models import Box, OcrLine


def L(text, x, y, w=300, h=20):
    return OcrLine(text, Box(x, y, w, h))


def test_detect_direction():
    assert detect_direction("抑郁症识别 PHQ") == "zh2en"      # 5 汉字 / 8
    assert detect_direction("Depression 抑郁") == "en2zh"
    assert detect_direction("") == "en2zh"


# Task 7 用例 08（中文表格，夹着英文路径）的真实识别文本
CASE08_OCR = """赛道
Track1
Track2
人群
老年人(Elder)
年轻人(Young)
对应目录
卜IPDD-AVG2026/MPDD-AVG2026-trainval/Elder、/
卜IPDD-AVG2026/MPDD-AVG2026-trainval/Young/
三个子赛道（用哪些模态）
子赛道
A-V+P
A-V-G+P
G+P
使用的数据
音频+视频+人格特征
音频+视频+步态(IMU传感器）+人格特征
步态+人格特征（不用音视频）"""


def test_direction_chinese_table_with_english_paths_is_zh():
    # 66 个汉字对 17 个英文单词：以中文为主（旧的「汉字/(汉字+字母)」只有 0.43，被判成英文）
    assert detect_direction(CASE08_OCR) == "zh2en"


def test_direction_single_letters_are_not_words():
    # A、V、G、P 这种单字母缩写不算英文单词
    assert detect_direction("A-V-G+P 步态") == "zh2en"


def test_direction_two_hanzi_per_english_word():
    # 一个英文单词约合两个汉字：3 个汉字 > 1 个词 × 2 → 中文；2 个汉字对 1 个词打平 → 英文（见上面原有用例）
    assert detect_direction("抑郁症 depression") == "zh2en"


def test_direction_english_text_with_chinese_term():
    assert detect_direction("The term 抑郁症 means depression in Chinese") == "en2zh"


def test_direction_chinese_sentence_with_english_terms():
    assert detect_direction("使用PHQ-8量表评估抑郁程度，并用BiLSTM建模") == "zh2en"


def test_formula_detection():
    assert is_formula("x = (a + b)^2 / 2")
    assert not is_formula("Results are shown in Table 2.")
    assert not is_formula("一、")


def test_join_lines_english_hyphen_and_space():
    assert join_lines(["The depres-", "sion scale", "is valid."]) == "The depression scale is valid."


def test_join_lines_keeps_hyphen_before_digit():
    assert join_lines(["COVID-", "19 data"]) == "COVID- 19 data"


def test_join_lines_keeps_hyphen_before_capital():
    assert join_lines(["COVID-", "Era data"]) == "COVID- Era data"


def test_join_lines_chinese_no_space():
    assert join_lines(["抑郁症的早期", "识别很重要"]) == "抑郁症的早期识别很重要"


def test_paragraph_lines_merge():
    segs = group_lines([L("First line of", 10, 10), L("the paragraph.", 10, 34)], "para")
    assert [s.text for s in segs] == ["First line of the paragraph."]
    assert segs[0].idx == 1


def test_big_gap_splits_paragraphs():
    segs = group_lines([L("Para one.", 10, 10), L("Para two.", 10, 70)], "para")
    assert [s.text for s in segs] == ["Para one.", "Para two."]


def test_two_columns_stay_separate():
    lines = [L("left a", 10, 10), L("right a", 400, 10), L("left b", 10, 34), L("right b", 400, 34)]
    segs = group_lines(lines, "para")
    assert [s.text for s in segs] == ["left a left b", "right a right b"]
    assert [s.idx for s in segs] == [1, 2]


def test_line_mode_never_merges():
    segs = group_lines([L("First line of", 10, 10), L("the paragraph.", 10, 34)], "line")
    assert [s.text for s in segs] == ["First line of", "the paragraph."]


def test_formula_line_is_own_skipped_segment():
    lines = [L("We define the score as", 10, 10), L("s = (a + b) / 2", 10, 34)]
    segs = group_lines(lines, "para")
    assert len(segs) == 2
    assert segs[1].skip and not segs[0].skip


def test_heights_too_different_not_merged():
    segs = group_lines([L("TITLE", 10, 10, h=40), L("body text", 10, 52, h=20)], "para")
    assert len(segs) == 2


def test_same_row_items_not_merged():
    segs = group_lines([L("OK", 10, 10, w=60), L("Cancel", 10, 12, w=60)], "para")
    assert len(segs) == 2


def test_invalid_mode():
    with pytest.raises(ValueError):
        group_lines([], "word")


def test_line_after_formula_not_merged_into_it():
    lines = [L("s = (a + b) / 2", 10, 10), L("where a is the mean.", 10, 34)]
    segs = group_lines(lines, "para")
    assert len(segs) == 2


def test_math_heavy_line_with_three_words_is_not_formula():
    assert not is_formula("1 + 2 = 3 = 4 = 5 + abc def ghi")


def test_horizontally_disjoint_neighbour_not_merged():
    segs = group_lines([L("ab", 10, 10, w=30), L("cd", 45, 34, w=30)], "para")
    assert len(segs) == 2


def test_formula_too_short_is_not_formula():
    assert not is_formula("1.")


def test_formula_low_math_ratio_is_not_formula():
    assert not is_formula("see Fig 2")


def test_x_offset_splits_even_when_overlapping():
    segs = group_lines([L("a", 10, 10), L("b", 80, 34)], "para")
    assert len(segs) == 2


def test_join_lines_skips_blank_items():
    assert join_lines(["a", "  ", "b"]) == "a b"


def test_join_lines_hyphen_after_non_letter_kept():
    assert join_lines(["x = 3-", "abc"]) == "x = 3- abc"


def test_join_lines_cjk_before_latin_no_space():
    assert join_lines(["抑郁", "PHQ"]) == "抑郁PHQ"


def test_join_lines_latin_before_cjk_no_space():
    assert join_lines(["PHQ", "抑郁"]) == "PHQ抑郁"


# ---------- Task 7b A1：同一视觉行的碎片横向合并 ----------

def test_row_fragments_merged_in_x_order():
    # OCR 把一行在链接处拆成两段，且给出的顺序是右段在前
    segs = group_lines([L("link text", 120, 10, w=80), L("Plain", 10, 12, w=100)], "para")
    assert [s.text for s in segs] == ["Plain link text"]
    assert len(segs[0].lines) == 1 and segs[0].lines[0].box == Box(10, 10, 190, 22)


def test_row_fragments_cjk_joined_by_join_words_rule():
    segs = group_lines([L("抑郁症", 10, 10, w=60), L("PHQ", 75, 10, w=40), L("量表", 120, 10, w=40)], "line")
    assert [s.text for s in segs] == ["抑郁症PHQ量表"]


def test_row_merge_gap_up_to_one_line_height():
    # 行高 20：间距 20 合并，21 不合并
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 130, 10, w=50)], "para")) == 1
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 131, 10, w=50)], "para")) == 2


def test_row_merge_keeps_two_column_rows_apart():
    # BERT 论文 96dpi 实测：行高 14，栏间距 23（约 1.6 倍行高）——两栏同一高度的行不能合
    lines = [L("strictions are sub-optimal for", 7, 31, w=291, h=14),
             L("and could be very harmful", 321, 31, w=290, h=14)]
    segs = group_lines(lines, "para")
    assert [s.text for s in segs] == ["strictions are sub-optimal for", "and could be very harmful"]


def test_row_merge_gap_uses_smaller_height():
    # 表格实测：「Track1」行高 12、「老年人(Elder)」行高 15，间距 14：按较矮者（12）算就不合并
    segs = group_lines([L("Track1", 10, 42, w=38, h=12), L("老年人(Elder)", 62, 41, w=92, h=15)], "para")
    assert len(segs) == 2


def test_row_merge_needs_half_vertical_overlap():
    # 竖直重叠 10/20 = 50%：合并；8/20 = 40%：不合并
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 115, 20, w=50)], "line")) == 1
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 115, 22, w=50)], "line")) == 2


def test_row_merge_allows_small_horizontal_overlap_only():
    # 行高 20：左右框重叠 10 像素（半个行高）仍合并，重叠 11 像素就是叠在一起的两样东西
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 100, 10, w=50)], "line")) == 1
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 99, 10, w=50)], "line")) == 2


def test_row_merge_also_applies_in_line_mode():
    segs = group_lines([L("Plain", 10, 10, w=100), L("link", 115, 10, w=50)], "line")
    assert [s.text for s in segs] == ["Plain link"]


# ---------- Task 7b A2：上标碎片不影响行高判断 ----------

def _sup_lines():
    # 行高 16、行距 21；第二行末尾有个高 11 的上标 [15]（y 24~35），和第二行重叠 4、和第一行重叠 2。
    # （Task 19：段中的行要「写满」才接下一行，第二行由宽 200 改成 270，像真实段落的中间行）
    return [L("first line of text", 10, 10, w=300, h=16),
            L("second line with", 10, 31, w=270, h=16), L("[15]", 282, 24, w=20, h=11),
            L("third line here.", 10, 52, w=300, h=16)]


def test_superscript_line_still_joins_paragraph():
    # 第二行合并上标后外框高 23（16/23 < 0.7），但正文字高仍是 16：照样并进段落
    segs = group_lines(_sup_lines(), "para")
    assert [s.text for s in segs] == ["first line of text second line with [15] third line here."]


def test_superscript_goes_to_row_it_overlaps_most():
    segs = group_lines(_sup_lines()[:3], "line")
    assert [s.text for s in segs] == ["first line of text", "second line with [15]"]


def test_superscript_fragment_attached_to_its_row():
    # 上标只和正文竖直重叠 2 像素（不到一半），但它明显更矮（10 < 0.8×14），仍算同一行
    segs = group_lines([L("mid-1970s,", 10, 28, w=80, h=14), L("[16]", 92, 20, w=20, h=10)], "line")
    assert [s.text for s in segs] == ["mid-1970s, [16]"]


def test_superscript_inside_line_span_attached():
    # 维基百科实测：「mid-1970s, the term …」整行被框住，上标 [16] 落在这一行的横向范围里
    segs = group_lines([L("mid-1970s, the term was", 10, 28, w=300, h=18),
                        L("[16]", 92, 24, w=20, h=13)], "line")
    assert [s.text for s in segs] == ["mid-1970s, the term was [16]"]
    assert segs[0].lines[0].box == Box(10, 24, 300, 22)


def test_similar_height_fragment_with_small_overlap_not_attached():
    # 高 12 对 14（> 0.8 倍，不算上标），竖直只重叠 3 像素：不合并
    segs = group_lines([L("aa", 10, 28, w=80, h=14), L("bb", 92, 19, w=20, h=12)], "line")
    assert len(segs) == 2


def test_large_title_with_small_fragment_stays_separate():
    lines = [L("TITLE", 10, 10, w=200, h=40), L("*", 212, 10, w=10, h=12), L("body text", 10, 52, h=20)]
    segs = group_lines(lines, "para")
    assert [s.text for s in segs] == ["TITLE *", "body text"]


# ---------- Task 7b A3：阅读顺序 ----------

def test_two_columns_left_first_even_if_right_starts_higher():
    # BERT 第 1 页实测：右栏首行 y=13，左栏标题 y=4、正文首行 y=31
    lines = [L("1 Introduction", 8, 4, w=109, h=12),
             L("strictions are sub-optimal", 321, 13, w=290, h=14),
             L("Language model pre-training", 7, 31, w=291, h=14),
             L("and could be very harmful", 321, 31, w=290, h=14),
             L("be effective for improving", 7, 50, w=291, h=14),
             L("tuning based approaches", 321, 50, w=290, h=14)]
    segs = group_lines(lines, "para")
    assert [s.text for s in segs] == [
        "1 Introduction",
        "Language model pre-training be effective for improving",
        "strictions are sub-optimal and could be very harmful tuning based approaches",
    ]
    assert [s.idx for s in segs] == [1, 2, 3]


def test_two_columns_with_nearly_aligned_paragraph_gaps_read_column_first():
    # BERT 第 4 页（用例 05）实测：左栏段落间隙 545~563，右栏 525~546，两栏的间隙只对齐 1 像素。
    # 这不算横贯的空行，应当先读完左栏再读右栏
    def para(x, y0, y1, word):
        return [L(f"{word} some body text", x, y, w=290, h=14) for y in range(y0, y1 - 13, 18)]
    lines = (para(4, 5, 454, "L1") + para(4, 476, 545, "L2") + para(4, 563, 690, "L3")
             + para(317, 6, 525, "R1") + para(317, 546, 886, "R2"))
    segs = group_lines(lines, "para")
    assert [s.text.split()[0] for s in segs] == ["L1", "L2", "L3", "R1", "R2"]


def test_table_rows_read_row_by_row():
    lines = [L("b2", 200, 50, w=40), L("a1", 10, 10, w=40), L("b1", 200, 10, w=40), L("a2", 10, 50, w=40)]
    assert [s.text for s in group_lines(lines, "para")] == ["a1", "b1", "a2", "b2"]


def test_single_column_top_to_bottom():
    lines = [L("Para two.", 10, 70), L("Para one.", 30, 10)]
    assert [s.text for s in group_lines(lines, "para")] == ["Para one.", "Para two."]


# ---------- Task 7b A4：列表项各起一段 ----------

@pytest.mark.parametrize("item", [
    "• the item text", "· the item text", "- the item text", "– the item text", "* the item text",
    "▪ the item text", "1. the item text", "1) the item text", "(1) the item text",
    "a) the item text", "(a) the item text", "一、项目的具体内容", "（一）项目的具体内容",
])
def test_list_item_starts_new_paragraph(item):
    segs = group_lines([L("Intro text", 10, 10), L(item, 10, 34)], "para")
    assert [s.text for s in segs] == ["Intro text", item]
    assert not segs[1].skip          # 是正文列表项，不是被当成公式才分开的


def test_list_items_each_own_paragraph():
    lines = [L("·短时间尺度", 30, 87, h=14), L("·中时间尺度", 30, 109, h=14), L("·长时间尺度", 30, 131, h=14)]
    assert len(group_lines(lines, "para")) == 3


def test_list_item_continuation_line_joins_item():
    segs = group_lines([L("• first item that", 10, 10), L("wraps onto a second line", 22, 34)], "para")
    assert [s.text for s in segs] == ["• first item that wraps onto a second line"]


@pytest.mark.parametrize("cont", [
    "2018). These include", "(2018), we do not use traditional left-to-right or",
    "-based approach", "3.5 million people were",
])
def test_non_list_starts_still_join(cont):
    segs = group_lines([L("Intro text", 10, 10), L(cont, 10, 34)], "para")
    assert len(segs) == 1

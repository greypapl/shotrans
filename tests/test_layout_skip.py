"""Task 20：logo 碎片、作者行不翻（标为 skip，不抹不画）。每个守卫用自己的数据；期望值写死。"""
import pytest

from shotrans.layout import group_lines, is_name_list, is_noise
from shotrans.models import Box, OcrLine


def L(text, x=10, y=10, w=200, h=20):
    return OcrLine(text, Box(x, y, w, h))


# ---------- 无意义碎片 ----------

@pytest.mark.parametrize("text", ["ar iv", "a r Niv", "ar X iv", "x7 qz"])
def test_logo_fragments_are_noise(text):
    assert is_noise(text)


@pytest.mark.parametrize("text", ["OK", "No", "Go", "Niv",          # 单独一块：可能是短词
                                  "I am OK", "Go to", "No, it is",    # 常见短词
                                  "cs.AI", "UI v2",                   # 全大写缩写
                                  "zq Search",                        # 4 个字母以上的词
                                  "ar iv 抑郁",                        # 连续 2 个汉字
                                  "Search", "1.2. 6", ""])
def test_real_short_text_is_not_noise(text):
    assert not is_noise(text)


@pytest.mark.parametrize("text", ["Q&A", "R&D", "U.S.", "A/B"])
def test_joined_acronyms_are_not_noise(text):
    # 用 & / . 连起来的大写字母是一个缩写，不是拆开的碎片
    assert not is_noise(text)


def test_month_abbreviations_are_not_noise():
    assert not is_noise("Jan Feb Mar")


def test_titles_are_not_noise():
    assert not is_noise("Mr. Li")


def test_letter_glued_to_cjk_char_is_not_noise():
    # 「x轴」：字母紧贴着汉字，是一个词；隔着空格的单字（「ar 虍 iv」）不算
    assert not is_noise("x轴 y轴")


@pytest.mark.parametrize("text", ["ab カナ cd", "ab 한국 cd"])
def test_kana_and_hangul_runs_are_not_noise(text):
    assert not is_noise(text)


def test_single_cjk_char_does_not_save_fragments():
    # 只有 1 个汉字（图标常被认成「虍」之类的单字）不算像词
    assert is_noise("ar 虍 iv")


def test_noise_segment_is_skipped():
    segs = group_lines([L("ar iv", 23, 9, 96, 27)], "para")
    assert [(s.text, s.skip) for s in segs] == [("ar iv", True)]


def test_short_word_segment_is_translated():
    segs = group_lines([L("OK", 23, 9, 30, 20)], "para")
    assert [(s.text, s.skip) for s in segs] == [("OK", False)]


# ---------- 人名行 ----------

@pytest.mark.parametrize("text", [
    "Gongbo Zhang, Wen Wang, Ye Tian, Li Yuan",
    "Gongbo Zhang, Wen Wang, and Li Yuan",
    "Alice Smith, Bob Jones & Carol King",
    "Jean-Luc Picard, Kathryn Janeway, Benjamin L. Sisko",
    "Mary O'Neil1, John Ronald Reuel Tolkien2*, Ada Lovelace",
])
def test_author_lines_are_name_lists(text):
    assert is_name_list(text)


@pytest.mark.parametrize("text", [
    "Computer Science, Machine Learning",                                   # 只有两项：和学科名分不开，宁可翻
    "Gongbo Zhang and Wen Wang",                                             # 两个人名同样不 skip（交给提示词）
    "Natural Language Processing, Computer Vision, Information Retrieval",  # 学科常用词
    "Home, About Us, Contact",                                               # 单词项
    "We thank Gongbo Zhang, Wen Wang, Ye Tian, and Li Yuan for comments.",   # 正文句子
    "Turning the TIDE, Cross Architecture, Large Models",                     # 全大写词 TIDE
    "Results, Discussion and Conclusion",
    "Alice Smith, bob jones, Carol King",                                    # 小写
])
def test_non_name_lines_are_not_name_lists(text):
    assert not is_name_list(text)


def test_academic_keyword_lists_are_not_name_lists():
    assert not is_name_list("Depression Detection, Multimodal Fusion, Graph Attention")


def test_accented_names_are_name_lists():
    assert is_name_list("José García, Ana López, Luis Pérez")


@pytest.mark.parametrize("text", [
    "Ashish Vaswani1,2, Noam Shazeer1, Niki Parmar3",
    "Ashish Vaswani¹², Noam Shazeer¹, Niki Parmar³",
    "Ashish Vaswani*, Noam Shazeer†, Niki Parmar1,*",
])
def test_affiliation_marks_are_tolerated(text):
    assert is_name_list(text)


def test_author_line_segment_is_skipped_but_title_is_not():
    segs = group_lines([L("Turning the TIDE: Cross-Architecture Distillation", 20, 122, 1050, 24),
                        L("Gongbo Zhang, Wen Wang, Ye Tian, Li Yuan", 20, 167, 347, 17)], "para")
    assert [(s.text, s.skip) for s in segs] == [
        ("Turning the TIDE: Cross-Architecture Distillation", False),
        ("Gongbo Zhang, Wen Wang, Ye Tian, Li Yuan", True)]

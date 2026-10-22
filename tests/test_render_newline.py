"""译文里带换行（bench 05：「…（Masked LM）\\n直观来说…」）：排版前先折成空格/什么都不留，否则字叠在一起。"""
import pytest
from PIL import Image, ImageDraw, ImageFont

from shotrans.models import Box, OcrLine, Segment
from shotrans.render import FONT_EN, normalize_translation, render

WHITE = (255, 255, 255)


def _page(src_text="source text here"):
    im = Image.new("RGB", (400, 120), WHITE)
    ImageDraw.Draw(im).text((10, 10), src_text, font=ImageFont.truetype(FONT_EN, 16), fill=(0, 0, 0))
    seg = Segment(1, src_text, [OcrLine(src_text, Box(10, 10, 140, 20))])
    return im, [seg]


def _draw(target, text):
    im, segs = _page()
    return render(im, segs, {1: text}, target)


@pytest.mark.parametrize("target,with_break,flat", [
    ("en", "Masked LM task\nIntuitively speaking", "Masked LM task Intuitively speaking"),
    ("en", "alpha beta  \r\n   gamma delta", "alpha beta gamma delta"),
    ("en", "alpha\rbeta\r\rgamma", "alpha beta gamma"),
    ("en", "alpha\tbeta gamma", "alpha beta gamma"),
    ("zh", "任务一：掩码语言模型\n直观来说，随机遮住一些词", "任务一：掩码语言模型直观来说，随机遮住一些词"),
    ("zh", "第一行 \r\n 第二行\t第三行", "第一行第二行 第三行"),
])
def test_newline_translation_renders_exactly_like_the_flattened_text(target, with_break, flat):
    a, b = _draw(target, with_break), _draw(target, flat)
    assert a.placements[1].lines == b.placements[1].lines
    assert a.image.tobytes() == b.image.tobytes()
    assert not any(c in l for l in a.placements[1].lines for c in "\n\r\t")


def test_normalize_translation_cases():
    assert normalize_translation("a\nb", "en") == "a b"
    assert normalize_translation("a \n \n b", "en") == "a b"
    assert normalize_translation("a\r\nb\rc\td", "en") == "a b c d"
    assert normalize_translation("甲\n乙", "zh") == "甲乙"
    assert normalize_translation("no break", "en") == "no break"
    assert normalize_translation("two  spaces kept", "zh") == "two  spaces kept"


def test_zh_break_between_ascii_words_keeps_one_space():
    assert normalize_translation("用 Python\nNumPy 计算", "zh") == "用 Python NumPy 计算"
    assert normalize_translation("版本 3 \n 4 号", "zh") == "版本 3 4 号"


def test_zh_break_between_ascii_symbols_keeps_one_space():
    # 评审 M-e：两侧是 ASCII 符号（不只是字母数字）也留空格
    assert normalize_translation("学 C++\nJava", "zh") == "学 C++ Java"
    assert normalize_translation("用 C#\n(.NET)", "zh") == "用 C# (.NET)"


def test_zh_break_next_to_chinese_leaves_nothing():
    assert normalize_translation("用 Python\n数组", "zh") == "用 Python数组"     # 右边是汉字
    assert normalize_translation("数组\nNumPy", "zh") == "数组NumPy"             # 左边是汉字
    assert normalize_translation("Python，\nNumPy", "zh") == "Python，NumPy"     # 左边是全角标点


@pytest.mark.parametrize("sep", ["\x0b", "\x0c", "\x85", " ", " "])
def test_other_line_separators_fold_like_newline(sep):
    assert normalize_translation(f"alpha{sep}beta", "en") == "alpha beta"
    assert normalize_translation(f"甲{sep}乙", "zh") == "甲乙"


def test_translation_that_is_only_whitespace_and_newlines_draws_nothing():
    im, segs = _page()
    rr = render(im, segs, {1: " \n\r\n\t "}, "en")
    assert rr.placements == {} and rr.image.tobytes() == im.tobytes()

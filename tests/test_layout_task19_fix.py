"""Task 19 评审修正的分段测试：「写满」按换行规则判断；标签要以冒号结尾或足够短。
每个守卫用自己的数据；期望值写死。"""
from PIL import Image, ImageDraw

from shotrans.layout import group_lines
from shotrans.models import Box, OcrLine


def L(text, x, y, w=300, h=20):
    return OcrLine(text, Box(x, y, w, h))


# ---------- 「写满」按换行规则 ----------

def test_ragged_line_before_long_word_continues():
    # 参差不齐的散文：第 2 行只到 65%，因为第 3 行开头是个长词，排不进第 2 行行末——仍是同一段
    lines = [L("Recordings were collected from thirty participants using", 10, 10, w=300, h=14),
             L("a portable headset during the", 10, 28, w=195, h=14),
             L("electroencephalographically guided sessions and later", 10, 46, w=290, h=14)]
    assert len(group_lines(lines, "para")) == 1


def test_short_line_list_still_splits():
    # 短行列表：「Milk」后面剩的空白放得下「Fresh」，却换了行——是有意断开的
    lines = [L("Milk", 10, 10, w=40, h=14), L("Fresh bread rolls", 10, 28, w=170, h=14)]
    assert [s.text for s in group_lines(lines, "para")] == ["Milk", "Fresh bread rolls"]


def test_cjk_first_char_needs_no_space():
    # 中文按字换行、不要空格：下一行 10 个字宽 200 → 字宽 20；第一个字 20 + 放宽 1 个字 = 40。
    # 块右边 210：上一行宽 161（剩 39）接续；宽 160（剩 40）另起一段
    def n(w1):
        return len(group_lines([L("前一行的文字", 10, 10, w=w1, h=20), L("后一行的十个字的文字", 10, 34, w=200, h=20)], "para"))
    assert n(161) == 1 and n(160) == 2


def _formula_lines():
    return [L("vector of the special token as C", 10, 10, w=180, h=14),
            L("and the final hidden vector for the input", 10, 28, w=300, h=14)]


def _ink(x0, x1):
    img = Image.new("RGB", (400, 100), "white")
    ImageDraw.Draw(img).rectangle([x0, 13, x1, 22], fill=(0, 0, 0))
    return img


def test_unrecognised_ink_after_short_line_counts_as_full():
    # bench 05：上一行的框只到 190（OCR 没认出行末的公式），框右边其实还有墨迹到 290：
    # 实际行末 291，离块右边 310 只剩 19，放不下「and 」+ 1 个字（约 37）——写满了，仍是同一段
    assert len(group_lines(_formula_lines(), "para", _ink(200, 290))) == 1


def test_line_end_is_the_last_ink_column():
    # 墨迹只到 250：实际行末 251，剩 59 放得下「and 」——照样拆开（不是「有墨迹就算写满」）
    assert len(group_lines(_formula_lines(), "para", _ink(200, 250))) == 2


def test_blank_after_short_line_still_splits_with_image():
    # 同样的行，框右边是干净底色：照换行规则拆开；不给截图也一样
    assert len(group_lines(_formula_lines(), "para", Image.new("RGB", (400, 100), "white"))) == 2
    assert len(group_lines(_formula_lines(), "para")) == 2


def test_ink_of_next_box_in_the_row_does_not_extend_the_line():
    # 同一行带里右边还有一段 OCR 认出的字（x=250 起）：它的墨迹属于那一段，不算这一行的延续
    lines = _formula_lines() + [L("value", 250, 10, w=50, h=14)]
    assert [s.text for s in group_lines(lines, "para", _ink(250, 299))][:2] == \
        ["vector of the special token as C", "value"]


def test_boxes_outside_the_row_band_do_not_cut_the_scan():
    # 右下方另一行带里有一段（x=200、y=60）：它不在这一行的行带里，不挡这一行行末的墨迹
    lines = _formula_lines() + [L("other", 200, 60, w=50, h=14)]
    assert [s.text for s in group_lines(lines, "para", _ink(200, 290))][0] == \
        "vector of the special token as C and the final hidden vector for the input"


def test_boxes_left_of_the_line_do_not_cut_the_scan():
    # 同一行带里左边还有一段（x=10，离开 50 像素，不并进这一行）：它在这一行左边，不挡行末的墨迹
    img = Image.new("RGB", (500, 100), "white")
    ImageDraw.Draw(img).rectangle([290, 13, 380, 22], fill=(0, 0, 0))
    lines = [L("tag", 10, 10, w=40, h=14), L("vector of the special token as C", 100, 10, w=180, h=14),
             L("and the final hidden vector for the input", 100, 28, w=300, h=14)]
    assert "vector of the special token as C and the final hidden vector for the input" in \
        [s.text for s in group_lines(lines, "para", img)]


def test_falls_back_to_80_percent_without_text():
    # 下一行没有文字（估不出字宽）：退回「上一行右端 ≥ 最长行宽的 80%」——128/160 接续，127 不接
    def n(w1):
        return len(group_lines([L("Item text", 10, 10, w=w1, h=14), L("", 10, 30, w=160, h=14)], "para"))
    assert n(128) == 1 and n(127) == 2


# ---------- 标签：以冒号结尾，或短到值的 1/3 ----------

def test_narrow_column_prose_next_to_wider_column_still_merges():
    # 1:2.5 的两栏、栏距 3 倍行高：窄栏每一行右边都紧挨着 2.5 倍宽的宽栏行，但它们是正文不是标签
    lines = []
    for i in range(3):
        y = 10 + 18 * i
        lines.append(L(f"narrow column line number {i} text", 10, y, w=200, h=14))
        lines.append(L(f"the wider column carries the main body text line {i}", 252, y, w=500, h=14))
    texts = [s.text for s in group_lines(lines, "para")]
    assert len(texts) == 2
    assert texts[0].startswith("narrow column line number 0") and texts[0].endswith("number 2 text")


def test_fullwidth_colon_label_splits():
    lines = [L("这一段的第一行文字写得满满当当的一整行", 22, 326, w=330, h=13),
             L("学科：", 21, 348, w=50, h=13),
             L("计算与语言", 111, 348, w=100, h=13)]
    assert [s.text for s in group_lines(lines, "para")] == ["这一段的第一行文字写得满满当当的一整行", "学科：", "计算与语言"]

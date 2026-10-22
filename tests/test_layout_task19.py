"""Task 19 分段改动的专属测试：全角编号、只有写满的行才接续、标签行不接、行内碎片放宽合并。
每个守卫用自己的数据；期望值写死。"""
import pytest
from PIL import Image, ImageDraw

from shotrans.layout import group_lines
from shotrans.models import Box, OcrLine


def L(text, x, y, w=300, h=20):
    return OcrLine(text, Box(x, y, w, h))


# ---------- 全角编号算列表项 ----------

@pytest.mark.parametrize("item", ["1，打开上次那个页面", "2．等译文出来", "3、点导出", "4）告诉我", "（1）项目的内容", "①项目的内容"])
def test_fullwidth_list_number_starts_new_paragraph(item):
    segs = group_lines([L("引言文字写在这里", 10, 10), L(item, 10, 34)], "para")
    assert [s.text for s in segs] == ["引言文字写在这里", item]


def test_four_digit_year_with_fullwidth_comma_is_not_a_list():
    segs = group_lines([L("引言文字写在这里", 10, 10), L("2018，我们提出了", 10, 34)], "para")
    assert len(segs) == 1


def test_fullwidth_decimal_is_not_a_list():
    segs = group_lines([L("引言文字写在这里", 10, 10), L("1．5倍的差距说明", 10, 34)], "para")
    assert len(segs) == 1


def test_chat_snapshot_numbered_items_are_separate():
    # 2026-10-12 聊天快照的真实识别行（四个全角编号项曾被并成一段）
    lines = [L("1，打开上次那个页面，按Ctr1+A1t+Q，框选和上次差不多的区域。", 42, 256, w=755, h=20),
             L("2．等译文出来，把鼠标移到浮窗上，浮窗上方会出现深色工具栏：", 42, 287, w=531, h=20),
             L("3，点「导出」，浮窗上会提示“已导出到debug/.", 42, 346, w=403, h=20),
             L("4，告诉我导出好了，我直接去读文件。", 42, 375, w=310, h=20)]
    assert [s.text[:2] for s in group_lines(lines, "para")] == ["1，", "2．", "3，", "4，"]


# ---------- 只有写满的行才接续 ----------

def test_short_line_does_not_continue_into_next():
    # （Task 19 修正：「写满」改按换行规则）下一行「abcd efghij」11 个字宽 110 → 字宽 10；
    # 第一个词 4 字 + 空格 = 50，再放宽 1 个字 = 60。块右边 120：上一行宽 51（剩 59）算写满接续；宽 50（剩 60）另起一段
    assert len(group_lines([L("Item text", 10, 10, w=51, h=14), L("abcd efghij", 10, 30, w=110, h=14)], "para")) == 1
    assert len(group_lines([L("Item text", 10, 10, w=50, h=14), L("abcd efghij", 10, 30, w=110, h=14)], "para")) == 2


def test_link_list_items_stay_separate():
    # arXiv 右栏的链接列表（真实框）：View PDF 宽 76 远不到下一行 161 的 80%
    lines = [L("View PDF", 2313, 104, w=76, h=12), L("HTML (experimental)", 2314, 125, w=161, h=15)]
    assert [s.text for s in group_lines(lines, "para")] == ["View PDF", "HTML (experimental)"]


def test_short_heading_not_merged_with_body():
    lines = [L("还有两点", 29, 470, w=80, h=18), L("这次启动的是我单独开的一份干净副本所以导出的文件", 29, 494, w=894, h=20)]
    assert len(group_lines(lines, "para")) == 2


# ---------- 标签行（右边紧挨着值）不接进上面的段 ----------

def _label_rows(value_x, value_w=547):
    # arXiv 快照的真实框：Comments 一行写满，下面「Subjects」标签右边是值
    return [L("Comments: 15 pages, 3 figures. Code: this https URL", 22, 326, w=330, h=13),
            L("Subjects", 21, 348, w=50, h=13),
            L("Computation and Language (cs.CL)", value_x, 348, w=value_w, h=13)]


def test_label_needs_value_three_times_wide_without_colon():
    # （Task 19 修正：不带冒号的标签要短到值的 1/3）值宽 150（= 3×50）算标签；149 不算，「Subjects」照常接上一段
    assert len(group_lines(_label_rows(111, 150), "para")) == 3
    assert [s.text for s in group_lines(_label_rows(111, 149), "para")][0] == \
        "Comments: 15 pages, 3 figures. Code: this https URL Subjects"


def _colon_label_rows(value_w):
    return [L("Comments: 15 pages, 3 figures. Code: this https URL", 22, 326, w=330, h=13),
            L("Subjects:", 21, 348, w=50, h=13),
            L("Computation and Language (cs.CL)", 111, 348, w=value_w, h=13)]


def test_colon_label_needs_value_twice_as_wide():
    # 以冒号结尾的标签：值宽 100（= 2×50）就算；99 不算，照常接上一段
    assert len(group_lines(_colon_label_rows(100), "para")) == 3
    assert [s.text for s in group_lines(_colon_label_rows(99), "para")][0] == \
        "Comments: 15 pages, 3 figures. Code: this https URL Subjects:"


def test_stacked_short_labels_stay_separate():
    # bench 14（arXiv 1706.03762）：「Subjects:」「Cite as:」两个短标签上下相邻，互相都算写满，也不能并成一段
    lines = [L("Subjects:", 21, 348, w=55, h=13), L("Computation and Language (cs.CL)", 111, 348, w=547, h=13),
             L("Cite as:", 22, 368, w=45, h=13), L("arXiv:1706.03762 [cs.CL]", 111, 368, w=151, h=13)]
    texts = [s.text for s in group_lines(lines, "para")]
    assert "Subjects:" in texts and "Cite as:" in texts


def test_label_row_not_joined_to_previous_paragraph():
    texts = [s.text for s in group_lines(_label_rows(111), "para")]
    assert texts == ["Comments: 15 pages, 3 figures. Code: this https URL", "Subjects", "Computation and Language (cs.CL)"]


def test_label_partner_gap_boundary():
    # Subjects 右端 71、行高 13：值在 4×13 = 52 以内（x=123）算标签；53（x=124）就不算，照常接上一段
    assert len(group_lines(_label_rows(123), "para")) == 3
    assert [s.text for s in group_lines(_label_rows(124), "para")][0] == \
        "Comments: 15 pages, 3 figures. Code: this https URL Subjects"


def test_right_partner_must_share_the_row():
    # 段落末行「end.」右边 30 像素处有一段宽的，但在下面一行带里（竖直不重叠）：不是标签的值，末行照常接进段落
    lines = [L("paragraph first line is full", 10, 10, w=300, h=14), L("end.", 10, 28, w=40, h=14),
             L("below", 80, 46, w=200, h=14)]
    assert [s.text for s in group_lines(lines, "para")][0] == "paragraph first line is full end."


def test_full_row_with_right_neighbour_still_joins():
    # 双栏：左栏的行右边紧挨着右栏（栏距 1.6 倍行高），但两栏一样宽，不是「标签: 值」，仍然接成段
    lines = [L("left column first line", 7, 31, w=291, h=14), L("right column first", 321, 31, w=290, h=14),
             L("left column second line", 7, 50, w=291, h=14), L("right column second", 321, 50, w=290, h=14)]
    texts = [s.text for s in group_lines(lines, "para")]
    assert texts == ["left column first line left column second line", "right column first right column second"]


# ---------- 行内碎片放宽合并 ----------

IMG = Image.new("RGB", (1000, 500), "white")   # 放宽合并要看间隙：给一张干净的白图


def test_loose_merge_needs_image():
    # 不给截图就无从判断间隙干不干净：不做放宽合并（间距 30 > 1 倍行高 20）
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 140, 10, w=50)], "line")) == 2
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 140, 10, w=50)], "line", IMG)) == 1


def test_loose_merge_refuses_gap_with_ink():
    # bench 15（GitHub 导航栏）：菜单项之间有下拉箭头。间隙里有墨迹就不合并，否则抹框会把箭头一起抹掉
    img = Image.new("RGB", (400, 100), "white")
    ImageDraw.Draw(img).polygon([(120, 17), (128, 17), (124, 22)], fill=(200, 200, 200))
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 140, 10, w=50)], "line", img)) == 2


def _gap_dot(x, v):
    img = Image.new("RGB", (400, 100), "white")
    img.putpixel((x, 20), (255 - v,) * 3)
    return len(group_lines([L("aa", 10, 10, w=100), L("bb", 140, 10, w=50)], "line", img))


def test_gap_clean_tolerance():
    # 间隙里一个像素比底色暗 10 仍算干净，暗 11 就算有东西（bench 17 维基「隐藏」按钮的浅灰底只差约 20）
    assert _gap_dot(125, 10) == 1 and _gap_dot(125, 11) == 2


def test_gap_check_skips_one_pixel_at_each_end():
    # 间隙是 x=110~139 这 30 列：两端各让 1 列给抗锯齿边——110、139 不看，111、138 要看
    assert _gap_dot(139, 200) == 1 and _gap_dot(138, 200) == 2
    assert _gap_dot(110, 200) == 1 and _gap_dot(111, 200) == 2


def test_two_column_rows_not_loosely_merged():
    # BERT 实测栏距 23（1.6 倍行高），栏间是干净白底：靠「右段是一列竖直对齐的行开头」挡住
    lines = [L("strictions are sub-optimal for", 7, 31, w=291, h=14),
             L("and could be very harmful", 321, 31, w=290, h=14),
             L("Language model pre-training", 7, 50, w=291, h=14),
             L("tuning based approaches", 321, 50, w=290, h=14)]
    assert len(group_lines(lines, "line", IMG)) == 4


def test_chat_fragments_split_by_unknown_symbol_merge():
    # 聊天快照真实框：「中⇄英」的 ⇄ 认不出，右段离开 23 像素（> 1 倍行高 17，≤ 3 倍）
    lines = [L("2．等译文出来，浮窗上方会出现深色工具栏：", 42, 287, w=531, h=20),
             L("．vLLM，译文原/译行/段", 591, 288, w=256, h=18),
             L("中#英", 870, 288, w=48, h=17)]
    assert [s.text for s in group_lines(lines, "line", IMG)] == ["2．等译文出来，浮窗上方会出现深色工具栏：．vLLM，译文原/译行/段中#英"]


def test_loose_gap_boundary_three_line_heights():
    # 行高 20：间距 60 合并，61 不合并
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 170, 10, w=50)], "line", IMG)) == 1
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 171, 10, w=50)], "line", IMG)) == 2


def test_loose_merge_needs_centre_alignment():
    # 间距 30：中线差 5（= 0.25×20）合并，6 不合并
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 140, 15, w=50)], "line", IMG)) == 1
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 140, 16, w=50)], "line", IMG)) == 2


def test_loose_merge_needs_similar_height():
    # 间距 30、中线对齐：高 16（= 0.8×20）合并；高 14 是上标那么矮，不走放宽合并
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 140, 12, w=50, h=16)], "line", IMG)) == 1
    assert len(group_lines([L("aa", 10, 10, w=100), L("bb", 140, 13, w=50, h=14)], "line", IMG)) == 2


@pytest.mark.parametrize("below", [
    L("value two", 140, 50, w=60),         # 左边对齐（右边、中线都不对齐）
    L("value two longer", 100, 50, w=140),  # 右边对齐
    L("value two", 120, 50, w=140),         # 中线对齐
], ids=["left", "right", "centre"])
def test_loose_merge_refuses_table_column(below):
    lines = [L("label", 10, 10, w=100), L("value one", 140, 10, w=100), below]
    texts = [s.text for s in group_lines(lines, "line", IMG)]
    assert "label" in texts and "value one" in texts


def test_column_alignment_tolerance():
    # 行高 20：对齐容差 0.2×20 = 4 像素。下一行左边差 4 算一列（不合并），差 5 不算（合并）
    def texts(dx):
        return [s.text for s in group_lines([L("label", 10, 10, w=100), L("value", 140, 10, w=100),
                                             L("other", 140 + dx, 50, w=60)], "line", IMG)]
    assert "label" in texts(4)
    assert "label value" in texts(5)


def test_column_check_only_looks_nearby():
    # 对齐的行在 5×20 = 100 像素以内才算一列；远在 101 像素外的不算
    def texts(y):
        return [s.text for s in group_lines([L("label", 10, 10, w=100), L("value", 140, 10, w=100),
                                             L("other", 140, y, w=60)], "line", IMG)]
    assert "label" in texts(110)
    assert "label value" in texts(111)

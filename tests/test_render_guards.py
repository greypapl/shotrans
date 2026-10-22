"""render.py 各守卫/分支的专属测试：每个测试只对应一处，去掉那一处就红。"""
import threading

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from shotrans.models import Box, OcrLine, Segment
from shotrans.render import ensure_contrast, fit_text, render, sample_bg, sample_fg, wrap_text

SEGOE = r"C:\Windows\Fonts\segoeui.ttf"
MSYH = r"C:\Windows\Fonts\msyh.ttc"
WHITE = (255, 255, 255)


def _finishes(fn, *args, seconds=3):
    """在线程里跑 fn：死循环时测试判失败，而不是把整个测试进程卡死。"""
    box = []
    t = threading.Thread(target=lambda: box.append(fn(*args)), daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), "疑似死循环"
    return box[0]


def _solid(w, h, rgb):
    return np.full((h, w, 3), rgb, dtype=np.uint8)


def _line_seg(box, idx=1, text="x"):
    return Segment(idx, text, [OcrLine(text, box)])


def _expected(size, draws, bg=WHITE):
    """按「该有的样子」独立画一张：draws = [(xy, text, font_path, px)]，字色黑。"""
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    for xy, text, path, px in draws:
        d.text(xy, text, font=ImageFont.truetype(path, px), fill=(0, 0, 0))
    return np.asarray(img)


# ---------- ensure_contrast ----------

def test_ensure_contrast_keeps_mid_contrast_fg():
    # (100,100,100) 对白底约 6.7:1：够 4.5，原样保留（不能一律换成黑/白）
    assert ensure_contrast((100, 100, 100), WHITE) == (100, 100, 100)


def test_ensure_contrast_replaces_fg_just_below_threshold():
    # (150,150,150) 对白底约 3.0:1：不够 4.5，换成黑
    assert ensure_contrast((150, 150, 150), WHITE) == (0, 0, 0)


# ---------- sample_bg ----------

def test_sample_bg_uses_outer_ring_not_interior():
    arr = _solid(200, 80, WHITE)
    arr[20:50, 50:150] = 0                      # 框内是整块黑，框外是白
    bg, noisy = sample_bg(arr, Box(50, 20, 100, 30))
    assert bg == WHITE and noisy is False


def test_sample_bg_box_covering_whole_image_falls_back_to_interior():
    # 框盖满整张图时外圈为空：退回用框内像素，不能崩
    bg, noisy = sample_bg(_solid(40, 20, (10, 20, 30)), Box(0, 0, 40, 20))
    assert bg == (10, 20, 30) and noisy is False


@pytest.mark.parametrize("box", [
    Box(0, 10, 50, 20),      # 贴左边
    Box(10, 0, 50, 20),      # 贴上边
    Box(70, 10, 60, 20),     # 右边伸出图外
    Box(10, 50, 50, 30),     # 下边伸出图外
], ids=["left", "top", "right", "bottom"])
def test_sample_bg_box_at_image_edge(box):
    bg, noisy = sample_bg(_solid(100, 60, (120, 200, 50)), box)
    assert bg == (120, 200, 50) and noisy is False


def _ring_columns(left_cols, right_cols, value):
    """白底，框 Box(20,20,100,20) 的外圈里，把最左 left_cols 列、最右 right_cols 列涂成 value。"""
    arr = _solid(200, 60, WHITE)
    if left_cols:
        arr[17:43, 20 - left_cols:20] = value
    if right_cols:
        arr[17:43, 120:120 + right_cols] = value
    return arr


BOX = Box(20, 20, 100, 20)       # 外圈共 106*26-100*20 = 756 个像素


def test_sample_bg_noisy_when_over_a_fifth_of_ring_differs():
    # 左右各 3 列 = 156/756 = 20.6% 不同色 → 同色 79.4% < 80% → 杂乱
    _, noisy = sample_bg(_ring_columns(3, 3, 0), BOX)
    assert noisy is True


def test_sample_bg_not_noisy_when_under_a_fifth_of_ring_differs():
    # 只有左 3 列 = 78/756 = 10.3% 不同色 → 同色 89.7% → 不杂乱，中位色仍是白
    bg, noisy = sample_bg(_ring_columns(3, 0, 0), BOX)
    assert noisy is False and bg == WHITE


def test_sample_bg_near_tolerance_not_too_tight():
    # 34% 的外圈像素比白色暗 25（在容差 30 内）→ 仍算同色，不杂乱
    arr = _ring_columns(3, 3, 230)
    arr[17:18, 20:120] = 230
    _, noisy = sample_bg(arr, BOX)
    assert noisy is False


def test_sample_bg_near_tolerance_not_too_loose():
    # 同样 34% 的外圈像素比白色暗 40（超出容差 30）→ 杂乱
    arr = _ring_columns(3, 3, 215)
    arr[17:18, 20:120] = 215
    _, noisy = sample_bg(arr, BOX)
    assert noisy is True


# ---------- sample_fg ----------

def test_sample_fg_returns_actual_ink_colour():
    img = Image.new("RGB", (400, 80), WHITE)
    ImageDraw.Draw(img).text((20, 20), "Hello world", font=ImageFont.truetype(SEGOE, 24), fill=(200, 30, 30))
    fg = sample_fg(np.asarray(img), Box(20, 20, 200, 30), WHITE)
    assert fg[0] > 150 and fg[1] < 100 and fg[2] < 100      # 红色，不是兜底的黑


@pytest.mark.parametrize("bg, expected", [(WHITE, (0, 0, 0)), ((30, 30, 30), (255, 255, 255))])
def test_sample_fg_no_ink_falls_back_to_contrasting_colour(bg, expected):
    arr = _solid(200, 60, bg)
    assert sample_fg(arr, Box(20, 20, 100, 20), bg) == expected


def test_sample_fg_ignores_pixels_too_close_to_bg():
    arr = _solid(200, 60, WHITE)
    arr[20:40, 20:120] = 195                    # 与白底只差 60：不算文字像素 → 兜底黑
    assert sample_fg(arr, Box(20, 20, 100, 20), WHITE) == (0, 0, 0)


def test_sample_fg_counts_pixels_far_from_bg():
    arr = _solid(200, 60, WHITE)
    arr[20:40, 20:120] = 150                    # 差 105：算文字像素，原样返回
    assert sample_fg(arr, Box(20, 20, 100, 20), WHITE) == (150, 150, 150)


def test_sample_fg_is_median_not_mean():
    arr = _solid(200, 60, WHITE)
    arr[20:40, 20:90] = 10                      # 70% 深色
    arr[20:40, 90:120] = 110                    # 30% 稍浅（均值 40，中位数 10）
    assert sample_fg(arr, Box(20, 20, 100, 20), WHITE) == (10, 10, 10)


def _stripes(pattern, rows=20):
    """白底上 Box(20,20,100,20) 里按列重复 pattern（每列一个颜色）。"""
    arr = _solid(200, 60, WHITE)
    for i in range(100):
        arr[20:20 + rows, 20 + i] = pattern[i % len(pattern)]
    return arr


def test_sample_fg_ignores_cleartype_colour_fringes():
    # ClearType：每 8 列里 2 列是黑字芯，6 列是偏紫的彩边（绿通道为 0，单看最大通道差比字芯还「远」）。
    # 各通道取中位数会得到 (120,0,120) 这种紫色；按三通道总差取最远的那部分像素才是黑。
    core, left, right = (20, 20, 20), (120, 0, 200), (200, 0, 120)
    arr = _stripes([core, core, left, left, left, right, right, right])
    assert sample_fg(arr, Box(20, 20, 100, 20), WHITE) == core


def test_sample_fg_extreme_part_is_not_just_a_few_pixels():
    # 10% 纯黑噪点 + 90% 深灰字：深灰才是字色（只取最极端的一小撮就会被噪点带偏）
    arr = _stripes([(0, 0, 0)] + [(60, 60, 60)] * 9)
    assert sample_fg(arr, Box(20, 20, 100, 20), WHITE) == (60, 60, 60)


def test_sample_fg_looks_inside_box_only():
    bg = (30, 30, 30)
    arr = _solid(200, 60, bg)
    arr[17:43, 17:123] = 220                    # 框外一圈亮像素
    arr[20:40, 20:120] = bg                     # 框内没有文字
    assert sample_fg(arr, Box(20, 20, 100, 20), bg) == (255, 255, 255)


# ---------- wrap_text ----------

def test_wrap_text_drops_trailing_space_on_last_line():
    font = ImageFont.truetype(SEGOE, 16)
    assert wrap_text("hello world ", font, 1000) == ["hello world"]


def test_wrap_text_no_empty_or_padded_lines():
    font = ImageFont.truetype(SEGOE, 16)
    lines = wrap_text("a" * 200, font, 50)
    assert lines and all(l and l == l.strip() for l in lines)


def test_wrap_text_trailing_space_does_not_count_toward_width():
    font = ImageFont.truetype(SEGOE, 16)
    w = font.getlength("xx aaa")
    # 「xx aaa」本身恰好放得下一行；aaa 后面那个空格不该算宽度
    assert wrap_text("xx aaa bbb", font, w) == ["xx aaa", "bbb"]


def test_wrap_text_word_that_just_fits_is_not_hard_split():
    font = ImageFont.truetype(SEGOE, 16)
    w = font.getlength("abcdefgh")
    assert wrap_text("xx abcdefgh yy", font, w) == ["xx", "abcdefgh", "yy"]


def test_wrap_text_char_wider_than_box_gets_own_line():
    font = ImageFont.truetype(MSYH, 16)
    assert _finishes(wrap_text, "好好好", font, 5) == ["好", "好", "好"]


def test_wrap_text_latin_char_wider_than_box_gets_own_line():
    font = ImageFont.truetype(SEGOE, 16)
    assert _finishes(wrap_text, "aaa", font, 3) == ["a", "a", "a"]


# ---------- fit_text ----------

def test_fit_text_picks_largest_px_that_fits_width():
    f = fit_text("Hello world again", SEGOE, 120, 24, 30, wrap=False)
    assert f.px == 15 and f.lines == ["Hello world again"] and not f.truncated


def test_fit_text_height_allows_two_pad_slack():
    # 框高 20、单行：字号可到 20 + 2*2 = 24
    f = fit_text("好", MSYH, 200, 20, 40, wrap=False)
    assert f.px == 24 and not f.truncated


def test_fit_text_wrap_height_uses_line_spacing():
    # 60 宽、40 高：12px 时 3 行共 2*15+12=42 ≤ 44，13px 起放不下
    f = fit_text("aaa bbb ccc ddd eee fff", SEGOE, 60, 40, 16, wrap=True)
    assert f.px == 12 and f.lines == ["aaa bbb", "ccc ddd", "eee fff"] and not f.truncated


def test_fit_text_no_wrap_keeps_single_line_even_if_wrappable():
    f = fit_text("aaa bbb ccc ddd eee fff", SEGOE, 60, 200, 16, wrap=False)
    assert len(f.lines) == 1 and f.truncated


def test_fit_text_exactly_min_px_is_not_truncated():
    w9 = ImageFont.truetype(SEGOE, 9).getlength("Hello world again")
    assert w9 < ImageFont.truetype(SEGOE, 10).getlength("Hello world again")   # 前提：只有 9 号放得下
    f = fit_text("Hello world again", SEGOE, int(w9) + 1, 20, 30, wrap=False)
    assert f.px == 9 and not f.truncated


def test_fit_text_start_below_min_px_still_fits():
    f = fit_text("好", MSYH, 100, 20, 5, wrap=False)
    assert f.px == 9 and f.lines == ["好"] and not f.truncated


def test_fit_text_truncation_keeps_max_lines_and_fits_width():
    f = fit_text("很长" * 200, MSYH, 100, 20, 16, wrap=True)
    font = ImageFont.truetype(MSYH, 9)
    assert f.truncated and len(f.lines) == 2        # (20 + 4 - 9) // 11 + 1
    assert all(font.getlength(l) <= 100 for l in f.lines)
    assert f.lines[-1].endswith("…")


def test_fit_text_truncation_line_budget_counts_pad():
    # 框高 18：(18 + 4 - 9) // 11 + 1 = 2 行（不算 PAD 就只有 1 行）
    f = fit_text("很长" * 200, MSYH, 100, 18, 16, wrap=True)
    assert f.truncated and len(f.lines) == 2


def test_fit_text_truncation_in_tiny_box_keeps_one_line():
    f = fit_text("很长" * 200, MSYH, 100, 1, 16, wrap=True)
    assert f.truncated and len(f.lines) == 1 and f.lines[-1].endswith("…")


def test_fit_text_truncation_when_even_ellipsis_does_not_fit():
    f = _finishes(fit_text, "很长很长", MSYH, 3, 20, 16, True)
    assert f.truncated and f.lines[-1] == "…"


# ---------- render ----------

def test_render_single_line_is_vertically_centered():
    # 原文「中文」框高 40 → 起始 38 号；框贴着图的右边和下边（没有空白可延伸），宽 60 只放得下 30 号的「好好」
    # （30 ≥ 下限 38×0.75）：字比框矮 10，上下居中 → 下移 (40-30)//2-1 = 4。
    # （Task 19 前用 300×100 的图、宽 40 的框、缩到 20 号；现在会先向右延伸、且缩小有下限，改成贴边的框）
    img = Image.new("RGB", (80, 60), WHITE)
    r = render(img, [_line_seg(Box(20, 20, 60, 40), text="中文")], {1: "好好"}, "zh")
    assert r.placements[1].px == 30
    assert (np.asarray(r.image) == _expected((80, 60), [((20, 24), "好好", MSYH, 30)])).all()


def test_render_single_line_offset_never_negative():
    # （Task 19：原文改成汉字「中」——字号按字形类别估计，汉字框高 10 → 10 号，与原来的「行高×0.95」一致）
    img = Image.new("RGB", (300, 100), WHITE)
    r = render(img, [_line_seg(Box(20, 20, 250, 10), text="中")], {1: "好"}, "zh")
    assert r.placements[1].px == 10
    assert (np.asarray(r.image) == _expected((300, 100), [((20, 20), "好", MSYH, 10)])).all()


def test_render_multi_line_is_top_aligned_with_line_spacing():
    # （Task 19：原文行改成汉字，理由同上）
    img = Image.new("RGB", (300, 100), WHITE)
    seg = Segment(1, "中文", [OcrLine("中", Box(20, 20, 250, 20)), OcrLine("文", Box(20, 44, 250, 20))])
    text = "一二三四五六七八九十一二三四五六七八九十"
    r = render(img, [seg], {1: text}, "zh")
    p = r.placements[1]
    # 行高 20 → 起点 19 号；两行共 round(19×1.25)+19 = 43 ≤ 44+4，放得下
    assert p.px == 19 and len(p.lines) == 2 and "".join(p.lines) == text
    want = _expected((300, 100), [((20, 20), p.lines[0], MSYH, 19), ((20, 44), p.lines[1], MSYH, 19)])
    assert (np.asarray(r.image) == want).all()


def test_render_keeps_content_between_lines_on_plain_background():
    # 底色干净时只抹各行自己的框，两行之间（隔 10 像素）的东西不动
    img = Image.new("RGB", (300, 120), WHITE)
    img.load()[60, 45] = (0, 0, 0)
    seg = Segment(1, "ab", [OcrLine("a", Box(20, 20, 200, 20)), OcrLine("b", Box(20, 50, 200, 20))])
    out = render(img, [seg], {1: "好"}, "zh").image
    assert out.load()[60, 45] == (0, 0, 0)


def test_render_single_line_segment_does_not_wrap():
    # （Task 19：单行段现在可以折到下方空白里；这里框贴着图的下边，没有空白，就仍然只排一行）
    img = Image.new("RGB", (300, 40), WHITE)
    r = render(img, [_line_seg(Box(20, 20, 100, 20), text="中")], {1: "alpha beta gamma delta epsilon zeta"}, "en")
    assert len(r.placements[1].lines) == 1


@pytest.mark.parametrize("target, path", [("zh", MSYH), ("en", SEGOE)])
def test_render_target_selects_font(target, path):
    # （Task 19：原文改成上伸+下伸都有的「Hg」——框高 40 → 38 号，与原来的「行高×0.95」一致）
    img = Image.new("RGB", (300, 100), WHITE)
    r = render(img, [_line_seg(Box(20, 20, 250, 40), text="Hg")], {1: "Hi"}, target)
    assert (np.asarray(r.image) == _expected((300, 100), [((20, 20), "Hi", path, 38)])).all()


def test_render_target_fonts_really_differ():
    a = _expected((300, 100), [((20, 20), "Hi", MSYH, 38)])
    b = _expected((300, 100), [((20, 20), "Hi", SEGOE, 38)])
    assert not (a == b).all()


@pytest.mark.parametrize("heights, px", [
    ([20], 19),             # 起点 = 汉字行框高 × 0.95
    ([10, 40, 20], 19),     # 乱序三行：中位数 20 → 19
    ([10, 40], 10),         # 两行：取偏小的那个中位数 10 → 9.5 → 10（Task 19 前取偏大的 40 → 38；
                            # 框只会被图标、上标撑大，偏小的更可信）
])
def test_render_start_px_from_median_line_height(heights, px):
    # （Task 19：原文行由「x」改成汉字「中」——字号按字形类别估计，只有 x 高度的「x」会估成框高的 1.67 倍）
    img = Image.new("RGB", (300, 200), WHITE)
    lines, y = [], 20
    for h in heights:
        lines.append(OcrLine("中", Box(20, y, 250, h)))
        y += h + 4
    r = render(img, [Segment(1, "中", lines)], {1: "好"}, "zh")
    assert r.placements[1].px == px


def _ink_rows(arr):
    rows = np.where((arr.min(axis=2) < 128).any(axis=1))[0]
    return rows.max() - rows.min() + 1


@pytest.mark.parametrize("font, px, original, redraw, target", [
    (SEGOE, 20, "Language model pre-training has been shown to be effective", "happy", "en"),
    (SEGOE, 14, "Language model pre-training has been shown to be effective", "happy", "en"),
    (MSYH, 20, "抑郁症的早期识别非常重要，需要多模态数据", "抑郁症", "zh"),
    (MSYH, 14, "抑郁症的早期识别非常重要，需要多模态数据", "抑郁症", "zh"),
])
def test_render_same_font_redraw_matches_original_size(font, px, original, redraw, target):
    # 用真 OCR 量框，再用同一字体重画（译文短，不会因为宽度缩字）：重画字号与原字号差不超过 1，
    # 同一字体同一字号，字形高度也就一样（原文 20 号时旧系数 0.8 只给 17 号）
    from shotrans.ocr import recognize

    f = ImageFont.truetype(font, px)
    img = Image.new("RGB", (int(f.getlength(original)) + 60, px * 3 + 40), WHITE)
    ImageDraw.Draw(img).text((30, 20), original, font=f, fill=(0, 0, 0))
    lines = recognize(img)
    assert len(lines) == 1
    r = render(img, [Segment(1, lines[0].text, lines)], {1: redraw}, target)
    assert abs(r.placements[1].px - px) <= 1


def test_render_same_px_same_glyph_height():
    # 上一条测试的前提：同字体同字号重画，字形墨迹高度一致
    a = _expected((300, 60), [((10, 10), "happy", SEGOE, 20)])
    b = _expected((300, 60), [((10, 10), "Language happy", SEGOE, 20)])
    assert _ink_rows(a) == _ink_rows(b)


def test_render_erases_pad_pixels_around_each_line_box():
    img = Image.new("RGB", (300, 100), WHITE)
    px = img.load()
    marks_erased = [(98, 45), (105, 38), (201, 45), (105, 61)]    # 左 -2、上 -2、右 +1、下 +1
    marks_kept = [(97, 50), (110, 37), (202, 50), (110, 62)]      # 再往外一格：不该动
    for xy in marks_erased + marks_kept:
        px[xy] = (0, 0, 0)
    # （Task 19：原文改成汉字「中」→ 起始 19 号，原框里就放得下；原来的「x」会估成 33 号、延伸后画到标记点上）
    r = render(img, [_line_seg(Box(100, 40, 100, 20), text="中")], {1: "好"}, "zh")
    out = r.image.load()
    assert all(out[xy] == WHITE for xy in marks_erased)
    assert all(out[xy] == (0, 0, 0) for xy in marks_kept)


def test_render_enforces_contrast_on_low_contrast_ink():
    img = Image.new("RGB", (400, 80), WHITE)
    ImageDraw.Draw(img).text((20, 20), "Hello world", font=ImageFont.truetype(SEGOE, 24), fill=(150, 150, 150))
    seg = _line_seg(Box(20, 20, 200, 30))
    assert render(img, [seg], {1: "你好"}, "zh").placements[1].fg == (0, 0, 0)


def test_render_empty_translation_leaves_image_untouched():
    img = Image.new("RGB", (300, 100), WHITE)
    ImageDraw.Draw(img).text((20, 20), "Hello", font=ImageFont.truetype(SEGOE, 24), fill=(0, 0, 0))
    r = render(img, [_line_seg(Box(20, 20, 100, 30))], {1: ""}, "zh")
    assert (np.asarray(r.image) == np.asarray(img)).all() and r.placements == {}


def test_render_placement_reports_geometry_and_truncation():
    # （Task 19：框贴着图的右边和下边，没有空白可延伸；截断发生在下限 round(19×0.75) = 14 号，不再是 9 号）
    img = Image.new("RGB", (120, 40), WHITE)
    r = render(img, [_line_seg(Box(20, 20, 100, 20), idx=7, text="中")], {7: "很长" * 50}, "zh")
    p = r.placements[7]
    assert p.idx == 7 and p.box == Box(20, 20, 100, 20)
    assert p.truncated and p.px == 14 and len(p.lines) == 1 and p.lines[0].endswith("…")


def test_render_accepts_rgba_and_keeps_input_unchanged():
    img = Image.new("RGBA", (300, 100), (255, 255, 255, 255))
    before = np.asarray(img).copy()
    r = render(img, [_line_seg(Box(20, 20, 250, 40))], {1: "好"}, "zh")
    assert r.image.mode == "RGB"
    assert (np.asarray(img) == before).all()

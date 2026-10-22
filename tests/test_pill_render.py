"""Task 22：行内代码胶囊不重画文字，而是把原图那块胶囊剪下来嵌回译文排版。
合成图：白底（或深色底）上画浅灰圆角矩形 + 等宽字。每个守卫用自己的夹具；期望值写死。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

import shotrans.render as R
from shotrans.models import Box, OcrLine, Pill, Segment, SegmentResult
from shotrans.render import MixedFont, PillFont, normalize_translation, pill_scale, render, valid_pill_spans, wrap_text

MSYH = r"C:\Windows\Fonts\msyh.ttc"
SEGOE = r"C:\Windows\Fonts\segoeui.ttf"
CONSOLA = r"C:\Windows\Fonts\consola.ttf"
P0 = "\ue000"   # 排版时第 0 个胶囊换成的私用区字符
P1 = "\ue001"


def pill_scene(page=(255, 255, 255), fill=(240, 240, 239), edge=(216, 216, 215), ink=(150, 40, 40),
               size=(400, 80), rects=(((95, 26, 165, 49), "now.md"),)):
    """白底上画几块描边圆角胶囊（含描边的外框 rects），里面写等宽字。返回 (图, [Pill...])。
    Pill.box 和 pills.py 一样是描边里面的色带。"""
    img = Image.new("RGB", size, page)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(CONSOLA, 15)
    pills = []
    for (x0, y0, x1, y1), text in rects:
        d.rounded_rectangle([x0, y0, x1, y1], radius=4, fill=fill, outline=edge)
        d.text((x0 + 5, y0 + 3), text, font=font, fill=ink)
        pills.append(Pill(text, Box(x0 + 1, y0 + 1, x1 - x0 - 1, y1 - y0 - 1)))
    return img, pills


def seg_with(pills, text="press now.md to start", starts=(6,), box=Box(20, 30, 300, 16)):
    return Segment(1, text, [OcrLine(text, box)],
                   pills=tuple(Pill(p.text, p.box, s) for p, s in zip(pills, starts)))


def colour_bbox(img, colour):
    """img 里某种颜色的像素的外框 (x0, y0, x1, y1)；没有返回 None。"""
    a = np.asarray(img)
    ys, xs = np.nonzero((a == np.array(colour)).all(axis=2))
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# ---------- 贴图 ----------

@pytest.mark.parametrize("page, fill, edge, ink", [
    ((255, 255, 255), (240, 240, 239), (216, 216, 215), (150, 40, 40)),   # 浅色主题、浅灰胶囊
    ((30, 30, 30), (60, 62, 66), (90, 92, 96), (230, 160, 120)),          # 深色主题、深灰胶囊
])
def test_pill_pasted_pixel_exact_when_font_not_shrunk(page, fill, edge, ink):
    img, pills = pill_scene(page, fill, edge, ink)
    seg = seg_with(pills)
    r = render(img, [seg], {1: "按 now.md 开始"}, "zh", pill_spans={1: ((2, 8, 0),)})
    out = r.image
    # 描边是 1 像素：剪图向外多剪 1 像素，所以描边整圈都贴回来了，胶囊连描边 71×24
    bb = colour_bbox(out, edge)
    assert bb is not None and (bb[2] - bb[0], bb[3] - bb[1]) == (71, 24)
    # 字号没缩：原图 1:1，逐像素相同（没有重采样）
    want = np.asarray(img.crop((95, 26, 166, 50)))
    got = np.asarray(out.crop((bb[0], bb[1], bb[2], bb[3])))
    assert (got == want).all()
    assert r.placements[1].pills == 1 and r.placements[1].lines == ["按 now.md 开始"]


def test_pill_moves_with_translation_word_order():
    # 原文胶囊 A 在左、B 在右；译文里 B 在前——贴图跟着译文语序
    img, pills = pill_scene(rects=(((60, 26, 110, 49), "aa"), ((200, 26, 250, 49), "bb")))
    ImageDraw.Draw(img).rectangle([61, 27, 62, 28], fill=(255, 0, 0))     # 两块各做一个记号：A 红、B 蓝
    ImageDraw.Draw(img).rectangle([201, 27, 202, 28], fill=(0, 0, 255))
    seg = seg_with(pills, "see aa and bb ok", (4, 11))
    r = render(img, [seg], {1: "看 bb 和 aa 吧"}, "zh", pill_spans={1: ((2, 4, 1), (7, 9, 0))})
    red, blue = colour_bbox(r.image, (255, 0, 0)), colour_bbox(r.image, (0, 0, 255))
    assert blue[0] < red[0] and blue[0] < 60 + 30      # B 现在排在最前面（靠左）
    assert r.placements[1].pills == 2


def test_no_spans_falls_back_to_text():
    # 没给位置（旧缓存、腾讯不带占位符重翻）：照旧把胶囊文字当文字画，胶囊连描边抹掉
    img, pills = pill_scene()
    r = render(img, [seg_with(pills)], {1: "按 now.md 开始"}, "zh")
    assert colour_bbox(r.image, (216, 216, 215)) is None
    assert r.placements[1].pills == 0


def test_private_use_chars_in_translation_fall_back_to_text():
    # 译文里本来就有私用区字符（图标字体被认出来的字）：会和胶囊记号混淆，这一段整段照旧当文字画
    img, pills = pill_scene()
    r = render(img, [seg_with(pills)], {1: "按 now.md 开始\ue005"}, "zh", pill_spans={1: ((2, 8, 0),)})
    assert colour_bbox(r.image, (216, 216, 215)) is None and r.placements[1].pills == 0


def test_identical_translation_leaves_pill_untouched():
    img, pills = pill_scene()
    r = render(img, [seg_with(pills)], {1: "press now.md to start"}, "zh", pill_spans={1: ((6, 12, 0),)})
    assert r.image.tobytes() == img.tobytes() and r.placements == {}


def test_skipped_segment_leaves_pill_untouched():
    img, pills = pill_scene()
    seg = seg_with(pills)
    seg.skip = True
    r = render(img, [seg], {1: "按 now.md 开始"}, "zh", pill_spans={1: ((2, 8, 0),)})
    assert r.image.tobytes() == img.tobytes()


def test_pill_shrinks_with_font():
    # 段落框很小、译文很长：字号缩到下限，胶囊图按 字号 / 原文字号 等比缩小（不再是 71 宽）
    img, pills = pill_scene(size=(400, 120))
    seg = seg_with(pills, box=Box(20, 30, 200, 16))
    ImageDraw.Draw(img).rectangle([0, 60, 399, 119], fill=(0, 0, 0))      # 下方不能延伸
    ImageDraw.Draw(img).rectangle([230, 0, 399, 59], fill=(0, 0, 0))      # 右边不能延伸
    r = render(img, [seg], {1: "请按照下面的说明把 now.md 打开并检查"}, "zh", pill_spans={1: ((10, 16, 0),)})
    p = r.placements[1]
    assert p.px < 15 and p.pills == 1
    a = np.asarray(r.image.crop((20, 20, 230, 60))).astype(int)
    # 胶囊底色（240）那一片的宽度比原来的 69 窄
    cols = np.nonzero((np.abs(a - [240, 240, 239]).max(axis=2) <= 3).sum(axis=0) >= 4)[0]
    assert 0 < cols.max() - cols.min() + 1 < 69


def test_pill_wider_than_region_drawn_at_region_width():
    # 段落框 100 宽、右边和下方都堵住：180 宽的胶囊等比缩到排进的区域宽度再画，不能画出界
    img, pills = pill_scene(size=(400, 120), rects=(((150, 85, 330, 108), "now.md"),))
    d = ImageDraw.Draw(img)
    d.rectangle([135, 0, 399, 60], fill=(0, 0, 0))
    d.rectangle([0, 60, 130, 80], fill=(0, 0, 0))
    seg = seg_with(pills, box=Box(20, 30, 100, 16))
    r = render(img, [seg], {1: "now.md"}, "zh", pill_spans={1: ((0, 6, 0),)})
    assert r.placements[1].pills == 1
    a = np.asarray(r.image.crop((0, 0, 135, 60))).astype(int)    # 缩放后描边颜色会变，按胶囊底色找
    cols = np.nonzero((np.abs(a - [240, 240, 239]).max(axis=2) <= 3).any(axis=0))[0]
    assert len(cols) and cols.min() >= 20 and cols.max() < 20 + 112


@pytest.mark.parametrize("tr", [
    "now.md 看看看",                  # 起始字号排不下两行，缩到 11 号才放得下（缩字号那一轮找到的区域）
    "now.md 看看看看看看看看看看看看",   # 到下限也放不下，截断（截断用的区域）
])
def test_pill_wider_than_region_after_shrink_or_truncate(tr):
    img, pills = pill_scene(size=(400, 120), rects=(((150, 85, 330, 108), "now.md"),))
    d = ImageDraw.Draw(img)
    d.rectangle([135, 0, 399, 60], fill=(0, 0, 0))
    d.rectangle([0, 63, 130, 80], fill=(0, 0, 0))   # 向下最多到 55（高 25）：两行在 11 号字顶到字底 29 ≤ 25 + 4，12 号 30 放不下
    seg = seg_with(pills, box=Box(20, 30, 100, 16))
    r = render(img, [seg], {1: tr}, "zh", pill_spans={1: ((0, 6, 0),)})
    p = r.placements[1]
    assert p.pills == 1 and p.px == 11 and len(p.lines) == 2 and p.truncated == (len(tr) > 10)
    a = np.asarray(r.image.crop((0, 0, 135, 60))).astype(int)
    cols = np.nonzero((np.abs(a - [240, 240, 239]).max(axis=2) <= 3).any(axis=0))[0]
    assert len(cols) and cols.min() >= 20 and cols.max() < 20 + 112


def test_pill_box_outside_image_is_not_cropped():
    # 剪出来是空的（框在图外）：这个胶囊不换成私用区字符，照旧当文字
    img, _ = pill_scene()
    text, names, images = R._pill_text("按 now.md 开始", (Pill("now.md", Box(500, 30, 60, 20)),), [(2, 8, 0)], img)
    assert (text, names, images) == ("按 now.md 开始", {}, {})


def test_extension_reach_counts_tall_pill_rows():
    # 单行段、右边 15 像素就堵住：三个 40×100 的胶囊只能一个一行排到下方空白，再加一行「。」，
    # 字顶到字底 102 + 102 + 60 + 15 = 279。向下扫描的范围若按「每字一行 × 行距」只有 9 × 19 + 15 = 186，
    # 要按胶囊行高算才够，否则只好缩字号
    img = Image.new("RGB", (400, 400), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for x in (100, 200, 300):
        d.rectangle([x, 280, x + 41, 381], fill=(240, 240, 239), outline=(216, 216, 215))
    pills = [Pill(t, Box(x + 1, 281, 40, 100)) for t, x in (("pq", 100), ("bd", 200), ("ok", 300))]
    d.rectangle([65, 0, 399, 200], fill=(0, 0, 0))
    seg = Segment(1, "pq bd ok", [OcrLine("pq bd ok", Box(10, 20, 40, 16))],
                  pills=tuple(Pill(p.text, p.box, s) for p, s in zip(pills, (0, 3, 6))))
    assert R.start_sizes([seg])[1] == 15
    r = render(img, [seg], {1: "pq bd ok。"}, "zh", pill_spans={1: ((0, 2, 0), (3, 5, 1), (6, 8, 2))})
    p = r.placements[1]
    assert p.pills == 3 and p.px == 15 and not p.truncated


# ---------- 有效位置 ----------

PILLS2 = (Pill("now.md", Box(0, 0, 9, 9)), Pill("Ctr1+K", Box(0, 0, 9, 9)))


def test_valid_spans_accepts_exact_text():
    assert valid_pill_spans("按 now.md 开始", PILLS2, [(2, 8, 0)]) == [(2, 8, 0)]


def test_valid_spans_key_combo_uses_fixed_spelling():
    # 胶囊 OCR 是「Ctr1+K」，占位符换回的是纠错后的「Ctrl+K」
    assert valid_pill_spans("按 Ctrl+K 键", PILLS2, [(2, 8, 1)]) == [(2, 8, 1)]


def test_valid_spans_rejects_text_mismatch():
    assert valid_pill_spans("按 now.mx 开始", PILLS2, [(2, 8, 0)]) == []


def test_valid_spans_rejects_unknown_pill_index():
    assert valid_pill_spans("按 now.md 开始", PILLS2[:1], [(2, 8, 1)]) == []


def test_valid_spans_rejects_negative_index():
    assert valid_pill_spans("now.md", PILLS2, [(0, 6, -2)]) == []


def test_valid_spans_rejects_reused_pill():
    assert valid_pill_spans("now.md now.md", PILLS2, [(0, 6, 0), (7, 13, 0)]) == [(0, 6, 0)]


def test_valid_spans_rejects_overlap():
    pills = (Pill("ab", Box(0, 0, 9, 9)), Pill("bc", Box(0, 0, 9, 9)))
    assert valid_pill_spans("abc", pills, [(0, 2, 0), (1, 3, 1)]) == [(0, 2, 0)]


def test_valid_spans_rejects_out_of_range():
    # 越界（切片会悄悄截短）：起点 ≥ 止点、止点超出译文
    pills = (Pill("", Box(0, 0, 9, 9)),)
    assert valid_pill_spans("abc", pills, [(3, 3, 0)]) == []
    assert valid_pill_spans("now.md", PILLS2, [(0, 9, 0)]) == []


# ---------- 排版：PillFont ----------

def pf(px=20, size=(40, 30), font=MSYH, max_w=None):
    return PillFont(MixedFont(font, px), {0: Image.new("RGB", size, (1, 200, 3)), 1: Image.new("RGB", (10, 10))},
                    1.0, max_w)


def test_pill_width_counts_as_image_width():
    base = MixedFont(MSYH, 20)
    assert pf().getlength("a " + P0) == base.getlength("a ") + 40   # 挨着空格：不再加间隙


def test_gap_next_to_cjk_is_quarter_em_each_side():
    base = MixedFont(MSYH, 20)
    assert pf().getlength("中" + P0 + "中") == pytest.approx(2 * base.getlength("中") + 40 + 2 * 5.0)


def test_gap_next_to_latin_is_smaller():
    base = MixedFont(MSYH, 20)
    assert pf().getlength("a" + P0 + ",") == pytest.approx(base.getlength("a") + base.getlength(",") + 40 + 2 * 2.0)


def test_gap_between_two_pills_is_wide():
    assert pf().getlength(P0 + P1) == pytest.approx(40 + 10 + 5.0)


def test_fullwidth_punctuation_counts_as_wide():
    base = MixedFont(MSYH, 20)
    assert pf().getlength(P0 + "。") == pytest.approx(40 + base.getlength("。") + 5.0)


def test_wrap_moves_whole_pill_to_next_line():
    font = pf()
    w = MixedFont(MSYH, 20).getlength("中中中")
    assert wrap_text("中中中" + P0, font, w + 30) == ["中中中", P0]


def test_pill_wider_than_line_shrinks_to_line_width():
    assert pf(size=(100, 20)).within(50).size(0) == (50, 10)
    assert pf(size=(100, 20)).within(150).size(0) == (100, 20)


def test_pill_scale_one_when_not_shrunk_else_ratio():
    assert pill_scale(15, 15) == 1.0                # 没缩：1:1
    assert pill_scale(10, 15) == pytest.approx(2 / 3)
    assert pill_scale(16, 15) == 1.0                # 字号比起始还大（起始低于下限被抬上来）：不放大


def test_pill_scale_is_continuous_at_first_shrink():
    # 评审 m2：刚缩一号（14 → 13），胶囊也只缩 13/14，不会一下缩到 0.76
    assert pill_scale(14, 14) == 1.0 and pill_scale(13, 14) == pytest.approx(13 / 14)


def test_scaled_size_rounds_and_keeps_aspect():
    f = PillFont(MixedFont(MSYH, 10), {0: Image.new("RGB", (71, 24))}, 0.5)
    assert f.size(0) == (36, 12)


def test_resized_image_is_lanczos_and_cached(monkeypatch):
    f = PillFont(MixedFont(MSYH, 10), {0: Image.new("RGB", (40, 20))}, 0.5)
    calls = []
    real = Image.Image.resize
    monkeypatch.setattr(Image.Image, "resize", lambda self, s, m=None, *a, **k: calls.append((s, m)) or real(self, s, m))
    assert f.image(0).size == (20, 10) and f.image(0).size == (20, 10)
    assert calls == [((20, 10), Image.LANCZOS)]


def test_line_height_ignores_pill_chars_but_stack_makes_room():
    font = pf(px=12, size=(40, 30))
    lh = font.line_height("中" + P0)
    assert lh == MixedFont(MSYH, 12).line_height("中")
    # msyh 12 号「中」的字形框 y 2～14，中线 mid = 8：30 高的胶囊画在字顶下 8 − 15 = −7 到 23。
    # 按取整余量算：伸出字顶 ⌈15 − 8 + 0.5⌉ = 8，伸出字底 ⌈8 + 15 + 0.5 − 12⌉ = 12。
    # 两行都有胶囊：行距 = 字号 12 + 上一行伸出字底 12 + 下一行伸出字顶 8 + 2 = 34
    assert R._stack(font, [P0, P0], lh) == ([0, 34], 8, 12)
    # 一行字、一行胶囊：12 + 0 + 8 + 2 = 22
    assert R._stack(font, ["中", P0], lh) == ([0, 22], 0, 12)
    # 没有胶囊的行：和以前一样
    assert R._stack(font, ["中", "中"], lh) == ([0, lh], 0, 0)


def test_stack_never_closer_than_line_spacing():
    # 胶囊只比字高一点（22 对 20 号字，mid = 15.5）：不伸出字顶，按位置算的行距 20 + 0 + 0 + 2 = 22 比行距 25 还小，取行距；
    # 伸出字底 ⌈15.5 + 11 + 0.5 − 20⌉ = 7
    font = pf(px=20, size=(40, 22))
    lh = font.line_height("中")
    assert lh == 25 and R._stack(font, ["中", P0], lh) == ([0, 25], 0, 7)


def test_stack_for_plain_font_unchanged():
    f = MixedFont(MSYH, 12)
    assert R._stack(f, ["a", "b", "c"], 15) == ([0, 15, 30], 0, 0)


def test_fit_height_counts_pill_rows():
    # 三行胶囊（中心距 32）字顶到字底 84：框高 79（+4 = 83）放不下；只按行距算（2 × 25 + 20 = 70）会以为放得下
    font = pf(px=20, size=(40, 30))
    assert R._lines_if_fit(P0 * 3, font, 20, 40, 79, True) is None


def test_fit_counts_pill_width():
    # 文字只有两个字，但胶囊 200 宽：一行 120 宽放不下（胶囊缩到 120 也要单独一行），一行高的框装不下两行
    font = pf(px=12, size=(200, 14))
    assert R._lines_if_fit("中中" + P0, font, 12, 120, 14, True) is None
    assert R._lines_if_fit("中中" + P0, font, 12, 120, 60, True) == ["中中", P0]


def test_fit_quick_reject_does_not_count_gaps():
    # 「至少几行」的估计不能把间隙算进去：折行处的间隙不占宽度，算进去会估多、把放得下的字号排除掉。
    # 三个 40 宽的胶囊、行宽 40：一行一个，三行（中心距 32）高 64 + 20 = 84；带间隙估成 130 / 40 → 4 行就错杀了
    font = pf(px=20, size=(40, 30))
    assert R._lines_if_fit(P0 * 3, font, 20, 40, 84, True) == [P0, P0, P0]


def test_truncate_shrinks_too_wide_pill_to_line_width():
    # 胶囊 150×60、行宽 90：缩到 90×36 单独一行，伸出字底 ⌈8 + 18 + 0.5 − 12⌉ = 15；两行行距 12 + 15 + 2 = 29，
    # 字顶到字底 41 ≤ 38 + 4，两行都留。不缩的话按 60 高算（伸出 27，行距 41）放不下第二行，
    # 胶囊那行再加「…」也放不下，什么都不剩
    font_make = lambda px: pf(px=px, size=(150, 60))
    fit = R._truncate(P0 + "中中中中中中中中", font_make, 90, 38, 12, True)
    assert fit.truncated and fit.lines[0] == P0 and len(fit.lines) == 2


def test_truncate_counts_pill_width():
    # 胶囊 60 宽：「P0 中中」= 60 + 3 + 24 = 87，再加「…」就超出 90，要再去掉一个字
    font_make = lambda px: pf(px=px, size=(60, 14))
    fit = R._truncate(P0 + "中中中中中", font_make, 90, 14, 12, True)
    assert fit.truncated and fit.lines == [P0 + "中…"]


def test_truncate_drops_line_when_pill_rows_are_taller():
    # 两行字的高度放得下（12 + 15 = 27 ≤ 30 + 4），但第二行是 40 高的胶囊：只留一行
    font_make = lambda px: pf(px=px, size=(30, 40))
    lines = R._truncate("中中中中中中" + P0 + "中", font_make, 75, 30, 12, True).lines
    assert len(lines) == 1


@pytest.mark.parametrize("h", [10, 50])
def test_pill_centre_aligned_with_text_centre(h):
    # 评审 I1：胶囊高 30 时居中和「顶端对齐行顶」只差半像素，测不出区别。用离 2 × mid（31）远的 10 和 50
    canvas = Image.new("RGB", (200, 100), (255, 255, 255))
    font = PillFont(MixedFont(MSYH, 20), {0: Image.new("RGB", (30, h), (0, 0, 255))}, 1.0)
    font.draw(ImageDraw.Draw(canvas), (10, 30), "中" + P0, (0, 0, 0), canvas)
    a = np.asarray(canvas).astype(int)
    blue = np.nonzero((a == [0, 0, 255]).all(axis=2))[0]
    assert blue.max() - blue.min() + 1 == h
    ink = np.nonzero((a[:, :40] < 128).all(axis=2))[0]        # 「中」的墨迹（胶囊在它右边）
    assert abs((blue.min() + blue.max()) / 2 - (ink.min() + ink.max()) / 2) <= 1.5


def test_draw_matches_measured_width():
    canvas = Image.new("RGB", (300, 60), (255, 255, 255))
    font = pf(px=20)
    font.draw(ImageDraw.Draw(canvas), (10, 10), "中" + P0 + "中", (0, 0, 0), canvas)
    a = np.asarray(canvas).astype(int)
    xs = np.nonzero((a == [1, 200, 3]).all(axis=2))[1]
    base = MixedFont(MSYH, 20)
    assert xs.min() == round(10 + base.getlength("中") + 5.0) and xs.max() - xs.min() + 1 == 40


def test_area_covers_pill_on_last_line_of_long_paragraph():
    # 评审 m1：多行段落、胶囊在末行。area（快照里记的译文占位、防重叠的依据）要盖住胶囊实际画到的上下沿
    img, pills = pill_scene(size=(400, 200), rects=(((300, 150, 371, 190), "now.md"),))   # 70×39 的高胶囊
    seg = Segment(1, "a b c d", [OcrLine("first line here", Box(20, 20, 120, 16)), OcrLine("second", Box(20, 40, 120, 16)),
                                 OcrLine("third", Box(20, 60, 120, 16)), OcrLine("fourth now.md", Box(20, 80, 120, 16))],
                  pills=(Pill("now.md", pills[0].box, None),))
    tr = "第一行的字很多很多很多第二行的字很多很多很多第三行的字很多很多很多 now.md"
    r = render(img, [seg], {1: tr}, "zh", pill_spans={1: ((tr.index("now.md"), len(tr), 0),)})
    p = r.placements[1]
    assert p.pills == 1 and len(p.lines) >= 4 and p.lines[-1].endswith("now.md")
    a = np.asarray(r.image).astype(int)
    rows = np.nonzero((a == [216, 216, 215]).all(axis=2).any(axis=1))[0]    # 描边（1:1 贴图，颜色不变）
    assert len(rows) and p.area.y <= rows.min() and rows.max() < p.area.bottom


def test_area_covers_pill_on_first_line():
    # 胶囊在首行：area 的上沿要盖住胶囊伸出字顶的那一截
    img, pills = pill_scene(size=(400, 200), rects=(((300, 150, 371, 190), "now.md"),))
    seg = Segment(1, "a b", [OcrLine("first line here", Box(20, 60, 120, 16)), OcrLine("second", Box(20, 80, 120, 16))],
                  pills=(Pill("now.md", pills[0].box, None),))
    tr = "now.md 后面是很多很多很多很多很多的字"
    r = render(img, [seg], {1: tr}, "zh", pill_spans={1: ((0, 6, 0),)})
    p = r.placements[1]
    assert p.pills == 1 and p.lines[0].startswith("now.md") and len(p.lines) >= 2
    a = np.asarray(r.image).astype(int)
    rows = np.nonzero((a == [216, 216, 215]).all(axis=2).any(axis=1))[0]
    assert len(rows) and p.area.y <= rows.min() < p.area.y + 3 and rows.max() < p.area.bottom


# ---------- 换行归一 ----------

def test_normalize_uses_pill_edge_chars_on_left():
    assert normalize_translation(P0 + "\nab", "zh", {P0: "now.md"}) == P0 + " ab"
    assert normalize_translation(P0 + "\nab", "zh") == P0 + "ab"     # 对照：不告诉它胶囊文字，就按私用区字符算


def test_normalize_uses_pill_edge_chars_on_right():
    assert normalize_translation("ab\n" + P0, "zh", {P0: "now.md"}) == "ab " + P0
    assert normalize_translation("见\n" + P0, "zh", {P0: "now.md"}) == "见" + P0


# ---------- 接线：Session、run_bench ----------

def test_session_passes_pill_spans_to_render():
    from shotrans.session import Session
    img, pills = pill_scene()
    s = Session(img)
    s.segments = [seg_with(pills)]
    s.apply(s.generation, SegmentResult(1, "按 now.md 开始", "vllm", pill_spans=((2, 8, 0),)))
    assert s._rendered().placements[1].pills == 1
    assert s.translation_of(s.segments[0]) == "按 now.md 开始"     # 复制、历史照旧是纯文本


def test_session_ignores_spans_of_non_translation(monkeypatch):
    from shotrans.session import Session
    img, pills = pill_scene()
    s = Session(img)
    s.segments = [seg_with(pills)]
    seen = {}
    import shotrans.session as S
    real = S.render
    monkeypatch.setattr(S, "render", lambda *a, **k: seen.update(k) or real(*a, **k))
    s.apply(s.generation, SegmentResult(1, "press now.md to start", "vllm", "占位符丢失，显示原文", ((6, 12, 0),)))
    s._rendered()
    assert seen["pill_spans"] == {}


def test_run_bench_passes_pill_spans(tmp_path, monkeypatch):
    from tools import run_bench as rb

    class Router:
        def translate(self, segs, direction, on_result):
            on_result(SegmentResult(segs[0].idx, "你好", "vllm", pill_spans=((0, 1, 0),)))

    seen = {}
    real = rb.render
    monkeypatch.setattr(rb, "render", lambda *a, **k: seen.update(k) or real(*a, **k))
    monkeypatch.setattr(rb, "OUT", tmp_path)
    monkeypatch.setattr(rb, "BENCH", tmp_path)
    monkeypatch.setattr(rb, "recognize", lambda img: [OcrLine("Hello", Box(10, 10, 120, 24))])
    monkeypatch.setattr(rb, "_router", lambda: Router())
    rb.stage_full("96", Image.new("RGB", (300, 100), "white"))
    assert seen["pill_spans"] == {1: ((0, 1, 0),)}

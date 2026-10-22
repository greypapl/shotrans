"""Task 19 排字改动的专属测试：按字形估字号、全图归类、先延伸再缩、缩小下限、按字符回退字体、译文同原文不动。
每个守卫用自己的数据；期望值写死，不读被测模块的常量。"""
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from shotrans.models import Box, OcrLine, Segment
from shotrans.render import estimate_em, fit_text, has_glyph, line_span, render, room, start_sizes

SEGOE = r"C:\Windows\Fonts\segoeui.ttf"
ARIAL = r"C:\Windows\Fonts\arial.ttf"
MSYH = r"C:\Windows\Fonts\msyh.ttc"
WHITE = (255, 255, 255)


def _seg(idx, box, text="中文"):
    return Segment(idx, text, [OcrLine(text, box)])


# ---------- 按字形类别估字号 ----------

@pytest.mark.parametrize("text, span", [
    ("was some name", 0.60),          # 只有 x 高度
    ("Demos", 0.82),                  # 有上伸（大写）
    ("rate", 0.82),                   # 有上伸（t）
    ("öre", 0.82),                    # 带变音符的字母按上伸算
    ("among", 0.83),                  # 有下伸（g）
    ("Bibliographic Tools", 1.05),    # 上伸 + 下伸
    ("(was)", 1.05),                  # 括号上下都伸
    ("new course, arc", 0.72),        # 只有逗号这种浅下伸
    ("Code, Data, Media", 0.94),      # 上伸 + 逗号
    ("抑郁症 PHQ", 1.0526),            # 有汉字：方块字
])
def test_line_span_by_glyph_class(text, span):
    assert line_span(text) == pytest.approx(span, abs=1e-4)


def test_estimate_em_divides_box_height_by_span():
    assert estimate_em(OcrLine("Demos", Box(0, 0, 50, 41))) == pytest.approx(50.0)


@pytest.mark.parametrize("font", [SEGOE, ARIAL])
def test_same_size_words_get_same_estimate_through_real_ocr(font):
    # arXiv 快照的病例：「Demos」（无下伸）和「Bibliographic Tools」（有下伸）同为 13 号，估出的字号都在 13±1
    from shotrans.ocr import recognize

    f = ImageFont.truetype(font, 13)
    ems = []
    for text in ("Demos", "Bibliographic Tools"):
        img = Image.new("RGB", (int(f.getlength(text)) + 60, 80), WHITE)
        ImageDraw.Draw(img).text((30, 30), text, font=f, fill=(0, 0, 0))
        lines = recognize(img)
        assert len(lines) == 1
        ems.append(estimate_em(lines[0]))
    assert all(abs(e - 13) <= 1 for e in ems), ems


# ---------- 全图归类：相差 ≤ 12% 的同一字号 ----------

def _cjk_segs(heights):
    return [_seg(i, Box(10, 10 + 60 * i, 200, h), "中") for i, h in enumerate(heights, 1)]


def test_similar_sizes_share_one_start_px():
    # 汉字框高 20 → 19.0，22 → 20.9（是 19.0 的 1.10 倍）：同一类，都用该类中位数 19
    assert start_sizes(_cjk_segs([20, 22])) == {1: 19, 2: 19}


def test_size_class_tolerance_boundary():
    # 框高 23 → 21.85（是 19.0 的 1.15 倍）：另起一类
    assert start_sizes(_cjk_segs([20, 23])) == {1: 19, 2: 22}


def test_class_median_is_used_for_all_members():
    # 三段 19.0 / 19.95 / 20.9 一类，中位数 19.95 → 20
    assert start_sizes(_cjk_segs([20, 21, 22])) == {1: 20, 2: 20, 3: 20}


def test_formula_segments_do_not_join_size_classes():
    segs = _cjk_segs([20, 22])
    segs.append(Segment(3, "x = 1", [OcrLine("x = 1", Box(10, 300, 200, 21))], skip=True))
    assert start_sizes(segs) == {1: 19, 2: 19}


def test_render_uses_class_px_for_short_heading():
    # 「Demos」框高 10 → 12.2，「Bibliographic Tools」框高 14 → 13.3（各自取整是 12 和 13）：
    # 相差 9% 归成一类，两段起始字号都用该类中位数 12
    img = Image.new("RGB", (600, 100), WHITE)
    segs = [_seg(1, Box(10, 10, 120, 14), "Bibliographic Tools"), _seg(2, Box(300, 10, 44, 10), "Demos")]
    r = render(img, segs, {1: "书目工具", 2: "演示"}, "zh")
    assert r.placements[1].px == r.placements[2].px == 12


# ---------- 向空白处延伸 ----------

def test_room_stops_before_ink_and_keeps_gap():
    img = Image.new("RGB", (200, 100), WHITE)
    d = ImageDraw.Draw(img)
    d.line([(120, 10), (120, 29)], fill=(0, 0, 0))        # 右边一条竖线（图标、边框之类）
    d.point((80, 70), fill=(0, 0, 0))                       # 右下方一个墨点
    right, down_only, down_right = room(img, Box(10, 10, 50, 20), WHITE, [], 10)
    assert right == 110            # 竖线在 120，留 10 像素
    assert down_only == 90         # 原宽（10~60）下方没东西，到图底 100 留 10
    assert down_right == 60        # 加宽到 110 后碰到 (80,70) 的墨点，留 10


def test_room_stops_before_other_segment_box():
    # 别的段的框（哪怕还没画任何东西）也不能占；框外扩 PAD=2
    img = Image.new("RGB", (200, 100), WHITE)
    right, _, _ = room(img, Box(10, 10, 50, 20), WHITE, [Box(120, 15, 30, 10)], 10)
    assert right == 108


def test_room_never_smaller_than_box():
    img = Image.new("RGB", (100, 50), WHITE)
    ImageDraw.Draw(img).line([(61, 10), (61, 29)], fill=(0, 0, 0))
    assert room(img, Box(10, 10, 50, 20), WHITE, [], 10)[0] == 60


def test_single_line_extends_right_at_class_size():
    img = Image.new("RGB", (300, 60), WHITE)
    p = render(img, [_seg(1, Box(10, 10, 60, 20))], {1: "很长的一句译文"}, "zh").placements[1]
    assert p.px == 19 and not p.truncated and p.lines == ["很长的一句译文"]
    assert p.area.right > 70


def test_extension_does_not_cover_other_segment():
    img = Image.new("RGB", (300, 60), WHITE)
    segs = [_seg(1, Box(10, 10, 60, 20)), _seg(2, Box(150, 10, 60, 20), "其他")]
    p = render(img, segs, {1: "很长的一句译文"}, "zh").placements[1]
    # 右边到 148-10=138 放不下一行，折到下方空白，字号不缩
    assert p.px == 19 and p.lines == ["很长的一句译", "文"] and p.area.right <= 138


def test_extension_does_not_cover_unrecognised_ink():
    img = Image.new("RGB", (300, 60), WHITE)
    ImageDraw.Draw(img).rectangle([150, 12, 160, 28], fill=(0, 0, 0))   # OCR 没认出的图标
    r = render(img, [_seg(1, Box(10, 10, 60, 20))], {1: "很长的一句译文"}, "zh")
    p = r.placements[1]
    assert p.area.right <= 140
    assert (np.asarray(r.image)[12:29, 150:161] == 0).all()


def test_multi_line_extends_down_before_right():
    img = Image.new("RGB", (400, 120), WHITE)
    seg = Segment(1, "中文", [OcrLine("中文", Box(10, 10, 200, 20)), OcrLine("再来", Box(10, 34, 200, 20))])
    p = render(img, [seg], {1: "一二三四五六七八九十" * 3}, "zh").placements[1]
    # 19 号一行 10 个字，原框只放两行；向下延伸放三行，宽度不变
    assert p.px == 19 and len(p.lines) == 3 and p.area.right <= 210 and p.area.bottom > 54


def test_noisy_background_does_not_extend():
    # 框上方紧贴一条黑带：外圈 42% 不是底色 → 算杂乱。右边、下边其实是干净的白底，
    # 但杂乱底的段不延伸：只在原框 100×20 里排，缩到下限 14 号仍放不下就截断
    arr = np.full((120, 400, 3), 255, dtype=np.uint8)
    arr[17:20, 17:123] = 0
    p = render(Image.fromarray(arr, "RGB"), [_seg(1, Box(20, 20, 100, 20))], {1: "很长的一句译文呀"}, "zh").placements[1]
    assert p.truncated and len(p.lines) == 1 and p.area.right <= 120


def test_later_segment_does_not_extend_into_earlier_translation():
    # 段 1 折到自己下方的空白（x 100~140），段 2 在左下方向右延伸：要在段 1 已画的译文前停下
    img = Image.new("RGB", (150, 150), WHITE)
    segs = [_seg(1, Box(100, 10, 40, 20)), _seg(2, Box(10, 60, 40, 20), "再来")]
    r = render(img, segs, {1: "很长的译文很长", 2: "很长的译文很长"}, "zh")
    a, b = r.placements[1].area, r.placements[2].area
    assert a.bottom > 80                # 前提：段 1 的译文确实画到了段 2 那几行
    assert b.right < a.x


# ---------- 缩小下限 ----------

def test_fit_text_min_px_floor_truncates_instead_of_shrinking_further():
    text = "Hello world again"
    w13 = ImageFont.truetype(SEGOE, 13).getlength(text)
    assert w13 < ImageFont.truetype(SEGOE, 14).getlength(text)        # 前提：14 号放不下
    f = fit_text(text, SEGOE, int(w13) + 1, 20, 19, wrap=False, min_px=14)
    assert f.px == 14 and f.truncated and f.lines[0].endswith("…")
    f = fit_text(text, SEGOE, int(w13) + 1, 20, 19, wrap=False, min_px=13)
    assert f.px == 13 and not f.truncated


def test_render_floor_is_three_quarters_of_class_px():
    # 起始 19 号、框贴着图边无处延伸：下限 round(19×0.75) = 14
    img = Image.new("RGB", (70, 30), WHITE)
    p = render(img, [_seg(1, Box(10, 10, 60, 20))], {1: "很长的一句译文"}, "zh").placements[1]
    assert p.px == 14 and p.truncated


# ---------- 按字符回退字体 ----------

def test_has_glyph():
    assert has_glyph(SEGOE, "a") and has_glyph(SEGOE, " ")
    assert not has_glyph(SEGOE, "〉") and not has_glyph(SEGOE, "中")
    assert has_glyph(MSYH, "〉") and has_glyph(MSYH, "中")


def test_missing_glyph_drawn_with_fallback_font():
    # 40 号时 Segoe UI 的上沿（ascent 44）比微软雅黑（43）高 1 像素：后备片段要按主字体的基线对齐
    img = Image.new("RGB", (300, 80), WHITE)
    r = render(img, [_seg(1, Box(10, 10, 250, 42))], {1: "ab〉"}, "en")
    assert r.placements[1].px == 40
    se, yh = ImageFont.truetype(SEGOE, 40), ImageFont.truetype(MSYH, 40)

    def draw(anchor_baseline):
        im = Image.new("RGB", (300, 80), WHITE)
        d = ImageDraw.Draw(im)
        d.text((10, 10), "ab", font=se, fill=(0, 0, 0))
        if anchor_baseline:
            d.text((10 + se.getlength("ab"), 10 + se.getmetrics()[0]), "〉", font=yh, fill=(0, 0, 0), anchor="ls")
        else:
            d.text((10 + se.getlength("ab"), 10), "〉", font=yh, fill=(0, 0, 0))
        return np.asarray(im)

    tofu = Image.new("RGB", (300, 80), WHITE)
    ImageDraw.Draw(tofu).text((10, 10), "ab〉", font=se, fill=(0, 0, 0))
    want = draw(True)
    assert not (want == np.asarray(tofu)).all()          # 前提：和「□」画法不同
    assert not (want == draw(False)).all()               # 前提：按基线和按上沿对齐不同
    assert (np.asarray(r.image) == want).all()


def test_mixed_width_used_for_wrapping():
    # 「〉」在微软雅黑里约 1 个字宽；按 Segoe 的缺字框量宽会量错。框宽恰好放下混排宽度
    seg19, yh19 = ImageFont.truetype(SEGOE, 19), ImageFont.truetype(MSYH, 19)
    width = seg19.getlength("ab") + yh19.getlength("〉")
    assert width != seg19.getlength("ab〉")                           # 前提：两种量法不同
    f = fit_text("ab〉", SEGOE, int(np.ceil(width)), 20, 19, wrap=False)
    assert f.px == 19 and not f.truncated


# ---------- 译文和原文相同就不动原图 ----------

def _logo():
    img = Image.new("RGB", (200, 60), WHITE)
    ImageDraw.Draw(img).text((10, 10), "ar iv", font=ImageFont.truetype(SEGOE, 24), fill=(0, 0, 0))
    return img


def test_identical_translation_leaves_original_untouched():
    img = _logo()
    r = render(img, [_seg(1, Box(10, 10, 60, 30), "ar iv")], {1: " AR  IV "}, "zh")
    assert (np.asarray(r.image) == np.asarray(img)).all() and r.placements == {}


def test_different_translation_is_drawn():
    img = _logo()
    r = render(img, [_seg(1, Box(10, 10, 60, 30), "ar iv")], {1: "arxiv"}, "zh")
    assert 1 in r.placements and not (np.asarray(r.image) == np.asarray(img)).all()


def test_keep_identical_false_redraws():
    img = _logo()
    r = render(img, [_seg(1, Box(10, 10, 60, 30), "ar iv")], {1: "ar iv"}, "zh", keep_identical=False)
    assert 1 in r.placements

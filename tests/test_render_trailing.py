"""Task 20：抹原文时连带框右边 OCR 没认出的一小串墨迹（行末「…”。」）；取底色时不算行内代码胶囊。
每个守卫用自己的合成图；期望值写死。行高 14：空白超过 0.6×14 ≈ 8 像素算断开，整串最多 3×14 = 42 像素。"""
import numpy as np
from PIL import Image, ImageDraw

from shotrans.models import Box, OcrLine, Segment
from shotrans.render import render, sample_bg

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BOX = Box(20, 40, 100, 14)    # 右边界 120


def _seg(idx=1, box=BOX, text="abc"):
    return Segment(idx, text, [OcrLine(text, box)])


def _render(img, segs=None):
    return render(img, segs or [_seg()], {1: "z"}, "en").image


def _blob(img, x0, x1, y0=43, y1=50):
    ImageDraw.Draw(img).rectangle([x0, y0, x1, y1], fill=BLACK)


def test_trailing_glyphs_right_after_the_box_are_erased():
    img = Image.new("RGB", (300, 100), WHITE)
    _blob(img, 125, 128)            # 离框 5 像素
    _blob(img, 134, 137)            # 离上一点 5 像素：同一串
    out = _render(img)
    assert out.getpixel((126, 46)) == WHITE and out.getpixel((135, 46)) == WHITE


def test_ink_after_a_wide_gap_is_kept():
    img = Image.new("RGB", (300, 100), WHITE)
    _blob(img, 131, 134)            # 框右边 120 到 131 之间空了 11 像素（> 8）
    assert _render(img).getpixel((132, 46)) == BLACK


def test_long_ink_run_is_kept():
    # 从框边一直连到 170（50 像素 > 42）：图标、图片，不是漏认的几个字
    img = Image.new("RGB", (300, 100), WHITE)
    _blob(img, 123, 172)
    assert _render(img).getpixel((125, 46)) == BLACK


def test_ink_running_into_another_segment_is_kept():
    img = Image.new("RGB", (300, 100), WHITE)
    _blob(img, 124, 140)
    other = _seg(2, Box(136, 40, 60, 14), "next")
    out = render(img, [_seg(), other], {1: "z"}, "en").image
    assert out.getpixel((126, 46)) == BLACK


def test_a_few_stray_pixels_are_not_a_glyph():
    img = Image.new("RGB", (300, 100), WHITE)
    for x in (124, 125, 126):        # 3 个像素：噪点
        img.putpixel((x, 46), BLACK)
    assert _render(img).getpixel((125, 46)) == BLACK
    img = Image.new("RGB", (300, 100), WHITE)
    for x in (124, 125, 126, 127):   # 4 个像素：算字
        img.putpixel((x, 46), BLACK)
    assert _render(img).getpixel((125, 46)) == WHITE


def test_ink_reaching_the_image_edge_counts_as_ended():
    img = Image.new("RGB", (140, 100), WHITE)
    _blob(img, 125, 139)             # 一直到图片右边缘（还没有够宽的空白，但图片到头了）
    assert _render(img).getpixel((130, 46)) == WHITE


def test_noisy_background_does_not_extend():
    # 框外一圈（3 像素）每 5 列有 2 列黑：中位色仍是白，但同色只占 60%，算底色杂乱——只抹框，不碰框右边的墨迹
    img = Image.new("RGB", (300, 100), WHITE)
    for y in range(37, 57):
        for x in range(17, 123):
            if not (20 <= x < 120 and 40 <= y < 54) and x % 5 < 2:
                img.putpixel((x, y), BLACK)
    _blob(img, 125, 128)
    assert _render(img).getpixel((126, 46)) == BLACK


def _text_seg_image():
    """框里有黑字（几条竖线），这一段的文字颜色是黑色。"""
    img = Image.new("RGB", (300, 100), WHITE)
    d = ImageDraw.Draw(img)
    for x in range(30, 110, 6):
        d.line([(x, 42), (x, 51)], fill=BLACK, width=2)
    return img


def test_coloured_emoji_after_text_is_kept_pixel_exact():
    # 「好的👍」：框右边 4 像素处一个黄色圆块（表情）——颜色和文字不同，不抹，一个像素都不动
    img = _text_seg_image()
    ImageDraw.Draw(img).ellipse([124, 41, 135, 52], fill=(255, 200, 40), outline=(200, 120, 20))
    before = img.crop((122, 38, 140, 56)).tobytes()
    assert _render(img).crop((122, 38, 140, 56)).tobytes() == before


def test_grey_ticks_after_dark_text_are_kept_pixel_exact():
    # 灰色 ✓✓（150 灰），文字是黑的：不是这段的字，不抹
    img = _text_seg_image()
    d = ImageDraw.Draw(img)
    for x0 in (124, 130):
        d.line([(x0, 47), (x0 + 2, 50), (x0 + 6, 43)], fill=(150, 150, 150), width=2)
    before = img.crop((122, 38, 140, 56)).tobytes()
    assert _render(img).crop((122, 38, 140, 56)).tobytes() == before


def test_saturated_dark_icon_after_grey_text_is_kept():
    # 文字是深灰，后面的图标是深蓝（通道差 ≤ 80，但它是彩色的）：不抹
    img = Image.new("RGB", (300, 100), WHITE)
    d = ImageDraw.Draw(img)
    for x in range(30, 110, 6):
        d.line([(x, 42), (x, 51)], fill=(60, 60, 60), width=2)
    d.rectangle([124, 43, 130, 50], fill=(20, 40, 130))
    assert _render(img).getpixel((127, 46)) == (20, 40, 130)


def test_same_colour_trailing_glyphs_then_emoji_only_text_is_erased():
    # 「…”。」同色（黑）先抹，紧跟着的黄色表情停下、留着
    img = _text_seg_image()
    d = ImageDraw.Draw(img)
    d.rectangle([123, 48, 125, 50], fill=BLACK)          # 「。」
    d.rectangle([129, 42, 131, 45], fill=BLACK)          # 「”」
    d.ellipse([136, 41, 147, 52], fill=(255, 200, 40))   # 表情
    out = _render(img)
    assert out.getpixel((124, 49)) == WHITE and out.getpixel((130, 43)) == WHITE
    assert out.getpixel((141, 46)) == (255, 200, 40)


def test_small_antialiased_comma_counts_as_text_colour():
    # bench 05 的小逗号：8 个像素大半是抗锯齿的灰，只有一个字芯是黑的——看字芯，算这段的字，抹掉
    img = _text_seg_image()
    for (dx, dy), v in {(0, 0): 213, (1, 0): 172, (0, 1): 111, (1, 1): 0, (2, 1): 204,
                        (1, 2): 152, (0, 3): 219, (1, 3): 178}.items():
        img.putpixel((124 + dx, 47 + dy), (v, v, v))
    out = _render(img)
    assert out.getpixel((125, 48)) == WHITE and out.getpixel((124, 48)) == WHITE


# ---- Task 21：语法高亮代码行末的彩色符号 ----
BLUE, RED, GREEN = (30, 60, 220), (200, 30, 30), (20, 160, 60)


def _code_line(colours):
    """框里几个「词」（每个 3 根竖线、词间空 6 像素），按 colours 依次上色：模拟语法高亮的一行代码。"""
    img = Image.new("RGB", (300, 100), WHITE)
    d = ImageDraw.Draw(img)
    x = 24
    for c in colours:
        for k in range(3):
            d.line([(x + 3 * k, 42), (x + 3 * k, 51)], fill=c, width=2)
        x += 15
    return img


def test_highlighted_code_trailing_glyph_in_one_of_its_colours_is_erased():
    # 段内有黑、蓝、红三种字芯颜色：行末红色的「;」是这段代码的字，抹掉
    img = _code_line([BLACK, BLUE, BLACK, RED, BLACK, BLUE])
    ImageDraw.Draw(img).rectangle([124, 44, 126, 51], fill=RED)
    assert _render(img).getpixel((125, 47)) == WHITE


def test_highlighted_code_trailing_glyphs_in_several_of_its_colours_are_erased():
    # 行末先黑「)」、再蓝、再红：每个都是段内用过的颜色，全抹
    img = _code_line([BLACK, BLUE, BLACK, RED, BLACK, BLUE])
    d = ImageDraw.Draw(img)
    d.rectangle([123, 44, 124, 51], fill=BLACK)
    d.rectangle([128, 44, 129, 51], fill=BLUE)
    d.rectangle([133, 44, 134, 51], fill=RED)
    out = _render(img)
    assert [out.getpixel((x, 47)) for x in (123, 128, 133)] == [WHITE, WHITE, WHITE]


def test_highlighted_code_trailing_glyph_in_another_colour_is_kept():
    # 同一行代码，行末是绿色的图标——段内没有绿色：留着
    img = _code_line([BLACK, BLUE, BLACK, RED, BLACK, BLUE])
    ImageDraw.Draw(img).rectangle([124, 44, 126, 51], fill=GREEN)
    assert _render(img).getpixel((125, 47)) == GREEN


def test_single_colour_text_trailing_red_glyph_still_kept():
    # 普通黑字（只有一种颜色）：行末红色的东西照旧留着（红点、图标）
    img = _code_line([BLACK] * 6)
    ImageDraw.Draw(img).rectangle([124, 44, 126, 51], fill=RED)
    assert _render(img).getpixel((125, 47)) == RED


def test_tiny_coloured_speck_in_text_does_not_make_it_highlighted():
    # 黑字带蓝链接，另有一个 2×2 的红点（4 个墨迹像素 < 30）：红点不算一种字色，彩色只有蓝一种，行末的红色东西照旧留着
    img = _code_line([BLACK, BLUE, BLACK, BLACK, BLUE, BLACK])
    d = ImageDraw.Draw(img)
    d.rectangle([110, 47, 111, 48], fill=RED)
    d.rectangle([124, 44, 126, 51], fill=RED)
    assert _render(img).getpixel((125, 47)) == RED


def test_grey_timestamp_then_grey_ticks_kept():
    # 评审 I4：黑字 + 框内灰色时间戳（Telegram 气泡「消息 12:30 ✓✓」），行末灰色 ✓✓ 仍是图标，留着
    grey = (140, 140, 140)
    img = _code_line([BLACK, BLACK, BLACK, BLACK, grey, grey])
    ImageDraw.Draw(img).rectangle([124, 46, 126, 50], fill=grey)
    assert _render(img).getpixel((125, 48)) == grey


def test_blue_link_then_blue_icon_kept():
    # 评审 I4：黑字里一个蓝链接（只有一种彩色），行末蓝色外链图标留着
    img = _code_line([BLACK, BLACK, BLUE, BLUE, BLACK, BLACK])
    ImageDraw.Draw(img).rectangle([124, 44, 128, 50], fill=BLUE)
    assert _render(img).getpixel((126, 47)) == BLUE


def test_palette_not_computed_without_off_colour_trailing_glyph(monkeypatch):
    # 评审 I5：行末没有异色字形时不扫色板（整图重画每段都扫，大图慢 4 倍）
    import shotrans.render as R
    calls = []
    real = R.ink_palette
    monkeypatch.setattr(R, "ink_palette", lambda *a, **k: calls.append(1) or real(*a, **k))
    img = _code_line([BLACK] * 6)
    ImageDraw.Draw(img).rectangle([124, 44, 126, 51], fill=BLACK)
    out = _render(img)
    assert out.getpixel((125, 47)) == WHITE and calls == []


def test_palette_computed_once_per_segment(monkeypatch):
    # 一段两行、两行行末都有异色字形：色板只扫一次
    import shotrans.render as R
    calls = []
    real = R.ink_palette
    monkeypatch.setattr(R, "ink_palette", lambda *a, **k: calls.append(1) or real(*a, **k))
    img = _code_line([BLACK, BLUE, BLACK, RED, BLACK, BLUE])
    d = ImageDraw.Draw(img)
    for y0 in (42, 62):
        x = 24
        for c in [BLACK, BLUE, BLACK, RED, BLACK, BLUE]:
            for k in range(3):
                d.line([(x + 3 * k, y0), (x + 3 * k, y0 + 9)], fill=c, width=2)
            x += 15
        d.rectangle([124, y0 + 2, 126, y0 + 9], fill=RED)
    seg = Segment(1, "ab", [OcrLine("a", Box(20, 40, 100, 14)), OcrLine("b", Box(20, 60, 100, 14))])
    out = render(img, [seg], {1: "z"}, "en").image
    assert out.getpixel((125, 47)) == WHITE and out.getpixel((125, 67)) == WHITE
    assert calls == [1]


def test_ink_palette_groups_close_colours():
    # 黑和深灰（差 40 ≤ 80）归一类；蓝单独一类
    from shotrans.render import ink_palette
    img = _code_line([BLACK, (40, 40, 40), BLUE, BLUE])
    pal = ink_palette(np.asarray(img), [BOX], WHITE)
    assert len(pal) == 2


def test_sample_bg_ignores_pill_fill_in_the_ring():
    # 胶囊占了外圈大半：不排除就会把胶囊的浅灰当成段落底色
    img = Image.new("RGB", (300, 100), WHITE)
    ImageDraw.Draw(img).rectangle([30, 36, 130, 57], fill=(240, 240, 239))
    arr = np.asarray(img)
    box = Box(20, 40, 120, 14)
    assert sample_bg(arr, box)[0] == (240, 240, 239)
    assert sample_bg(arr, box, [Box(30, 36, 101, 22)])[0] == WHITE

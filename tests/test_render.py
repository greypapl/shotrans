import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from shotrans.models import Box, OcrLine, Segment
from shotrans.render import (
    contrast_ratio, ensure_contrast, fit_text, render, sample_bg, sample_fg, wrap_text,
)

SEGOE = r"C:\Windows\Fonts\segoeui.ttf"
MSYH = r"C:\Windows\Fonts\msyh.ttc"


def _img_with_text(bg, fg, text="Hello world", size=(400, 80)):
    img = Image.new("RGB", size, bg)
    ImageDraw.Draw(img).text((20, 20), text, font=ImageFont.truetype(SEGOE, 24), fill=fg)
    return img


def _seg():
    return Segment(1, "Hello world", [OcrLine("Hello world", Box(20, 20, 200, 30))])


def _gradient(w=400, h=120):
    row = np.linspace(0, 255, w).astype(np.uint8)
    arr = np.stack([np.tile(row, (h, 1))] * 3, axis=-1)
    return Image.fromarray(arr, "RGB")


def test_contrast_ratio_known_values():
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21, rel=1e-3)
    assert contrast_ratio((90, 90, 90), (90, 90, 90)) == pytest.approx(1)


def test_ensure_contrast_keeps_good_fg():
    assert ensure_contrast((0, 0, 0), (255, 255, 255)) == (0, 0, 0)


def test_ensure_contrast_fixes_light_on_white():
    assert ensure_contrast((200, 200, 200), (255, 255, 255)) == (0, 0, 0)


def test_ensure_contrast_fixes_dark_on_dark():
    assert ensure_contrast((60, 60, 60), (30, 30, 30)) == (255, 255, 255)


def test_sample_bg_white_page():
    arr = np.asarray(_img_with_text((255, 255, 255), (0, 0, 0)))
    bg, noisy = sample_bg(arr, Box(20, 20, 200, 30))
    assert bg == (255, 255, 255) and noisy is False


def test_sample_bg_gradient_is_noisy():
    _, noisy = sample_bg(np.asarray(_gradient()), Box(100, 20, 200, 30))
    assert noisy is True


def test_sample_fg_dark_theme():
    arr = np.asarray(_img_with_text((30, 30, 30), (240, 240, 240)))
    fg = sample_fg(arr, Box(20, 20, 200, 30), (30, 30, 30))
    assert min(fg) >= 150


def test_wrap_text_respects_width():
    font = ImageFont.truetype(SEGOE, 16)
    text = "the quick brown fox jumps over the lazy dog"
    lines = wrap_text(text, font, 100)
    assert len(lines) > 1
    assert all(font.getlength(l) <= 100 for l in lines)
    assert " ".join(lines) == text


def test_wrap_text_chinese_by_char():
    font = ImageFont.truetype(MSYH, 16)
    lines = wrap_text("抑郁症的早期识别非常重要", font, 50)
    assert len(lines) > 1 and "".join(lines) == "抑郁症的早期识别非常重要"


def test_wrap_hard_splits_long_word():
    font = ImageFont.truetype(SEGOE, 16)
    lines = wrap_text("a" * 200, font, 50)
    assert all(font.getlength(l) <= 50 for l in lines)
    assert "".join(lines) == "a" * 200


def test_fit_shrinks_before_truncating():
    f = fit_text("Hello world again", SEGOE, 120, 24, 30, wrap=False)
    assert 9 <= f.px < 30 and not f.truncated
    assert ImageFont.truetype(SEGOE, f.px).getlength(f.lines[0]) <= 120


def test_long_text_truncated_in_small_box():
    f = fit_text("很长" * 200, MSYH, 100, 20, 16, wrap=True)
    assert f.truncated and f.px == 9
    assert f.lines[-1].endswith("…")


def test_render_erases_original_ink():
    img = _img_with_text((255, 255, 255), (0, 0, 0))
    r = render(img, [_seg()], {1: "你好"}, "zh")
    out, orig = np.asarray(r.image), np.asarray(img)
    p = r.placements[1]
    x0 = 20 + int(ImageFont.truetype(MSYH, p.px).getlength("你好")) + 3
    x1 = 20 + int(ImageFont.truetype(SEGOE, 24).getlength("Hello world")) - 2
    assert x1 > x0 + 10                      # 前提：原文比译文宽
    assert orig[20:50, x0:x1].min() < 100    # 原来这里有黑字
    assert out[20:50, x0:x1].min() > 245     # 现在抹成了白底


def test_render_leaves_outside_untouched():
    img = _img_with_text((255, 255, 255), (0, 0, 0))
    out, orig = np.asarray(render(img, [_seg()], {1: "你好"}, "zh").image), np.asarray(img)
    assert (out[:, 230:] == orig[:, 230:]).all()
    assert (out[:15] == orig[:15]).all()


def test_render_placement_colors_and_contrast():
    p = render(_img_with_text((255, 255, 255), (0, 0, 0)), [_seg()], {1: "你好"}, "zh").placements[1]
    assert p.bg == (255, 255, 255)
    assert contrast_ratio(p.fg, p.bg) >= 4.5
    assert not p.truncated


def test_render_dark_theme_light_text():
    p = render(_img_with_text((30, 30, 30), (240, 240, 240)), [_seg()], {1: "你好"}, "zh").placements[1]
    assert min(p.fg) >= 150


def test_skip_and_missing_translation_untouched():
    img = _img_with_text((255, 255, 255), (0, 0, 0))
    skip = Segment(1, "x = 1", [OcrLine("x = 1", Box(20, 20, 200, 30))], skip=True)
    r1 = render(img, [skip], {1: "不该画"}, "zh")
    r2 = render(img, [_seg()], {}, "zh")
    assert (np.asarray(r1.image) == np.asarray(img)).all() and r1.placements == {}
    assert (np.asarray(r2.image) == np.asarray(img)).all() and r2.placements == {}


def test_noisy_background_fills_whole_segment_box():
    img = _gradient()
    seg = Segment(1, "a b", [OcrLine("a", Box(100, 20, 200, 20)), OcrLine("b", Box(100, 44, 80, 20))])
    r = render(img, [seg], {1: "好"}, "zh")
    p = r.placements[1]
    # (290, 60) 在整段框里、但不在任何一行的框里：只有「整块铺色」才会被填
    assert tuple(np.asarray(r.image)[60, 290]) == p.bg

import threading
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from shotrans.models import Box, OcrLine, OcrWord
from shotrans.ocr import (
    _to_lines, available_languages, cjk_ratio, join_words, pick_result, recognize, upscale_factor,
)

SEGOE = r"C:\Windows\Fonts\segoeui.ttf"
MSYH = r"C:\Windows\Fonts\msyh.ttc"


def _text_image(text, font_path, px, size=(900, 120)):
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((20, 30), text, font=ImageFont.truetype(font_path, px), fill="black")
    return img


def test_join_words_spaces_only_between_latin():
    assert join_words(["Hello", "world"]) == "Hello world"
    assert join_words(["抑", "郁", "症"]) == "抑郁症"
    assert join_words(["使用", "PHQ-8", "量表"]) == "使用PHQ-8量表"
    assert join_words(["PHQ-8", "score"]) == "PHQ-8 score"
    assert join_words(["", "a"]) == "a"


def test_join_words_skips_empty_word_in_middle():
    assert join_words(["a", "", "b"]) == "a b"


def _fake_result(*lines):
    """lines: 每行是 [(text, x, y, w, h), ...]，模仿 pywinrt 的 OcrResult。"""
    def word(t, x, y, w, h):
        return SimpleNamespace(
            text=t, bounding_rect=SimpleNamespace(x=x, y=y, width=w, height=h))
    return SimpleNamespace(
        lines=[SimpleNamespace(words=[word(*w) for w in ws]) for ws in lines])


def test_to_lines_scale_2_halves_all_coordinates():
    res = _fake_result([("Hello", 40, 20, 100, 30), ("world", 160, 24, 80, 40)])
    out = _to_lines(res, 2)
    assert out == [OcrLine("Hello world", Box(20, 10, 100, 22))]


def test_to_lines_skips_line_without_words():
    res = _fake_result([], [("ok", 10, 10, 20, 20)])
    out = _to_lines(res, 1)
    assert out == [OcrLine("ok", Box(10, 10, 20, 20))]


def test_to_lines_clamps_tiny_size_to_one():
    res = _fake_result([("i", 10, 10, 1, 1)])
    out = _to_lines(res, 2)
    assert out == [OcrLine("i", Box(5, 5, 1, 1))]


def test_to_lines_keeps_scaled_word_boxes():
    # Task 20：词级框也按放大倍数换算回原图；空词跳过（和拼行一致）
    res = _fake_result([("Hello", 40, 20, 100, 30), ("", 150, 20, 4, 30), ("world", 160, 24, 80, 40)])
    (line,) = _to_lines(res, 2)
    assert [(w.text, w.box) for w in line.words] == [("Hello", Box(20, 10, 50, 15)), ("world", Box(80, 12, 40, 20))]


def test_words_do_not_affect_line_equality():
    # 有没有词级框不改变「这是同一行」：旧测试、旧快照手写的行照样相等
    words = (OcrWord("ok", Box(10, 10, 20, 20)),)
    assert OcrLine("ok", Box(10, 10, 20, 20), words) == OcrLine("ok", Box(10, 10, 20, 20))
    assert OcrLine("ok", Box(10, 10, 20, 20)).words == ()


def test_cjk_ratio():
    assert cjk_ratio("抑郁abcd") == pytest.approx(2 / 6)
    assert cjk_ratio("123 ...") == 0.0


def test_pick_result_prefers_zh_when_chinese():
    en = [OcrLine("garbage", Box(0, 0, 1, 1))]
    zh = [OcrLine("抑郁症的识别", Box(0, 0, 1, 1))]
    assert pick_result(en, zh) is zh


def test_pick_result_prefers_en_when_english():
    en = [OcrLine("The quick brown fox", Box(0, 0, 1, 1))]
    zh = [OcrLine("The quick brown 狐", Box(0, 0, 1, 1))]
    assert pick_result(en, zh) is en


def test_upscale_factor():
    assert upscale_factor(800, 600, 10000) == 2
    assert upscale_factor(2560, 1440, 10000) == 1
    assert upscale_factor(1200, 800, 2000) == 1  # 放大后超过引擎上限就不放大


def test_languages_installed():
    langs = available_languages()
    assert "en-US" in langs
    assert "zh-Hans-CN" in langs


def test_recognize_english_real_engine():
    lines = recognize(_text_image("The quick brown fox jumps over the lazy dog", SEGOE, 28))
    text = " ".join(l.text for l in lines)
    assert "quick brown fox" in text
    assert "quick" in [w.text for w in lines[0].words]           # 真引擎也给词级框
    assert all(lines[0].box.x <= w.box.x and w.box.right <= lines[0].box.right + 1 for w in lines[0].words)
    b = lines[0].box
    assert 10 <= b.x <= 40      # 坐标已经从放大图换算回原图
    assert 25 <= b.y <= 60


def test_recognize_chinese_real_engine():
    lines = recognize(_text_image("抑郁症的早期识别非常重要", MSYH, 32))
    assert "抑郁症" in "".join(l.text for l in lines)


def test_recognize_works_in_worker_thread():
    box = {}

    def run():
        box["lines"] = recognize(_text_image("Hello world", SEGOE, 28))

    t = threading.Thread(target=run)
    t.start()
    t.join(30)
    assert "Hello" in " ".join(l.text for l in box["lines"])

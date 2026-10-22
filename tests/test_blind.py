import random

from tools.blind import assign_sides, score


def test_assign_sides_is_random_but_complete():
    key = assign_sides(["01", "02", "03", "04"], random.Random(0))
    assert set(key) == {"01", "02", "03", "04"}
    for sides in key.values():
        assert sorted(sides.values()) == ["ours", "youdao"]
        assert set(sides) == {"甲", "乙"}
    assert len({k["甲"] for k in key.values()}) == 2  # 种子 0 下两种摆法都出现


def test_score_counts_not_worse():
    key = {"01": {"甲": "ours", "乙": "youdao"},
           "02": {"甲": "youdao", "乙": "ours"},
           "03": {"甲": "ours", "乙": "youdao"}}
    answers = {"01": "甲", "02": "甲", "03": "平"}
    assert score(key, answers) == (2, 3)   # 01 赢、03 平、02 输


def test_score_ignores_unanswered():
    key = {"01": {"甲": "ours", "乙": "youdao"}}
    assert score(key, {}) == (0, 0)


# ---- 公平裁剪 ----
import numpy as np
from PIL import Image, ImageDraw

from tools.blind import MARGIN, fair_crop_box, residual_red, selection_box

RED = (240, 20, 20)


def _frame(size, box, handles=True, width=2, bottom=True):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    d.line([(x0, y0), (x1, y0)], fill=RED, width=width)
    d.line([(x0, y0), (x0, y1)], fill=RED, width=width)
    d.line([(x1, y0), (x1, y1)], fill=RED, width=width)
    if bottom:
        d.line([(x0, y1), (x1, y1)], fill=RED, width=width)
    if handles:
        xm, ym = (x0 + x1) // 2, (y0 + y1) // 2
        for cx, cy in [(x0, y0), (x1, y0), (x0, y1), (x1, y1), (xm, y0), (xm, y1), (x0, ym), (x1, ym)]:
            d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill="white", outline=RED, width=2)
    return img


def _has_red(img):
    a = np.asarray(img.convert("RGB"))
    return bool(((a[..., 0] >= 200) & (a[..., 1] <= 90) & (a[..., 2] <= 90)).any())


def test_full_frame_with_handles_is_found_and_cropped_clean():
    img = _frame((400, 300), (40, 30, 360, 270))
    x0, y0, x1, y1 = selection_box(img)
    assert abs(x0 - 42) <= 1 and abs(y0 - 32) <= 1 and abs(x1 - 360) <= 1 and abs(y1 - 270) <= 1
    crop = img.crop(fair_crop_box(img))
    assert not _has_red(crop)
    assert not residual_red(crop)


def test_no_border_means_whole_image():
    img = Image.new("RGB", (200, 150), "white")
    assert selection_box(img) is None
    assert fair_crop_box(img) == (0, 0, 200, 150)


def test_selection_over_top_part_stops_at_bottom_border():
    img = _frame((300, 800), (10, 5, 290, 670))
    ImageDraw.Draw(img).rectangle([0, 700, 300, 760], fill=(200, 200, 200))  # 工具栏之类
    box = selection_box(img)
    assert abs(box[3] - 670) <= 1
    crop_box = fair_crop_box(img)
    assert crop_box[3] == box[3] - MARGIN
    assert not _has_red(img.crop(crop_box))


def test_red_text_inside_selection_is_not_a_border():
    img = _frame((400, 300), (40, 30, 360, 270), handles=False)
    d = ImageDraw.Draw(img)
    for k in range(20):                      # 一串小红字笔画：短竖线、间隔排开
        d.line([(80 + k * 10, 100), (80 + k * 10, 125)], fill=RED, width=2)
    d.rectangle([80, 150, 95, 160], fill=RED)  # 小红块
    box = selection_box(img)
    assert box[:2] == (42, 32) or (abs(box[0] - 42) <= 1 and abs(box[1] - 32) <= 1)
    assert abs(box[2] - 360) <= 1 and abs(box[3] - 270) <= 1


def test_red_text_only_no_border():
    img = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(img)
    for k in range(25):
        d.line([(50 + k * 12, 100), (50 + k * 12, 130)], fill=RED, width=2)
    assert selection_box(img) is None


def test_border_on_image_edge_is_trimmed():
    img = _frame((300, 200), (0, 0, 299, 199), handles=False)
    crop_box = fair_crop_box(img)
    assert not _has_red(img.crop(crop_box))


def test_selection_covering_width_but_missing_bottom_edge():
    img = _frame((300, 500), (20, 10, 280, 400), handles=False, bottom=False)
    box = selection_box(img)
    assert box[3] == 500           # 没有下边线 -> 一直到图底
    assert not _has_red(img.crop(fair_crop_box(img)))


def test_margin_removes_handles_but_less_does_not():
    # 手柄向框内伸出约 5px，MARGIN 必须盖得住
    assert MARGIN >= 7


def test_short_red_bar_outside_selection_is_not_a_border():
    img = _frame((400, 400), (20, 10, 380, 250), handles=False)
    ImageDraw.Draw(img).line([(100, 330), (170, 330)], fill=RED, width=2)  # 框外一条 70px 红线
    box = selection_box(img)
    assert abs(box[3] - 250) <= 1


def test_handle_remnants_at_image_edge_without_border_are_trimmed():
    img = Image.new("RGB", (300, 120), "white")
    d = ImageDraw.Draw(img)
    pink = (252, 120, 110)
    for cx, cy in [(299, 60), (150, 0), (150, 119), (0, 60)]:   # 选框在图外，只露出手柄半边
        d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], outline=pink, width=2)
    assert selection_box(img) is None
    crop = img.crop(fair_crop_box(img))
    assert crop.size == (300 - 2 * MARGIN, 120 - 2 * MARGIN)
    assert not residual_red(crop)

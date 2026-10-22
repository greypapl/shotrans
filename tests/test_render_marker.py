"""「显示的是原文」角标（规格 §5.3：在该段角落标出提示）。"""
import numpy as np
from PIL import Image

from shotrans.models import Box, OcrLine, Segment
from shotrans.render import render

WHITE = (255, 255, 255)
AMBER = (245, 158, 11)


def _seg(box, idx=1):
    return Segment(idx, "x", [OcrLine("x", box)])


def test_marked_segment_gets_6px_triangle_at_top_right():
    img = Image.new("RGB", (200, 100), WHITE)
    r = render(img, [_seg(Box(20, 30, 100, 20))], {}, "zh", marked={1})
    out = r.image
    # 框右边界 x=120（不含），上边 y=30：三角形顶点 (114,30) (119,30) (119,35)
    for p in [(119, 30), (114, 30), (119, 35), (117, 31)]:
        assert out.getpixel(p) == AMBER, p
    for p in [(113, 30), (119, 36), (120, 30), (119, 29), (115, 34)]:
        assert out.getpixel(p) == WHITE, p
    changed = (np.asarray(out) != np.asarray(img)).any(axis=2)
    assert changed.sum() == 21  # 边长 6 的直角三角形：1+2+…+6 个像素，别处一个不动
    assert r.placements == {}


def test_default_no_marker():
    img = Image.new("RGB", (200, 100), WHITE)
    r = render(img, [_seg(Box(20, 30, 100, 20))], {}, "zh")
    assert (np.asarray(r.image) == np.asarray(img)).all()


def test_only_marked_idx_gets_marker():
    img = Image.new("RGB", (200, 100), WHITE)
    r = render(img, [_seg(Box(20, 10, 100, 20), 1), _seg(Box(20, 60, 100, 20), 2)], {}, "zh", marked={2})
    assert r.image.getpixel((119, 10)) == WHITE
    assert r.image.getpixel((119, 60)) == AMBER


def test_marker_clipped_at_image_edge_does_not_crash():
    img = Image.new("RGB", (100, 50), WHITE)
    r = render(img, [_seg(Box(60, 0, 60, 20))], {}, "zh", marked={1})  # 框伸出图片右边
    assert r.image.getpixel((99, 0)) == AMBER
    assert r.image.size == (100, 50)


def test_marker_drawn_on_top_of_neighbour_translation():
    """角标在所有译文画完之后再画：不会被后面一段的抹底色盖掉。"""
    img = Image.new("RGB", (200, 100), WHITE)
    segs = [_seg(Box(20, 30, 100, 20), 1), _seg(Box(20, 28, 120, 30), 2)]
    r = render(img, segs, {2: "你好"}, "zh", marked={1})
    assert r.image.getpixel((119, 30)) == AMBER

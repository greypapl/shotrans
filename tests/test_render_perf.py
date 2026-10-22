"""排字的速度守卫（Task 19 修正）：render 在界面线程上、每来一段译文就重画一次，必须快。
- 整体：2560×1440、150 个单行段，中英两种目标都 < 2 秒（改前约 7~10 秒，改后约 0.3 秒；阈值放宽免得机器忙时误报）；
- 各项优化各有一个「数次数/量面积」的确定性测试（不看时间）：空白扫描只扫用得上的范围、碰到障碍就停、
  原框放得下就不扫；折行每行只量几次；明显超高的字号不折行；整串都在主字体里时不逐字查字形。
- 折行和缩字号的结果与逐个量的写法逐一相同（参考实现写在测试里）。"""
import random
import time

import pytest
from PIL import Image, ImageDraw, ImageFont

import shotrans.render as R
from shotrans.models import Box, OcrLine, Segment
from shotrans.render import MixedFont, fit_text, render, wrap_text

SEGOE = r"C:\Windows\Fonts\segoeui.ttf"
MSYH = r"C:\Windows\Fonts\msyh.ttc"
WHITE = (255, 255, 255)


def _grid(n):
    segs = []
    for i in range(n):
        x, y = 20 + (i % 4) * 620, 20 + (i // 4) * 30
        segs.append(Segment(i + 1, "Some English words", [OcrLine("Some English words", Box(x, y, 300, 16))]))
    return segs


@pytest.mark.parametrize("target, text", [
    ("zh", "这是一段比较长的中文译文，用来测试排字速度"),
    ("en", "This is a rather long English translation used to time layout"),
])
def test_render_150_segments_on_large_image_is_fast(target, text):
    img = Image.new("RGB", (2560, 1440), WHITE)
    segs = _grid(150)
    render(img, segs[:2], {1: text, 2: text}, target)          # 预热：字体加载、字形缓存
    t = time.perf_counter()
    r = render(img, segs, {s.idx: text for s in segs}, target)
    assert time.perf_counter() - t < 2.0
    assert len(r.placements) == 150


# ---------- 空白扫描只扫用得上的范围 ----------

def _count_scanned(monkeypatch):
    area = []
    real = R._dirty

    def spy(img, x0, y0, x1, y1, bg, others):
        area.append((x1 - x0) * (y1 - y0))
        return real(img, x0, y0, x1, y1, bg, others)

    monkeypatch.setattr(R, "_dirty", spy)
    return area


def test_scan_limited_to_what_the_text_can_use(monkeypatch):
    # 2560×1440 的空白大图里一个单行段：译文 19 号一行约 210 像素宽，只需要扫这附近，不用扫到图边、图底
    area = _count_scanned(monkeypatch)
    img = Image.new("RGB", (2560, 1440), WHITE)
    p = render(img, [Segment(1, "中文", [OcrLine("中文", Box(10, 10, 60, 20))])], {1: "很长的一句译文还要再长一点"}, "zh").placements[1]
    assert p.px == 19 and len(p.lines) == 1
    assert 0 < sum(area) < 200_000          # 整列扫到图底就是 60×1420 + 整行扫到图边 2490×20，再向右又向下更多


def test_scan_stops_at_first_obstacle(monkeypatch):
    # 右边 5 像素就有一条竖线：扫描碰到它就停，不必把这一行带一直扫到图边
    area = _count_scanned(monkeypatch)
    img = Image.new("RGB", (2560, 100), WHITE)
    ImageDraw.Draw(img).line([(65, 0), (65, 99)], fill=(0, 0, 0))
    right, _, _ = R.room(img, Box(10, 10, 50, 20), WHITE, [], 2)
    assert right == 63
    assert area[0] <= 64 * 20


def test_no_scan_when_original_box_fits(monkeypatch):
    area = _count_scanned(monkeypatch)
    img = Image.new("RGB", (400, 100), WHITE)
    p = render(img, [Segment(1, "中文", [OcrLine("中文", Box(10, 10, 200, 20))])], {1: "短"}, "zh").placements[1]
    assert p.lines == ["短"] and area == []


def test_reach_scan_matches_full_scan():
    # 只扫「用得上」的范围：结果和扫到图边一样（障碍远在用得上的范围外时，延伸区域照样够用）
    img = Image.new("RGB", (2560, 400), WHITE)
    ImageDraw.Draw(img).line([(2000, 0), (2000, 399)], fill=(0, 0, 0))
    ImageDraw.Draw(img).line([(0, 300), (2559, 300)], fill=(0, 0, 0))
    seg = Segment(1, "中文", [OcrLine("中文", Box(10, 10, 60, 20))])
    reach = R._room_right(img, seg.box, WHITE, R._boxes_array([]), 10, 400)
    assert 410 <= reach < 1990
    assert R._room_right(img, seg.box, WHITE, R._boxes_array([]), 10, None) == 1990


def test_scan_ignores_boxes_outside_the_window():
    # 别的段的框（外扩 2 后是 x 120~145、y 80~95）紧挨在向右扫描窗口（x 150 起、y 100~120）的左上方、不相交：
    # 不能把它算进窗口里（否则切片下标为负，会把窗口开头一片误标成障碍，向右一点也延伸不了）
    img = Image.new("RGB", (400, 200), WHITE)
    right, down_only, _ = R.room(img, Box(100, 100, 50, 20), WHITE, [Box(122, 82, 21, 11)], 2)
    assert right == 398 and down_only == 198


def test_right_then_down_checks_the_widened_columns():
    # 单行段向右延伸到 138（右边是段 2），一行放不下要折到下方——但加宽的那几列下方（x=100, y=40）有墨点：
    # 只能看加宽后的整片区域，不能只看原框那几列。放不下两行就缩到 18 号排一行
    img = Image.new("RGB", (300, 60), WHITE)
    img.putpixel((100, 40), (0, 0, 0))
    segs = [Segment(1, "中文", [OcrLine("中文", Box(10, 10, 60, 20))]), Segment(2, "其他", [OcrLine("其他", Box(150, 10, 60, 20))])]
    p = render(img, segs, {1: "很长的一句译文"}, "zh").placements[1]
    assert p.px == 18 and p.lines == ["很长的一句译文"]


def test_reach_scan_looks_one_gap_further():
    # 障碍在「用得上的范围」外 5 像素（x=415，范围到 10+400=410）：退 gap=10 后真实右界 405 < 410，
    # 所以要多扫一个 gap 才能看见它，结果才和扫到图边一样
    img = Image.new("RGB", (2560, 1000), WHITE)
    ImageDraw.Draw(img).line([(415, 0), (415, 999)], fill=(0, 0, 0))
    ImageDraw.Draw(img).line([(0, 435), (2559, 435)], fill=(0, 0, 0))
    box, none = Box(10, 10, 60, 20), R._boxes_array([])
    assert R._room_right(img, box, WHITE, none, 10, 400) == R._room_right(img, box, WHITE, none, 10, None) == 405
    # 向下同理：范围到 10+420=430，横线在 435
    assert R._room_down(img, box, WHITE, none, 10, 70, 420) == R._room_down(img, box, WHITE, none, 10, 70, None) == 425


# ---------- 折行：每行只量几次，结果与逐个量完全相同 ----------

def _greedy(text, font, max_w):
    """改前的写法：逐个加词，加到放不下就换行。"""
    lines, cur = [], ""
    for tok in R._TOKEN_RE.findall(text):
        cand = cur + tok
        if font.getlength(cand.rstrip()) <= max_w:
            cur = cand
            continue
        if cur.strip():
            lines.append(cur.rstrip())
        cur = tok.lstrip()
        while cur and font.getlength(cur.rstrip()) > max_w:
            cut = max(len(cur) - 1, 1)
            while cut > 1 and font.getlength(cur[:cut]) > max_w:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


WORDS = ["the", "AVATAR", "Typography", "of", "a", "x", "supercalifragilistic", "To", "We,", "抑郁", "症", "，",
         "识别", "PHQ-9", "(2018)", "〉", "  ", "是"]


def test_wrap_text_matches_greedy_reference():
    rng = random.Random(19)
    for path in (SEGOE, MSYH):
        for px in (9, 13, 19):
            font = MixedFont(path, px)
            for _ in range(40):
                text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 30)))
                for w in (3, 30, 61, 120, 333):
                    assert wrap_text(text, font, w) == _greedy(text, font, w), (path, px, text, w)


def test_wrap_text_measures_only_a_few_times_per_line():
    calls = []
    font = MixedFont(MSYH, 19)
    real = font.getlength
    font.getlength = lambda s: calls.append(s) or real(s)
    lines = wrap_text("这是一段很长的中文译文用来测试折行" * 12, font, 19 * 20)
    assert len(lines) == 11
    assert len(calls) <= 60           # 逐个量要 200 多次


def test_wrap_text_gallops_when_lines_are_long():
    # 一行能放 100 个字、起始猜 8 个：倍增着往上找，几次就到；一步一步加 8 个要十几次
    calls = []
    font = MixedFont(MSYH, 10)
    real = font.getlength
    font.getlength = lambda s: calls.append(s) or real(s)
    lines = wrap_text("很" * 200, font, 10 * 100)
    assert lines == ["很" * 100, "很" * 100]
    assert len(calls) <= 16


def _fit_reference(text, path, w, h, start, min_px):
    for px in range(max(start, min_px), min_px - 1, -1):
        font = MixedFont(path, px)
        lines = _greedy(text, font, w)
        if all(font.getlength(l) <= w for l in lines) and (len(lines) - 1) * round(px * 1.25) + px <= h + 4:
            return px, lines
    return None


def test_fit_text_matches_reference():
    rng = random.Random(7)
    for path in (SEGOE, MSYH):
        for _ in range(60):
            text = " ".join(rng.choice(WORDS) for _ in range(rng.randint(1, 25)))
            w, h, start = rng.randint(40, 400), rng.randint(12, 120), rng.randint(10, 30)
            got = fit_text(text, path, w, h, start, wrap=True, min_px=9)
            want = _fit_reference(text, path, w, h, start, 9)
            if want is None:
                assert got.truncated
            else:
                assert (got.px, got.lines, got.truncated) == (want[0], want[1], False), (path, text, w, h, start)


def test_too_tall_sizes_are_skipped_without_wrapping(monkeypatch):
    # 300 个字、行宽 100、只有 20 像素高：无论哪个字号都至少要好几行，明显超高，不必真去折行
    called = []
    monkeypatch.setattr(R, "wrap_text", lambda *a: called.append(a) or ["x"])
    assert R._lines_if_fit("很" * 300, MixedFont(MSYH, 19), 19, 100, 20, True) is None
    assert called == []
    # 下限估计放得下时照常折行
    R._lines_if_fit("很很", MixedFont(MSYH, 19), 19, 100, 20, True)
    assert len(called) == 1


# ---------- 整串都在主字体里时不逐字查字形 ----------

def test_primary_covered_text_skips_per_char_lookup(monkeypatch):
    font = MixedFont(SEGOE, 19)
    text = "plain latin text, all in Segoe UI"
    font.getlength(text)                       # 预热：逐字的字形查询记进缓存
    calls = []
    real = R.has_glyph
    monkeypatch.setattr(R, "has_glyph", lambda p, c: calls.append(c) or real(p, c))
    w = font.getlength(text)
    runs = font.runs(text)
    assert calls == []
    assert w == ImageFont.truetype(SEGOE, 19).getlength(text)
    assert [s for _, s in runs] == [text]


def test_mixed_text_still_uses_fallback_runs():
    font = MixedFont(SEGOE, 40)
    runs = font.runs("ab〉c")
    assert [s for _, s in runs] == ["ab", "〉", "c"]
    assert font.getlength("ab〉c") == sum(f.getlength(s) for f, s in runs)

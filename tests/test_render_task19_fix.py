"""Task 19 评审修正的排字测试（次要项）：缩小时各区域都试、截断选面积最大的区域；字号按相邻间隙归类；
只有符号的行不参与估字号；韩文、日文假名按方块字估；后备字体下沿更低时加大行距。
每个守卫用自己的数据；期望值写死。"""
from PIL import Image, ImageDraw

from shotrans.models import Box, OcrLine, Segment
from shotrans.render import MixedFont, line_span, render, start_sizes

WHITE = (255, 255, 255)
SEGOE = r"C:\Windows\Fonts\segoeui.ttf"


# ---------- 缩小时每个区域都试；截断时选面积最大的 ----------

def _tall_and_wide():
    # 两行段（框 100×44，起始 19 号）：原宽向下一直空到图底（窄而高 100×300），
    # 右下方 y=70 有一条横线，向右又向下只有 380×50（宽而矮）
    img = Image.new("RGB", (400, 320), WHITE)
    ImageDraw.Draw(img).line([(150, 70), (399, 70)], fill=(0, 0, 0))
    seg = Segment(1, "中文", [OcrLine("中文", Box(10, 10, 100, 20)), OcrLine("再来", Box(10, 34, 100, 20))])
    return img, seg


def test_shrinks_in_whichever_region_fits_first():
    # 70 个字：19 号哪里都放不下；18 号在窄而高的区域里放得下（每行 5 字 × 14 行）。
    # 只在最后一个区域（宽而矮）里缩会一路缩到 15 号
    img, seg = _tall_and_wide()
    p = render(img, [seg], {1: "很" * 70}, "zh").placements[1]
    assert (p.px, p.truncated) == (18, False) and p.area.right <= 110


def test_truncates_in_largest_region():
    # 400 个字到下限 14 号也放不下：在面积大的窄而高区域（100×300 > 380×50）里截断，排 17 行，而不是宽而矮的 3 行
    img, seg = _tall_and_wide()
    p = render(img, [seg], {1: "很" * 400}, "zh").placements[1]
    assert p.truncated and p.px == 14 and len(p.lines) == 17 and p.area.right <= 110


# ---------- 字号按相邻间隙归类 ----------

def _cjk_segs(heights):
    return [Segment(i, "中", [OcrLine("中", Box(10, 10 + 60 * i, 200, h))]) for i, h in enumerate(heights, 1)]


def test_size_classes_split_by_gaps_not_by_class_minimum():
    # 汉字框高 20/22/23 → 19.0 / 20.9 / 21.85：相邻相差 10%、4.5%，同一类（中位数 20.9 → 21）。
    # 按「和本类最小值比」会因 21.85/19.0 = 1.15 把第三段切出去
    assert start_sizes(_cjk_segs([20, 22, 23])) == {1: 21, 2: 21, 3: 21}


def test_size_class_span_is_capped():
    # 40/44/48/49：相邻都近，最大/最小 = 1.225 ≤ 1.25，同一类；40/44/48/51：1.275 > 1.25，最后一段另起一类
    assert len(set(start_sizes(_cjk_segs([40, 44, 48, 49])).values())) == 1
    assert start_sizes(_cjk_segs([40, 44, 48, 51])) == {1: 42, 2: 42, 3: 42, 4: 48}


# ---------- 只有符号的行：估不出 ----------

def test_symbol_only_line_has_no_span():
    assert line_span("→ • ©") is None
    assert line_span("→ a") is not None


def test_symbol_only_segment_uses_own_box_and_stays_out_of_classes():
    # 「→」框高 21：不按 x 高度（会估成 35 号），按自己的框高 × 0.95 = 20；也不和 19 号的正文段归成一类
    segs = [Segment(1, "中", [OcrLine("中", Box(10, 10, 200, 20))]), Segment(2, "→", [OcrLine("→", Box(10, 80, 20, 21))])]
    assert start_sizes(segs) == {1: 19, 2: 20}


def test_symbol_only_lines_ignored_inside_a_segment():
    # 段里两行只有「•」（各估 33 号）、一行汉字（19 号）：只按汉字那行算，不是三行取中位数的 33
    lines = [OcrLine("•", Box(10, 10, 20, 20)), OcrLine("•", Box(10, 34, 20, 20)), OcrLine("中文", Box(10, 58, 200, 20))]
    assert start_sizes([Segment(1, "• • 中文", lines)]) == {1: 19}


# ---------- 后备字体下沿更低时加大行距 ----------

def test_line_height_grows_for_low_fallback_glyph():
    # 9 号 Segoe UI：行距 round(9×1.25) = 11。「∯」走后备字体，按基线对齐后比 g j p q y 还低 1 像素 → 12；
    # 「，」也走后备字体但不比 g 低，不加
    f = MixedFont(SEGOE, 9)
    assert f.line_height("agy") == 11 and f.line_height("a，") == 11 and f.line_height("a∯") == 12


def test_fit_uses_grown_line_height():
    # 宽 30 只能一行放「aaaa」：两行在 11 的行距下高 9+11 = 20，正好放进 16+2×2；行距 12 就放不下，到下限 9 号只能截断
    from shotrans.render import fit_text
    assert not fit_text("aaaa bbbb", SEGOE, 30, 16, 9, wrap=True, min_px=9).truncated
    f = fit_text("aaaa ∯bbb", SEGOE, 30, 16, 9, wrap=True, min_px=9)
    assert f.truncated and len(f.lines) == 1      # 截断时也按 12 的行距算能放几行：(16+4−9)//12+1 = 1


def test_render_draws_with_grown_line_height():
    # 两行汉字框高 15 → 起始 14 号；英文译文折成两行，行距 round(14×1.25)=18，有「∯」时 19：译文区域高 19 + 14 = 33
    img = Image.new("RGB", (300, 120), WHITE)
    seg = Segment(1, "中文", [OcrLine("中文", Box(10, 10, 50, 15)), OcrLine("再来", Box(10, 30, 50, 15))])
    p = render(img, [seg], {1: "aaaa ∯bbb"}, "en").placements[1]
    assert (p.px, len(p.lines), p.area.h) == (14, 2, 33)


def test_kana_and_hangul_are_square():
    for text in ("ひらがな", "カタカナ", "안녕하세요", "ㅎㅏ"):
        assert line_span(text) == 1.0526, text

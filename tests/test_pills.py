"""Task 20：行内代码胶囊的识别、分段、保护、绘制。每个守卫用自己的合成图；期望值写死。

几何用例里的「词」是竖条纹（每 3 列一条 1 像素黑线）：框里最多的颜色仍是底色，框的位置精确可控。"""
from PIL import Image, ImageDraw, ImageFont

from shotrans.layout import group_lines
from shotrans.models import Box, OcrLine, OcrWord, Pill, Segment
from shotrans.ocr import join_words
from shotrans.pills import find_pills, pill_around
from shotrans.protect import protect, restore
from shotrans.render import render
from tests.router_fakes import echo, make, run

WHITE = (255, 255, 255)
FILL = (240, 240, 239)
EDGE = (216, 216, 216)
Y, H = 40, 14            # 词框 y 40~53，行高 14：色带上下各最多伸出 7 像素，总高最多 25.2


def stripes(d, b: Box, color=(0, 0, 0)):
    for x in range(b.x, b.right, 3):
        d.line([(x, b.y), (x, b.bottom - 1)], fill=color)


def line_of(spec, y=Y, h=H):
    """spec：[(文字, x, 宽), ...] → OcrLine（带词级框）。"""
    words = tuple(OcrWord(t, Box(x, y, w, h)) for t, x, w in spec)
    box = words[0].box
    for w in words[1:]:
        box = box.union(w.box)
    return OcrLine(join_words([w.text for w in words]), box, words)


def scene(spec, pill=None, size=(400, 100), page=WHITE, fill=FILL, edge=EDGE, y=Y, h=H):
    """pill：(左, 上, 右, 下) 描边矩形（含描边那一圈），里面填 fill。"""
    img = Image.new("RGB", size, page)
    d = ImageDraw.Draw(img)
    if pill:
        d.rectangle(pill, fill=fill, outline=edge)
    ln = line_of(spec, y, h)
    for w in ln.words:
        stripes(d, w.box)
    return img, ln


def pills(img, ln):
    import numpy as np
    return find_pills(np.asarray(img), ln)


SPEC = [("press", 20, 50), ("Ctrl+Alt+Q", 100, 90), ("to", 200, 20), ("start", 230, 50)]


# ---------- 识别 ----------

def test_pill_found_with_exact_fill_box():
    # 描边矩形 x 95~195、y 36~57：填色区 x 96~194、y 37~56
    img, ln = scene(SPEC, pill=(95, 36, 195, 57))
    assert pills(img, ln) == [Pill("Ctrl+Alt+Q", Box(96, 37, 99, 20))]


def test_plain_text_on_white_has_no_pill():
    img, ln = scene(SPEC)
    assert pills(img, ln) == []


def test_two_words_in_one_pill_become_one_pill():
    spec = [("run", 20, 30), ("git", 70, 30), ("status", 106, 60), ("now", 190, 30)]
    img, ln = scene(spec, pill=(64, 36, 172, 57))
    assert pills(img, ln) == [Pill("git status", Box(65, 37, 107, 20))]


def test_word_that_cannot_find_its_own_pill_joins_the_neighbours():
    # 胶囊开头的红色圆点：框里最多的是红色，自己找不出色带；但它落在后面词找到的胶囊里，也算进去
    img, ln = scene([("say", 20, 30), ("●", 70, 10), ("vLLM", 86, 40), ("ok", 150, 20)], pill=(64, 36, 132, 57))
    ImageDraw.Draw(img).rectangle([70, 40, 79, 53], fill=(140, 51, 49))
    assert [p.text for p in pills(img, ln)] == ["● vLLM"]


def test_pad_guard_band_must_close_within_half_line_height():
    # 上面留白 8 行（> 7）：像标签页、页眉色带，不是胶囊；留白 7 行就是
    img, ln = scene(SPEC, pill=(95, 31, 195, 55))   # 填色 y 32~54：上 8 行、下 1 行
    assert pills(img, ln) == []
    img, ln = scene(SPEC, pill=(95, 32, 195, 55))   # 上 7 行、下 1 行
    assert [p.text for p in pills(img, ln)] == ["Ctrl+Alt+Q"]


def test_max_height_guard():
    # 上下各 6 行：总高 26 > 25.2，不算；上 6 下 5：25，算
    img, ln = scene(SPEC, pill=(95, 33, 195, 60))
    assert pills(img, ln) == []
    img, ln = scene(SPEC, pill=(95, 33, 195, 59))
    assert [p.text for p in pills(img, ln)] == ["Ctrl+Alt+Q"]


def test_edge_row_must_be_uniform():
    # 上边是 3 行黑白相间的点（像上一行的字），下边是均匀的灰线、线外也是灰的，左右有灰竖线：
    # 底色 c 是白的，上下都「碰到了东西」，但上面那不是胶囊的边
    img, ln = scene(SPEC)
    d = ImageDraw.Draw(img)
    for yy in (34, 35, 36):
        for x in range(90, 201):
            if (x + yy) % 2:
                img.putpixel((x, yy), (0, 0, 0))
    d.rectangle([90, 57, 200, 60], fill=(150, 150, 150))
    d.line([(95, 30), (95, 60)], fill=(150, 150, 150))
    d.line([(195, 30), (195, 60)], fill=(150, 150, 150))
    assert pill_around(__import__("numpy").asarray(img), Box(100, Y, 90, H), H) is None


def test_beyond_guard_table_cell_is_not_a_pill():
    # 白底单元格：上下各一条灰线、线外还是白的——表格，不是胶囊
    img, ln = scene(SPEC)
    d = ImageDraw.Draw(img)
    d.line([(0, 36), (399, 36)], fill=(150, 150, 150))
    d.line([(0, 57), (399, 57)], fill=(150, 150, 150))
    d.line([(95, 36), (95, 57)], fill=(150, 150, 150))
    d.line([(195, 36), (195, 57)], fill=(150, 150, 150))
    assert pills(img, ln) == []


def test_beyond_guard_control_white_pill_on_grey_page_is_a_pill():
    # 对照：同样的线，但线外是灰色页面——白色胶囊
    img, ln = scene(SPEC, pill=(95, 36, 195, 57), page=(200, 200, 200), fill=WHITE, edge=(150, 150, 150))
    assert [p.text for p in pills(img, ln)] == ["Ctrl+Alt+Q"]


def test_hug_left_guard():
    # 色带左头离胶囊第一个词 31 像素（> 1.5 × 14 = 21）：不是贴着字的胶囊；20 像素算
    img, ln = scene(SPEC, pill=(68, 36, 195, 57))    # 填色从 69 起：离 100 是 31
    assert pills(img, ln) == []
    img, ln = scene(SPEC, pill=(79, 36, 195, 57))    # 填色从 80 起：离 100 是 20
    assert [p.text for p in pills(img, ln)] == ["Ctrl+Alt+Q"]


def test_hug_right_guard():
    # 右头离最后一个词 43 像素（> 3 × 14 = 42）：不算；42 像素算（右边常有 OCR 没认出的图标）
    spec = [("press", 20, 50), ("Ctrl+Alt+Q", 100, 90), ("to", 300, 20)]
    img, ln = scene(spec, pill=(95, 36, 233, 57))    # 填色到 232（右边界 233）：离 190 是 43
    assert pills(img, ln) == []
    img, ln = scene(spec, pill=(95, 36, 232, 57))    # 填色到 231（右边界 232）：离 190 是 42
    assert [p.text for p in pills(img, ln)] == ["Ctrl+Alt+Q"]


def test_saturated_highlight_is_not_a_pill():
    # 没有描边的色带：Ctrl+F 的黄色高亮、选中文字的浅蓝底——彩色，不是代码胶囊
    for fill in ((255, 235, 59), (204, 232, 255)):
        img, ln = scene(SPEC, pill=(95, 36, 195, 57), fill=fill, edge=fill)
        assert pills(img, ln) == [], fill


def test_grey_borderless_band_is_a_pill():
    # 对照：同样没有描边的色带，灰色的（深色主题的代码底 GitHub 那种略带蓝的灰也算）是胶囊
    for fill in ((240, 240, 239), (230, 234, 240)):
        img, ln = scene(SPEC, pill=(95, 36, 195, 57), fill=fill, edge=fill)
        assert [p.text for p in pills(img, ln)] == ["Ctrl+Alt+Q"], fill


def test_line_without_words_has_no_pill():
    img, ln = scene(SPEC, pill=(95, 36, 195, 57))
    assert pills(img, OcrLine(ln.text, ln.box)) == []


def test_real_font_rounded_pill():
    # 等宽字 + 浅灰圆角矩形：和聊天界面的行内代码一样
    font = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 16)
    img = Image.new("RGB", (420, 80), WHITE)
    d = ImageDraw.Draw(img)
    x, words = 20, []
    for t in ("press", "Ctrl+Alt+Q", "to", "start"):
        x = round(x)
        words.append((t, x, d.textbbox((x, 30), t, font=font)))
        x += font.getlength(t + " ")
    top = min(b[1] for _, _, b in words)
    bot = max(b[3] for _, _, b in words)
    _, _, cb = words[1]
    d.rounded_rectangle([cb[0] - 5, top - 4, cb[2] + 5, bot + 3], radius=5, fill=FILL, outline=EDGE)
    for t, x0, _ in words:
        d.text((x0, 30), t, font=font, fill=(150, 40, 40) if t == "Ctrl+Alt+Q" else (0, 0, 0))
    ws = tuple(OcrWord(t, Box(b[0], b[1], b[2] - b[0], b[3] - b[1])) for t, _, b in words)
    lb = ws[0].box
    for w in ws[1:]:
        lb = lb.union(w.box)
    found = pills(img, OcrLine("press Ctrl+Alt+Q to start", lb, ws))
    assert [p.text for p in found] == ["Ctrl+Alt+Q"]
    p = found[0].box
    assert abs(p.x - (cb[0] - 4)) <= 3 and abs(p.right - (cb[2] + 5)) <= 3
    assert p.y == top - 3 and p.bottom == bot + 3


# ---------- 分段 ----------

def test_segment_keeps_inline_pill():
    img, ln = scene(SPEC, pill=(95, 36, 195, 57))
    (seg,) = group_lines([ln], "para", img)
    assert seg.text == "press Ctrl+Alt+Q to start" and [p.text for p in seg.pills] == ["Ctrl+Alt+Q"]


def test_segment_that_is_all_pill_is_a_button_not_code():
    # 整段的词全在色带里（独立的按钮、选中的列表项）：照常翻，不当胶囊
    img, ln = scene([("Save", 100, 40), ("changes", 146, 50)], pill=(95, 36, 200, 57))
    (seg,) = group_lines([ln], "para", img)
    assert seg.pills == ()


def test_pill_text_must_be_a_substring_of_the_segment():
    # 胶囊在行末、以连字符断词：「data-」+ 下一行「base」拼成「database」，胶囊文字不在段落里，换不了占位符——不要
    l1_spec = [("see", 20, 30), ("the", 56, 30), ("data-", 92, 50)]
    img, l1 = scene(l1_spec, pill=(87, 36, 146, 57))
    d = ImageDraw.Draw(img)
    l2 = line_of([("base", 20, 40), ("now", 66, 30), ("ok", 102, 40)], y=60)
    for w in l2.words:
        stripes(d, w.box)
    (seg,) = group_lines([l1, l2], "para", img)
    assert seg.text == "see the database now ok" and seg.pills == ()


def test_skipped_segment_has_no_pills():
    img, ln = scene([("x7", 20, 20), ("qz", 100, 20), ("ab", 140, 20)], pill=(95, 36, 125, 57))
    (seg,) = group_lines([ln], "para", img)
    assert seg.skip and seg.pills == ()


def test_no_image_no_pills():
    _, ln = scene(SPEC, pill=(95, 36, 195, 57))
    assert group_lines([ln], "para")[0].pills == ()


# ---------- 保护 ----------

def test_literal_is_protected_verbatim_before_other_rules():
    p = protect("run ⟦ git push https://x.org ⟧ now", literals=["git push https://x.org"])
    assert p.text == "run ⟦ ⟨1⟩ ⟧ now" and p.slots == {"⟨1⟩": "git push https://x.org"}


def test_literal_combo_gets_fixed_spelling():
    # 胶囊里除了按键组合还有别的字：胶囊文字也要先纠错，才对得上纠错后的段落原文，整块一个占位符
    p = protect("按Ctr1+A1t+Q to quit框选", literals=["Ctr1+A1t+Q to quit"])
    assert p.slots == {"⟨1⟩": "Ctrl+Alt+Q to quit"} and p.text == "按⟨1⟩框选"


def test_short_literal_does_not_eat_inside_other_words():
    # 胶囊「id」：identifier、hide 里的 id 不能换（没给位置时按整词找第一处）
    p = protect("Set id to the user identifier, then hide it.", (), ("id",))
    assert p.text == "Set ⟨1⟩ to the user identifier, then hide it." and p.slots == {"⟨1⟩": "id"}


def test_literal_word_boundary_on_each_side():
    # rapid 里的 id 只有左边粘着字母，idea 里的只有右边粘着：两头都要查，换的是最后那个整词
    p = protect("rapid idea id", (), ("id",))
    assert p.text == "rapid idea ⟨1⟩"


def test_literal_count_limits_replacements():
    # 两个同样文字的胶囊只换两处，第三处 foo 是普通文字
    p = protect("use foo or foo then foo", (), ("foo", "foo"))
    assert p.text == "use ⟨1⟩ or ⟨2⟩ then foo"


def test_literal_with_position_replaces_exactly_there():
    # 给了位置：换的是胶囊那一处（第二个 foo，下标 11），不是第一处
    p = protect("use foo or foo then", (), (("foo", 11),))
    assert p.text == "use foo or ⟨1⟩ then" and p.slots == {"⟨1⟩": "foo"}


def test_literal_position_mismatch_falls_back_to_whole_word():
    # 位置对不上（那里不是这段字）：退回按整词找
    p = protect("hide id now", (), (("id", 0),))
    assert p.text == "hide ⟨1⟩ now"


def test_single_cjk_char_pill_takes_only_its_own_char():
    p = protect("键盘上按键", (), (("键", 4),))
    assert p.text == "键盘上按⟨1⟩"
    p = protect("键盘上按键", (), ("键",))        # 没位置：也只换一处
    assert p.text == "⟨1⟩盘上按键"


def test_segment_pill_records_its_position():
    # 段里有两个「id」，胶囊是第二个：start 指向第二个（下标 7），路由只换那一处
    spec = [("id", 20, 20), ("and", 50, 30), ("id", 90, 20), ("ok", 140, 20)]
    img, ln = scene(spec, pill=(85, 36, 115, 57))
    (seg,) = group_lines([ln], "para", img)
    assert seg.text == "id and id ok" and [(p.text, p.start) for p in seg.pills] == [("id", 7)]
    router, v, _ = make(echo("T:"))
    run(router, [seg])
    assert v.calls[0]["jobs"][0].text == "id and ⟨1⟩ ok"


def test_router_sends_pill_as_placeholder_and_restores_it():
    seg = Segment(1, "工具栏：．vLLM，译文原/译行/段", [OcrLine("x", Box(0, 0, 100, 20))],
                  pills=(Pill("．vLLM，译文原/译行/段", Box(0, 0, 50, 20)),))
    router, v, _ = make(echo("T:"))
    res, _ = run(router, [seg], "zh2en")
    assert v.calls[0]["jobs"][0].text == "工具栏：⟨1⟩"
    assert res[1].text == "T:工具栏：．vLLM，译文原/译行/段"


def test_router_cache_key_includes_pills():
    plain = Segment(1, "press Ctrl+K now", [OcrLine("x", Box(0, 0, 100, 20))])
    pilled = Segment(1, "press Ctrl+K now", [OcrLine("x", Box(0, 0, 100, 20))], pills=(Pill("press", Box(0, 0, 9, 9)),))
    router, v, _ = make(echo("T:"))
    run(router, [plain])
    run(router, [pilled])
    assert len(v.calls) == 2 and v.calls[1]["jobs"][0].text == "⟨1⟩ ⟨2⟩ now"


# ---------- 绘制 ----------

def test_render_erases_whole_pill_beyond_the_line_box():
    # 胶囊在行末、伸出 OCR 框（右边界 190）70 像素：里面有 OCR 没认出的「×」（离框 50 像素，不算行末漏认的字）。
    # 整块胶囊连字一起抹成底色
    img, ln = scene(SPEC[:2], pill=(95, 36, 260, 57))
    ImageDraw.Draw(img).line([(240, 42), (250, 52)], fill=(0, 0, 0), width=2)
    seg = Segment(1, ln.text, [ln], pills=(Pill("Ctrl+Alt+Q", Box(96, 37, 164, 20)),))
    out = render(img, [seg], {1: "按 ⟨1⟩ 开始"}, "zh").image
    assert out.getpixel((245, 47)) == WHITE and out.getpixel((255, 38)) == WHITE

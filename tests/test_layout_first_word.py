"""评审修正（Task 20 附带）：「写满」判断里下一行的第一个词按 render 的分词取——
碰到空白或方块字（汉字、全角标点、假名、谚文）就停；行末墨迹扫描不算上下相邻行框重叠的那几像素。
每个守卫用自己的数据；期望值写死。"""
from PIL import Image, ImageDraw

from shotrans.layout import group_lines
from shotrans.models import Box, OcrLine


def L(text, x, y, w, h=18):
    return OcrLine(text, Box(x, y, w, h))


def _n(body: str, heading: str = "还有两点") -> int:
    """评审的复现：短标题宽 80，下一行正文宽 894。标题后面剩 814 的空白，放得下正文的第一个词——标题有意断开，应是 2 段。"""
    return len(group_lines([L(heading, 29, 470, 80), L(body, 29, 496, 894)], "para"))


def test_body_starting_with_cjk_char_splits():
    assert _n("这次启动的是我单独开的一份干净副本在临时目录里所以导出的文件不在你的截图翻译目录下") == 2


def test_body_starting_with_digits_splits():
    assert _n("2026年这次启动的是我单独开的一份干净副本在临时目录里所以导出的文件不在你的截图目录") == 2


def test_body_starting_with_latin_word_splits():
    assert _n("Python这次启动的是我单独开的一份干净副本在临时目录里所以导出的文件不在你的截图") == 2


def test_body_starting_with_curly_quote_splits():
    assert _n("“导出”按钮是在当前运行实例启动之后才加的需要重启才能生效但当前代码正被修改中") == 2


def test_japanese_body_starting_with_kana_splits():
    assert _n("これはテストのためのながいほんぶんのいちぎょうめでありまだまだつづいていきます", "はじめに") == 2


def test_mixed_latin_then_cjk_word_stops_at_the_cjk_char():
    # 下一行 30 个字宽 300 → 字宽 10；块右边 310。第一个词按分词是「vLLM」：4 字 + 空格 + 放宽 1 字 = 60。
    # 上一行宽 241（剩 59）放不下 → 写满，接成一段；宽 240（剩 60）放得下 → 有意断开，拆。
    # 只取「v」会在 241 就拆；取到整行（split()[0]）在 240 也接——两种错法各被一条断言抓住。
    body = "vLLM服务没有响应时改用备用服务继续翻译剩下的段落内容即可"
    assert len(group_lines([L("first line of the paragraph", 10, 10, 241, 14), L(body, 10, 28, 300, 14)], "para")) == 1
    assert len(group_lines([L("first line of the paragraph", 10, 10, 240, 14), L(body, 10, 28, 300, 14)], "para")) == 2


# ---------- 行末墨迹扫描不算相邻行框重叠的部分 ----------

def _ink_rows(y0, y1):
    img = Image.new("RGB", (400, 100), "white")
    ImageDraw.Draw(img).rectangle([200, y0, 290, y1], fill=(0, 0, 0))
    return img


def test_next_line_overlap_is_not_trailing_ink():
    # 上一行框 y 10~24，下一行框 y 22~36（OCR 框重叠 2 像素）。x 200~290、y 22~23 的墨迹是下一行的上伸部分，
    # 不是上一行行末没认出的字：上一行只到 190，剩的空白放得下「and 」——照样拆开
    lines = [L("vector of the special token as C", 10, 10, 180, 14),
             L("and the final hidden vector for the input", 10, 22, 300, 14)]
    assert len(group_lines(lines, "para", _ink_rows(22, 23))) == 2


def test_previous_line_overlap_is_not_trailing_ink():
    # 第 2 行框 y 22~36 和第 1 行框（y 10~24）重叠 2 像素；那里的墨迹是第 1 行的下伸部分，不算第 2 行的行末
    lines = [L("a long first line that fills the whole column width ok", 10, 10, 300, 14),
             L("vector of the special token as C", 10, 22, 180, 14),
             L("and the final hidden vector for the input", 10, 40, 300, 14)]
    texts = [s.text for s in group_lines(lines, "para", _ink_rows(22, 23))]
    assert texts == ["a long first line that fills the whole column width ok vector of the special token as C",
                     "and the final hidden vector for the input"]


def test_ink_inside_the_line_band_still_counts():
    # 对照：墨迹在上一行独占的 y 13~20 里——仍是行末没认出的字，写满，接成一段
    lines = [L("vector of the special token as C", 10, 10, 180, 14),
             L("and the final hidden vector for the input", 10, 22, 300, 14)]
    assert len(group_lines(lines, "para", _ink_rows(13, 20))) == 1

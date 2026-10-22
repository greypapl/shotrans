"""把 OCR 的行分组成待翻译单位，判断翻译方向，识别公式行。

分组分三步：
1. 横向合并：Windows OCR 会在链接变色、上标、较大字距处把一行拆成几段，先把同一视觉行的碎片拼回来
   （离得较远的碎片要等高、居中对齐、不像表格的一列、且两段之间是干净底色才拼——这要传入截图）；
2. 纵向成段：相邻、等高、左对齐的行并成一段——但上一行要写满才接续（短标题、链接列表、标签-值各自成段），
   「标签: 值」的标签行不接进上面的段，列表项（含全角编号）各起一段；
3. 排阅读顺序（递归 XY 切分）：先按横贯的空隙切成「行带」自上而下，带内按栏从左到右，栏内自上而下。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

import numpy as np
from PIL import Image

from .models import Box, OcrLine, Pill, Segment
from .pills import find_pills
from .ocr import CJK_RE, is_cjk_char, join_words
from .render import SQUARE_RE

EN_WORD_RE = re.compile(r"[A-Za-z]{2,}")
ZH_PER_WORD = 2        # 一个英文单词约合几个汉字（判断翻译方向用）

MATH_CHARS = set("=+−-×÷*/^_<>≤≥±∑∫√∞∂∆∇∈∉⊂⊆∪∩→←↔≈≠≡∝αβγδεθλμσφψωΣΠΩ(){}[]|0123456789.,")
WORD_RE = re.compile(r"[A-Za-z]{3,}|[一-鿿]")

# ---- 横向合并的阈值（单位：较矮那段的行高）----
ROW_GAP = 1.0          # 水平间距 ≤ 1 倍行高才算同一行。实测 BERT 双栏栏距 ≈ 1.6 倍、表格列距 ≥ 1.8 倍，
                       # 而链接/上标处的断开只有 0~0.4 倍
ROW_OVERLAP_MAX = 0.5  # 左右两段最多重叠半个行高（OCR 框的误差）；再多就是叠在一起的两样东西
ROW_V_OVERLAP = 0.5    # 竖直方向至少重叠较矮者高度的一半
SUP_RATIO = 0.8        # 比对方矮到 0.8 倍以下的碎片当上标/下标：只要竖直有重叠就并进这一行，且不参与行高判断
# ---- 行内碎片的放宽合并（Task 19）：句中的行内代码、按钮式文字之间常有较宽的空白，OCR 会拆成离得较远的两段 ----
LOOSE_GAP = 3.0        # 间距 ≤ 3 倍行高也可以合并，但要满足下面两条、右段不像表格的一列、且间隙是干净底色
                       # （导航菜单项之间有下拉箭头、按钮之间有边框：并起来抹框会把它们一起抹掉。bench 15 实测）
GAP_CLEAN = 10         # 间隙里每个像素和间隙中位色的差都不超过这么多，才算干净底色（间隙两端各让 1 像素给抗锯齿边）。
                       # 不用 render 的 30：bench 17 维基「外观」旁的「隐藏」按钮浅灰底与白底只差约 20，30 会把按钮并进来
LOOSE_CENTER = 0.25    # 两段竖直中线相差 ≤ 0.25 倍行高（比普通合并要求的「重叠一半」严）
COLUMN_ALIGN = 0.2     # 右段的左/右边或中线与上下别的行相差 ≤ 0.2 倍行高（至少 2 像素）就算对齐成列
COLUMN_REACH = 5       # 只看上下 5 倍行高以内的行
# ---- 纵向成段（Task 19）----
WRAP_TOL = 1.0         # 上一行要「写满」：剩下的空白放不下下一行的第一个词（+ 空格），再放宽 1 个字宽（见 _is_full）
FULL_LINE = 0.8        # 估不出字宽时的退路：右端到块左边 ≥ 块内最长行宽（含当前行）的 80%
LABEL_GAP = 4.0        # 右边 4 倍行高内紧挨着同一行带里另一段、
LABEL_WIDTH = 2.0      # 且那一段至少有它 2 倍宽的行，
LABEL_SHORT = 3.0      # 而且它以冒号结尾、或者短到值的 1/3 以下，是「标签: 值」的标签，不接进上面的段。
                       # 不用「没写满」判断：bench 14 里「Subjects:」「Cite as:」两个短标签上下相邻，互相都算写满

LIST_START_RE = re.compile(
    r"^\s*(?:"
    r"[•·▪]"                              # 项目符号（不要求后面有空格）
    r"|[-–*]\s"                           # - – * 后面要跟空格，免得把 -based、-0.5 当成列表
    r"|\d{1,2}[.)](?!\d)"                 # 1. 1)；最多两位数，免得把 2018). 3.5 当成编号
    r"|\d{1,2}[．、，）](?!\d)"            # 全角：1． 2、 3， 4）（OCR 常把中文页面里的编号认成全角）
    r"|\(\d{1,2}\)"                       # (1)
    r"|（\d{1,2}）"                        # （1）
    r"|[①-⑳]"                            # ① ~ ⑳
    r"|\(?[A-Za-z]\)"                     # a) (a)
    r"|[一二三四五六七八九十]{1,3}、"       # 一、
    r"|（[一二三四五六七八九十]{1,3}）"     # （一）
    r")"
)


def detect_direction(text: str) -> str:
    """汉字数对比英文单词数（不是字母数：一个单词有好几个字母，按字母算会偏向英文）。
    一个英文单词大约对应两个汉字，所以汉字数 > 2 × 单词数才算中文为主；打平算英文。
    单字母（A-V+P 里的 A、V、P，单选框被认成的 O）不算单词。
    标定：Task 7 用例 08（中文表格夹英文路径）66 汉字 / 17 词 = 3.9 → 中文；
    中文正文用例 01/02/17 的比值 ≥ 30，英文用例全部为 0。"""
    cjk = len(CJK_RE.findall(text))
    words = len(EN_WORD_RE.findall(text))
    return "zh2en" if cjk > ZH_PER_WORD * words else "en2zh"


# 紧跟在词后的引用标号（「pain.[17]」「word[3,4]」「word [12–15]」）：算公式比例前先去掉，免得整行被判成公式。
# 前面要是至少 2 个字母的词（可带一个句读）：「x[1] + y[2]」里单个字母后的方括号是下标，不去。
# 后面只能是行尾、空白再接文字、或句读：「arr[10] = arr[9] + 1」「list[3] == x」「f(arr[1])」是代码/公式，不去
CITATION_RE = re.compile(r"([A-Za-z]{2,}[.,;:]?)\s?\[\d{1,3}(?:\s*[,–—-]\s*\d{1,3})*\]"
                         r"(?=\s*$|\s+[A-Za-z]|[.,;:])")


def is_formula(text: str) -> bool:
    text = CITATION_RE.sub(r"\1", text)
    chars = [c for c in text if not c.isspace()]
    if len(chars) < 3:
        return False
    math = sum(1 for c in chars if c in MATH_CHARS)
    return math / len(chars) >= 0.4 and len(WORD_RE.findall(text)) <= 2


# ---- 不该翻的段（Task 20）----
# 一块拉丁字母：字母串，或用 & / . 连起来的几串（Q&A、R&D、U.S.、A/B 是一块）
LATIN_CHUNK_RE = re.compile(r"[A-Za-z]+(?:[&/.][A-Za-z]+)*")
# 连续 2 个方块字（汉字、假名、谚文）：有真正的中日韩文字，不是碎片
CJK_RUN_RE = re.compile(r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿가-힣ᄀ-ᇿ㄰-㆏]{2,}")
CJK_ONE_RE = re.compile(r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿가-힣ᄀ-ᇿ㄰-㆏]")
# 常见的两三个字母的英文词：单独出现时是真词，不是 logo 碎片（「I am OK」「Go to」「No, it is」照常翻）；
# 也包括月份、星期的缩写（Jan Feb Mar）和称谓（Mr. Li）
COMMON_SHORT = frozenset("""
an am as at be by do go he hi if in is it me my no of oh ok on or so to up us we
add age ago all and any app are ask bad big box but buy can cut day did dog end far few fix for fun get got
had has her him his hot how its job key let log low man map may new nor not now off old one our out own pay
per put ran red run sat saw say see set she sit six ten the too top try two use via was way who why win yes
yet you
jan feb mar apr jun jul aug sep oct nov dec mon tue wed thu fri sun
mr mrs ms dr jr sr st
fig eq tab ref sec vol pp et al cf vs etc
""".split())   # 最后一行：论文里的缩写（「Fig. 3a」「et al.」「cf. Eq. 5」），不是 logo 碎片；figs、eqs、refs 四个字母本来就像词


def _word_like(chunk: str) -> bool:
    """一块字母像不像一个词：4 个字母以上、常见短词、或全大写缩写（AI、PDF）——都至少 2 个字母；
    用 & / . 连起来的全大写字母（Q&A、R&D、U.S、A/B）也算缩写。
    单个字母（a、I、r）不算：logo 碎片里最常见的就是单个字母。"""
    parts = re.split(r"[&/.]", chunk)
    if len(parts) > 1:
        return all(p.isupper() for p in parts) or any(_word_like(p) for p in parts)
    if len(chunk) < 2:
        return False
    return len(chunk) >= 4 or chunk.lower() in COMMON_SHORT or chunk.isupper()


def is_noise(text: str) -> bool:
    """没有任何像词的东西：拉丁字母被拆成至少 2 小块、每块都不像词，且没有连续 2 个方块字——
    logo、图标被 OCR 成的碎片（arXiv 的 logo 认成「ar iv」，大模型译成「第4卷」）。
    单独一块（「OK」「No」「Go」「Niv」）不算：可能就是一个短词。
    紧贴着方块字的字母（「x轴」「A股」）也算像词：它和那个字连成一个词。"""
    if CJK_RUN_RE.search(text):
        return False
    chunks = list(LATIN_CHUNK_RE.finditer(text))

    def glued(m: re.Match) -> bool:
        a, b = m.span()
        return (a > 0 and bool(CJK_ONE_RE.match(text[a - 1]))) or (b < len(text) and bool(CJK_ONE_RE.match(text[b])))

    return len(chunks) >= 2 and not any(_word_like(m.group(0)) or glued(m) for m in chunks)


# 人名里的一个词：首字母大写、其余小写（可含连字符、撇号连起来的部分，如 Jean-Luc、O'Neil），或带点的首字母缩写（J.）
# 带重音的拉丁字母也算（José García、Łukasz）：大写含 À-Þ，小写含 ß-ÿ；拉丁扩展 A（Ā-ž）大小写交替，两边都放
_UP = "A-ZÀ-ÖØ-ÞĀ-ž"
_LO = "a-zß-öø-ÿĀ-ž"
_NAME_WORD = (rf"(?:(?:[{_UP}]['’])?[{_UP}][{_LO}]+(?:[{_UP}][{_LO}]+)?(?:[-'’][{_UP}]?[{_LO}]+)*"
              rf"|[{_UP}]\.)")   # 也认 O'Neil、McDonald
_MARKS = "\\d*†‡§¶⁰¹²³⁴-⁹"
_NAME_RE = re.compile(rf"{_NAME_WORD}(?: {_NAME_WORD}){{1,3}}[{_MARKS}]*")
# 人名后的单位标号：「Vaswani1,2」「Shazeer¹」「Parmar*†」——先去掉（标号里的逗号不是人名之间的分隔）
_AFFIL_RE = re.compile(rf"(?<=[{_LO}])[{_MARKS}]+(?:,[{_MARKS}]+)*")
_NAME_SEP_RE = re.compile(r"\s*,\s*(?:(?:and|&)\s+)?|\s+(?:and|&)\s+")
NAME_ITEMS_MIN = 3     # 至少 3 个人名：「Computer Science, Machine Learning」这种两项的学科名与人名形式上分不开
# 学科、栏目名里常见、人名里几乎不出现的词：出现了就不是人名行（「Natural Language Processing, Computer Vision, …」）
NOT_NAME_WORDS = frozenset("""
Science Sciences Engineering Learning Language Languages Computer Computing Computation Machine Data Vision
Intelligence Artificial Processing Network Networks System Systems Theory Analysis Information Retrieval
Mathematics Physics Biology Chemistry Statistics Economics Medicine Research Methods Applications Software
Hardware Security Robotics Graphics Databases Models Model Natural Neural Deep Signal Image Speech Audio
Video Human Social Health Management Education Design Development Introduction Conclusion Results Related
Work Discussion Abstract Background Overview Home About Contact News Products Services Support Help Settings
Search Tools Papers Citations References Privacy Policy Terms Account Sign Log Logic Algorithms Optimization
Control Quantum Finance Business Marketing Studies Technology Technologies University Institute Department
School College Laboratory Lab Center Centre Group Team Project Projects Section Chapter Table Figure
Detection Fusion Attention Recognition Mining Generation Classification Segmentation Estimation Translation
Depression Diagnosis Prediction Representation Representations Transformer Transformers Graph Graphs Multimodal
Reinforcement Supervised Unsupervised Contrastive Federated Adversarial Generative Diffusion Embedding
Embeddings Clustering Regression Inference Reasoning Understanding Tracking Synthesis Modeling Modelling
Evaluation Benchmark Benchmarks Dataset Datasets Survey Review Framework Approach Method
""".split())


def is_name_list(text: str) -> bool:
    """整段是「人名, 人名, …（and 人名）」：至少 NAME_ITEMS_MIN 个，每个 2–4 个首字母大写的词
    （允许末尾的 and/&，人名后的单位标号 1 2 * †），且没有一个词是学科/栏目常用词。论文作者行就是这样。"""
    items = [s for s in _NAME_SEP_RE.split(_AFFIL_RE.sub("", text.strip().rstrip("."))) if s]
    if len(items) < NAME_ITEMS_MIN or not all(_NAME_RE.fullmatch(s) for s in items):
        return False
    return not any(w.rstrip("0123456789*†‡§¶") in NOT_NAME_WORDS for s in items for w in s.split())


def is_list_start(text: str) -> bool:
    return bool(LIST_START_RE.match(text))


def join_lines(texts: list[str]) -> str:
    return _join_lines_at(texts)[0]


def _join_lines_at(texts: list[str]) -> tuple[str, list[int]]:
    """拼好的段落，和每一行（strip 之后）在段落里的起始下标（空行是 -1）。"""
    out = ""
    starts: list[int] = []
    for t in texts:
        t = t.strip()
        if not t:
            starts.append(-1)
            continue
        if not out:
            out = t
            starts.append(0)
        elif out.endswith("-") and len(out) >= 2 and out[-2].isalpha() and t[0].islower():
            starts.append(len(out) - 1)
            out = out[:-1] + t  # 英文断词连字符：depres- + sion
        elif is_cjk_char(out[-1]) or is_cjk_char(t[0]):
            starts.append(len(out))
            out += t
        else:
            starts.append(len(out) + 1)
            out += " " + t
    return out, starts


def _union(boxes: list[Box]) -> Box:
    b = boxes[0]
    for o in boxes[1:]:
        b = b.union(o)
    return b


def _v_overlap(a: Box, b: Box) -> int:
    return min(a.bottom, b.bottom) - max(a.y, b.y)


@dataclass
class _Row:
    """一条视觉行：由若干 OCR 碎片按 x 顺序组成。"""

    parts: list[OcrLine] = field(default_factory=list)

    @property
    def box(self) -> Box:
        """所有碎片的外框：抹原文用它。"""
        return _union([p.box for p in self.parts])

    @property
    def core(self) -> Box:
        """去掉上标/下标碎片后的正文框：判断行高、行距用它。"""
        top = max(p.box.h for p in self.parts)
        return _union([p.box for p in self.parts if p.box.h >= SUP_RATIO * top])

    @property
    def line(self) -> OcrLine:
        return OcrLine(join_words([p.text for p in self.parts]), self.box, tuple(w for p in self.parts for w in p.words))


def _column_head(frag: OcrLine, lines: list[OcrLine]) -> bool:
    """frag 的左边、右边或中线和上下几行里的别的段对齐：像表格的一列（或双栏的另一栏），不是行内碎片。"""
    b = frag.box
    tol = max(2.0, COLUMN_ALIGN * b.h)
    for o in lines:
        ob = o.box
        if o is frag or abs(ob.y - b.y) > COLUMN_REACH * b.h:
            continue
        if (abs(ob.x - b.x) <= tol or abs(ob.right - b.right) <= tol
                or abs((ob.x + ob.right) - (b.x + b.right)) / 2 <= tol):
            return True
    return False


def _gap_clean(arr, left: Box, right: Box) -> bool:
    """两段之间（横向间隙 × 竖直重叠部分）是不是一片干净的底色。"""
    x0, x1 = max(left.right + 1, 0), min(right.x - 1, arr.shape[1])
    y0, y1 = max(left.y, right.y, 0), min(left.bottom, right.bottom, arr.shape[0])
    if x0 >= x1 or y0 >= y1:
        return True
    px = arr[y0:y1, x0:x1].reshape(-1, 3).astype(int)
    return bool((abs(px - np.median(px, axis=0)).max() <= GAP_CLEAN))


def _same_row(row: _Row, frag: OcrLine, lines: list[OcrLine] = (), arr=None) -> bool:
    """arr：截图像素（numpy 数组）。不给就不做放宽合并。"""
    core, b = row.core, frag.box
    h = min(core.h, b.h)
    gap = b.x - row.box.right
    if gap > ROW_GAP * h:
        # 放宽：等高（都不算上标）、竖直居中对齐、不像表格的一列、间隙是干净底色
        centre = abs((core.y + core.bottom) - (b.y + b.bottom)) / 2
        return (arr is not None and gap <= LOOSE_GAP * h and h >= SUP_RATIO * max(core.h, b.h)
                and centre <= LOOSE_CENTER * h and not _column_head(frag, lines)
                and _gap_clean(arr, row.box, b))
    ov = _v_overlap(core, b)
    if h < SUP_RATIO * max(core.h, b.h):
        # 上标/下标：竖直方向沾上就算；它可以落在这一行的横向范围里（OCR 常把正文整行连同上标位置一起框住）
        return ov > 0
    return gap >= -ROW_OVERLAP_MAX * h and ov >= ROW_V_OVERLAP * h


def merge_rows(lines: list[OcrLine], arr=None) -> list[_Row]:
    rows: list[_Row] = []
    for frag in sorted(lines, key=lambda l: (l.box.x, l.box.y)):
        cands = [r for r in rows if _same_row(r, frag, lines, arr)]
        if cands:
            max(cands, key=lambda r: _v_overlap(r.core, frag.box)).parts.append(frag)
        else:
            rows.append(_Row([frag]))
    return rows


def _can_join(block: list[_Row], row: _Row) -> bool:
    last, first, cur = block[-1].core, block[0].core, row.core
    h = max(last.h, cur.h)
    if min(last.h, cur.h) / h < 0.7:
        return False
    gap = cur.y - last.bottom
    if gap < -0.3 * h or gap > 0.8 * h:
        return False
    if abs(cur.x - first.x) > 2 * h and abs(cur.x - last.x) > 2 * h:
        return False
    overlap = min(last.right, cur.right) - max(last.x, cur.x)
    return overlap >= 0.3 * min(last.w, cur.w)


def _is_square(ch: str) -> bool:
    """方块字（汉字、全角标点、假名、谚文）：排版软件在它前后都可以断行。"""
    return is_cjk_char(ch) or bool(SQUARE_RE.match(ch))


def _first_word(text: str) -> str:
    """换行时不可拆的第一个单位（和 render 的分词一致）：以方块字开头就是这一个字；
    否则是开头一串既不是空白、也不是方块字的字符——碰到空白或方块字就停。
    不能用 split()[0]：中日文没有空格，「2026年……」「Python的……」「“导出”……」「はじめに」会把整行当成一个词，
    上一行永远「写满」，短标题就并进下面的正文了。"""
    if _is_square(text[0]):
        return text[0]
    n = 0
    while n < len(text) and not text[n].isspace() and not _is_square(text[n]):
        n += 1
    return text[:n]


def _line_end(arr, which: _Row, right: int, rows: list[_Row]) -> int:
    """which 这一行实际的右端：框右边到 right 之间、在别的 OCR 框（同一行带里右边的下一段）之前，
    要是还有墨迹（OCR 没认出的公式、符号），算到最后一点墨迹为止。
    竖直方向只扫这一行独占的部分：上下相邻的行框和它重叠的那几像素（上一行的下伸字母、下一行的上伸字母）不算。"""
    b = which.box
    stop = right
    for o in rows:
        if o.box.x >= b.right and _v_overlap(o.box, b) > 0:
            stop = min(stop, o.box.x)
    x0, x1 = max(b.right, 0), min(stop, arr.shape[1])
    y0, y1 = max(b.y, 0), min(b.bottom, arr.shape[0])
    for o in rows:
        ob = o.box
        if o is which or ob.right <= x0 or ob.x >= x1 or _v_overlap(ob, b) <= 0:
            continue
        if ob.y < b.y:          # 上面的行伸下来
            y0 = max(y0, ob.bottom)
        elif ob.bottom > b.bottom:   # 下面的行伸上来
            y1 = min(y1, ob.y)
    if x0 >= x1 or y0 >= y1:
        return b.right
    px = arr[y0:y1, x0:x1].astype(np.int16)
    ink = (np.abs(px - np.median(px.reshape(-1, 3), axis=0)).max(axis=2) > GAP_CLEAN).any(axis=0)
    return x0 + int(np.nonzero(ink)[0].max()) + 1 if ink.any() else b.right


def _is_full(block: list[_Row], row: _Row, which: _Row, arr=None, rows: list[_Row] = ()) -> bool:
    """which 这一行写满了没有（按排版软件的换行规则判断）：它的右端到块的右边界剩下的空白，
    放不下 row 的第一个词（西文再加一个空格）——放得下的话，这个词本该排在 which 的行末，
    所以 which 是有意断开的（短行列表、标题）。参差不齐的散文里，某行只到 65% 往往是因为下一行开头是个长词。
    字宽按 row 这一行的「框宽 ÷ 字数」估，再放宽 WRAP_TOL 个字（比例字体里个别词比平均宽）。
    row 没有文字、估不出字宽时，退回「右端到块左边 ≥ 块内最长行宽的 FULL_LINE 倍」。
    arr（截图）给了的话，行的右端按实际墨迹算（_line_end）：OCR 常认不出行末的公式、符号
    （bench 05 的「C ∈ R^H」只认出「C e」，框只到 89%），框外其实还有字，这一行并没有在框的右端结束。"""
    blk = block + [row]
    left = min(r.box.x for r in blk)
    right = max(r.box.right for r in blk)
    text = row.line.text.strip()
    if not text:
        return which.box.right - left >= FULL_LINE * (right - left)
    cw = row.box.w / len(text)
    word = _first_word(text)
    need = len(word) * cw + (0 if _is_square(word[0]) else cw)
    end = which.box.right if arr is None else _line_end(arr, which, right, rows)
    return right - end < need + WRAP_TOL * cw


def _is_label(row: _Row, rows: list[_Row]) -> bool:
    """「标签」行：同一行带里、右边 LABEL_GAP 倍行高以内紧挨着一段至少 LABEL_WIDTH 倍宽的值
    （「Subjects」右边的值、表格同一行的下一格），而且标签本身要像个标签：以冒号结尾，
    或者短到不超过值的 1/LABEL_SHORT。双栏的左右两栏一样宽不算；1:2.5 的窄栏正文行也不算（不然窄栏会被拆碎）。"""
    b = row.core
    colon = row.line.text.rstrip().endswith((":", "："))
    for o in rows:
        if o is row:
            continue
        ob = o.core
        h = max(b.h, ob.h)
        gap = ob.x - b.right
        if (0 <= gap <= LABEL_GAP * h and _v_overlap(b, ob) >= ROW_V_OVERLAP * min(b.h, ob.h)
                and o.box.w >= LABEL_WIDTH * row.box.w and (colon or o.box.w >= LABEL_SHORT * row.box.w)):
            return True
    return False


def _continues(block: list[_Row], row: _Row, rows: list[_Row], arr=None) -> bool:
    """row 能不能接在 block 后面成段：几何上相邻（_can_join），上一行写满了，且 row 不是「标签」。"""
    return _can_join(block, row) and _is_full(block, row, block[-1], arr, rows) and not _is_label(row, rows)


BAND_GAP = 0.5        # 横着切开两个「行带」所需的最小空隙（单位：行高中位数）


def _split(items: list[int], lo, hi, min_gap: float) -> list[list[int]]:
    """按 lo 排序后扫描投影：和当前组之间的空隙不到 min_gap 的并进同一组（可传递）。"""
    groups: list[list[int]] = []
    end = 0
    for i in sorted(items, key=lo):
        if groups and lo(i) - end < min_gap:
            groups[-1].append(i)
            end = max(end, hi(i))
        else:
            groups.append([i])
            end = hi(i)
    return groups


def reading_order(boxes: list[Box], unit: float) -> list[int]:
    """返回块的阅读顺序（下标），递归 XY 切分：
    1. 先找横贯全部块、高度 ≥ BAND_GAP × 行高的水平空隙，切成自上而下的几个行带；
    2. 切不动就找竖直空隙（任意宽度），切成从左到右的几栏；
    3. 都切不动就按 (y, x) 排。
    这样表格按行读（行间空隙横贯整张表），双栏按栏读（两栏的段落间隙偶尔对齐一两个像素不算数）。"""
    def xy(idx: list[int]) -> list[int]:
        if len(idx) <= 1:
            return idx
        for groups in (_split(idx, lambda i: boxes[i].y, lambda i: boxes[i].bottom, BAND_GAP * unit),
                       _split(idx, lambda i: boxes[i].x, lambda i: boxes[i].right, 1)):
            if len(groups) > 1:
                return [i for g in groups for i in xy(g)]
        return sorted(idx, key=lambda i: (boxes[i].y, boxes[i].x))

    return xy(list(range(len(boxes))))


def group_lines(lines: list[OcrLine], mode: str, image: Image.Image | None = None) -> list[Segment]:
    """image：识别用的截图。给了才做「行内碎片放宽合并」（要看两段之间是不是干净底色）。"""
    if mode not in ("para", "line"):
        raise ValueError(f"未知的分组模式：{mode}")
    arr = np.asarray(image.convert("RGB")) if image is not None else None
    rows = sorted(merge_rows(lines, arr), key=lambda r: (r.core.y, r.core.x))
    blocks: list[list[_Row]] = []
    formula: list[bool] = []
    for row in rows:
        text = row.line.text
        is_f = is_formula(text)
        target = None
        if mode == "para" and not is_f and not is_list_start(text):
            best = None
            for i, b in enumerate(blocks):
                if formula[i] or not _continues(b, row, rows, arr):
                    continue
                gap = row.core.y - b[-1].core.bottom
                if best is None or gap < best[0]:
                    best = (gap, i)
            if best is not None:
                target = best[1]
        if target is None:
            blocks.append([row])
            formula.append(is_f)
        else:
            blocks[target].append(row)
    heights = sorted(r.core.h for r in rows)
    unit = heights[len(heights) // 2] if heights else 0
    order = reading_order([_union([r.box for r in b]) for b in blocks], unit)
    segs = []
    for n, i in enumerate(order, 1):
        ls = [r.line for r in blocks[i]]
        text = join_lines([l.text for l in ls])
        # 公式、logo 碎片、作者行：不翻、不抹，保持原样
        skip = formula[i] or is_noise(text) or is_name_list(text)
        pills = () if skip or arr is None else _segment_pills(arr, ls, text)
        segs.append(Segment(idx=n, text=text, lines=ls, skip=skip, pills=pills))
    return segs


def _segment_pills(arr, lines: list[OcrLine], text: str) -> tuple[Pill, ...]:
    """这一段里的行内代码胶囊。整段的词全在胶囊里（独立的按钮、选中的列表项、整行高亮）不算：那是界面元素，照常翻；
    胶囊文字不是段落原文的子串（行末连字符拼词时改了字）的也不要——占位符替换不了它。"""
    pills: list[Pill] = []
    covered = total = 0
    _, starts = _join_lines_at([l.text for l in lines])
    for ln, ls in zip(lines, starts):
        found = find_pills(arr, ln)
        for p in found:
            first = next((k for k, w in enumerate(ln.words) if _centre_in(w.box, p.box)), 0)
            pos = _pill_offset(ln.text.strip(), p.text, len(join_words([w.text for w in ln.words[:first]])))
            start = ls + pos if pos is not None and ls >= 0 else None
            if start is not None and text[start:start + len(p.text)] != p.text:
                start = None
            pills.append(replace(p, start=start))
        total += len(ln.words)
        covered += sum(1 for w in ln.words if any(_centre_in(w.box, p.box) for p in found))
    if covered >= total:
        return ()
    return tuple(p for p in pills if p.text and p.text in text)


def _centre_in(wb: Box, b: Box) -> bool:
    return b.contains(wb.x + wb.w / 2, wb.y + wb.h / 2)


def _pill_offset(line_text: str, pill_text: str, approx: int) -> int | None:
    """胶囊文字在这一行里的起始下标：行里可能有好几处同样的字，取离 approx（胶囊前面那几个词拼起来的长度）最近的一处。"""
    hits = [m.start() for m in re.finditer(re.escape(pill_text), line_text)] if pill_text else []
    return min(hits, key=lambda s: abs(s - approx)) if hits else None

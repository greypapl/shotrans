"""把译文画回截图：取色、抹原文、排字。纯 Pillow，不碰界面。

排字（Task 19）：
1. 字号：每行按字形类别从 OCR 框高估出原文字号（estimate_em），全图相近的字号归成一类，同类用同一个起始字号；
2. 放不下先延伸再缩：先在原框里排；放不下就向右、向下延伸到真正的空白处（不压别的段、不压任何非底色像素）；
   仍放不下才逐步缩小，下限 = max(MIN_PX, 该类字号 × MIN_SCALE)；到下限仍放不下才截断加「…」；
3. 目标字体缺字（如 Segoe UI 没有全角标点、中文、⇄）时按字符换用后备字体，不画「□」；
4. 译文和原文相同（去空白、不分大小写）就不抹不画，原图保持原样；
5. 行内代码胶囊（Task 22）：知道胶囊在译文里的位置（SegmentResult.pill_spans）时，不重画胶囊里的字，
   而是把原图上那块胶囊（连底色带描边）剪下来，当一个不可拆的「字」嵌回译文排版里（见 PillFont）。"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache, partial

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .models import Box, OcrLine, Pill, Segment
from .protect import fix_key_combos

FONT_ZH = r"C:\Windows\Fonts\msyh.ttc"
FONT_EN = r"C:\Windows\Fonts\segoeui.ttf"
FONT_SYMBOL = r"C:\Windows\Fonts\seguisym.ttf"
# 目标字体缺字时依次换用的后备字体
FALLBACKS = {FONT_EN: (FONT_ZH, FONT_SYMBOL), FONT_ZH: (FONT_EN, FONT_SYMBOL)}
MIN_PX = 9
PAD = 2              # 抹原文时向外多抹几像素
RING = 3             # 取背景色的外圈宽度
FG_DIST = 80         # 和背景差这么多才算文字像素
NOISY_NEAR = 30      # 外圈像素离中位色这么近才算「同色」
NOISY_FRACTION = 0.8 # 同色比例低于这个就算背景杂乱
LINE_SPACING = 1.25
MIN_CONTRAST = 4.5
FG_CORE = 0.3        # 文字像素里只取离背景最远的这 30% 求中位数：避开 ClearType 彩边和抗锯齿过渡色
# 起始字号 = 行高中位数 × PX_PER_LINE_H。OCR 的框贴着字形（框高 ≈ 字形墨迹高 + 1），比字号略大。
# 标定（2026-10，本机 Windows OCR）：用 segoeui / msyh 在 11~28 号各画 4 句一行字交给 recognize()，
# 量得 字号/框高：segoeui 中位数 0.952（0.923~1.0，36 例），msyh 中位数 1.0（0.75~1.077，35 例），
# 合并中位数 0.952。取 0.95：同语言原样重画时字形高度与原文相差不超过 1 像素（见测试）。
PX_PER_LINE_H = 0.95
# ---- 按字形类别估字号（Task 19）----
# OCR 框贴着墨迹：「Demos」没有下伸字母，框高只有 10；「Bibliographic Tools」有 g p，框高 13，其实是同一字号。
# 所以把框高看成「墨迹跨度 × 字号」，墨迹跨度（单位：字号）按这一行里出现的字形类别相加：
#   只有 x 高度的小写字母 SPAN_X；有上伸（大写、数字、b d f h i k l t 等）加 SPAN_ASC；
#   有下伸（g j p q y、括号）加 SPAN_DESC；只有逗号、分号这种浅下伸加 SPAN_COMMA；
#   有汉字、假名、韩文就是方块字 SPAN_CJK；一个字母数字都没有（只有符号、图标）的行估不出，不参与。
#   标定脚本：tools/calibrate_em.py。
# 标定（2026-10-12，本机 Windows OCR，segoeui / arial / msyh 三种字体，11~27 号，每类 4 句，共约 100 例/类）：
#   字号/框高中位数：只有 x 高度 1.667（→ 跨度 0.60）；只有上伸 1.227（→ 0.82）；只有下伸 1.20（→ 0.83）；
#   上伸+下伸 1.0 / 0.913 / 1.0（→ 1.05，与 Task 7b 的 0.95 一致）；汉字 0.95（→ 1.05）；
#   x 高度+逗号 1.389（→ 0.72）；上伸+逗号 1.056（→ 0.95）。各类加起来与实测相差都在 3% 以内。
SPAN_X = 0.60
SPAN_ASC = 0.22
SPAN_DESC = 0.23
SPAN_COMMA = 0.12
SPAN_CJK = round(1 / PX_PER_LINE_H, 4)
ASC_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789bdfhiklt()[]{}|/\\!?'\"#$%&@^*")
DESC_CHARS = set("gjpqy()[]{}|")
COMMA_CHARS = set(",;")
# 方块字：汉字、日文假名、韩文（谚文音节与字母）——都按 SPAN_CJK 估
SQUARE_RE = re.compile(r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿"
                       r"ᄀ-ᇿ㄰-㆏가-힣]")
SIZE_CLASS_TOL = 1.12   # 全图各段的字号从小到大排，和前一个相差不超过 12% 归成一类，同类用同一个起始字号（该类中位数）
SIZE_CLASS_SPAN = 1.25  # 但同一类最大与最小也不超过 25%：免得 12、13、14、15… 一路相邻都近而连成一大类
MIN_SCALE = 0.75        # 缩小的下限：该类字号的 75%（且不小于 MIN_PX）
EXT_GAP = 0.5           # 向空白处延伸时和障碍（别的段、非底色像素、图像边缘）留的间距（单位：起始字号）
MARK_PX = 6                 # 「这段带说明」角标：右上角直角三角形的边长
MARK_COLOR = (245, 158, 11)  # 琥珀色

RGB = tuple[int, int, int]


@dataclass
class Placement:
    idx: int
    box: Box
    px: int
    lines: list[str]
    fg: RGB
    bg: RGB
    truncated: bool
    area: Box | None = None   # 译文实际占的区域（可能延伸出 box 到右侧/下方的空白）
    pills: int = 0            # 贴回的胶囊图个数（lines 里那几处写的是胶囊文字）


@dataclass
class RenderResult:
    image: Image.Image
    placements: dict[int, Placement]


@dataclass
class Fit:
    px: int
    lines: list[str]
    truncated: bool
    width: int | None = None   # 排进的区域宽度（胶囊比一行还宽时按它等比缩小）


def _luminance(c: RGB) -> float:
    def ch(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGB, b: RGB) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def ensure_contrast(fg: RGB, bg: RGB) -> RGB:
    if contrast_ratio(fg, bg) >= MIN_CONTRAST:
        return fg
    black, white = (0, 0, 0), (255, 255, 255)
    return black if contrast_ratio(black, bg) >= contrast_ratio(white, bg) else white


def _clip(box: Box, w: int, h: int, pad: int = 0) -> tuple[int, int, int, int]:
    return max(box.x - pad, 0), max(box.y - pad, 0), min(box.right + pad, w), min(box.bottom + pad, h)


def sample_bg(arr: np.ndarray, box: Box, exclude: Iterable[Box] = ()) -> tuple[RGB, bool]:
    """外圈取底色。exclude：外圈里不算的区域（行内代码胶囊的底色不是段落的底色）；去掉后外圈空了就不去。"""
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = _clip(box, w, h, RING)
    ix0, iy0, ix1, iy1 = _clip(box, w, h)
    mask = np.ones((y1 - y0, x1 - x0), dtype=bool)
    mask[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = False
    kept = mask.copy()
    for e in exclude:
        ex0, ey0, ex1, ey1 = _clip(e, w, h, 1)
        kept[max(ey0 - y0, 0):max(ey1 - y0, 0), max(ex0 - x0, 0):max(ex1 - x0, 0)] = False
    if kept.any():
        mask = kept
    ring = arr[y0:y1, x0:x1][mask]
    if ring.size == 0:
        ring = arr[iy0:iy1, ix0:ix1].reshape(-1, 3)
    med = np.median(ring, axis=0)
    near = np.abs(ring.astype(int) - med).max(axis=1) <= NOISY_NEAR
    return tuple(int(v) for v in med), bool(near.mean() < NOISY_FRACTION)


def sample_fg(arr: np.ndarray, box: Box, bg: RGB) -> RGB:
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = _clip(box, w, h)
    px = arr[y0:y1, x0:x1].reshape(-1, 3).astype(int)
    diff = np.abs(px - np.array(bg))
    far, dist = px[diff.max(axis=1) > FG_DIST], diff.sum(axis=1)[diff.max(axis=1) > FG_DIST]
    if len(far) == 0:
        return ensure_contrast(bg, bg)
    # 按三个通道的总差排序：字芯（三个通道都远离背景）排在彩边（只有一两个通道远离）前面
    k = max(1, int(np.ceil(len(far) * FG_CORE)))
    core = far[np.argsort(-dist, kind="stable")[:k]]
    return tuple(int(v) for v in np.median(core, axis=0))


_TOKEN_RE = re.compile(
    r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]|[^\s\u3000-\u303f\u3400-\u9fff\uff00-\uffef]+\s*|\s+"
)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    """中文按字、英文按词换行；单个词比一行还宽就硬切。

    效果等同于「逐个加词，加到放不下就换行」，但不逐个量：一行能放几个词先按上一行的个数猜，
    再倍增/二分找到「加上第 k 个词还放得下、加上第 k+1 个就放不下」的 k（前缀越长越宽）。
    每行只量几次，长译文在多个字号下反复折行时快得多（测宽是排字里最贵的一步）。"""
    toks = _TOKEN_RE.findall(text)
    n = len(toks)
    lines: list[str] = []
    cur = ""
    i = 0
    guess = 8

    def fits(k: int) -> bool:   # cur 后面接上 toks[i:k] 放不放得下
        return font.getlength((cur + "".join(toks[i:k])).rstrip()) <= max_w

    while i < n:
        if fits(i + 1):
            lo, hi = i + 1, None          # lo：已知放得下；hi：已知放不下
            k = min(n, i + guess)
            if k > lo:
                if fits(k):
                    lo = k
                else:
                    hi = k
            if hi is None and lo < n:
                if fits(lo + 1):           # 猜对了（再多一个就放不下）时只多量这一次
                    lo += 1
                    step = max(1, guess)
                    while lo < n:
                        k = min(n, lo + step)
                        if fits(k):
                            lo = k
                            step *= 2
                        else:
                            hi = k
                            break
                else:
                    hi = lo + 1
            if hi is not None:
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    if fits(mid):
                        lo = mid
                    else:
                        hi = mid
            guess = max(1, lo - i)
            cur += "".join(toks[i:lo])
            i = lo
            if i == n:
                break
        tok = toks[i]      # 这个词接不上了：换行
        i += 1
        if cur.strip():
            lines.append(cur.rstrip())
        cur = tok.lstrip()
        while cur and font.getlength(cur.rstrip()) > max_w:
            cut = max(len(cur) - 1, 1)  # 至少切一个字，否则单字比行还宽时死循环
            while cut > 1 and font.getlength(cur[:cut]) > max_w:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


@lru_cache(maxsize=256)
def _truetype(path: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, px)


GLYPH_PROBE_PX = 32
DESC_PROBE = "gjpqy"          # 主字体最低的下伸字母：后备字体的墨迹比它们还低才加大行距
NOTDEF_PROBE = "\U000f0000"   # 补充私用区的字：系统字体都没有，画出来就是该字体的「缺字框」


@lru_cache(maxsize=None)
def _notdef(path: str) -> tuple:
    f = _truetype(path, GLYPH_PROBE_PX)
    m = f.getmask(NOTDEF_PROBE)
    return m.size, bytes(m)


@lru_cache(maxsize=4096)
def has_glyph(path: str, ch: str) -> bool:
    """这个字体能不能画 ch：画出来和「缺字框」一模一样就是没有（空白画出来是空的，自然算有）。"""
    m = _truetype(path, GLYPH_PROBE_PX).getmask(ch)
    return (m.size, bytes(m)) != _notdef(path)


_COVERAGE: dict[str, tuple[set[str], set[str]]] = {}   # 字体 -> (有字形的字, 缺字形的字)，逐字缓存


def covers(path: str, text: str) -> bool:
    """这个字体能不能画 text 里的每个字。按字缓存：常见的整串都在主字体里时只做一次集合比较。"""
    good, bad = _COVERAGE.setdefault(path, (set(), set()))
    chars = set(text)
    if chars <= good:
        return True
    for c in chars - good - bad:
        (good if has_glyph(path, c) else bad).add(c)
    return chars <= good


class MixedFont:
    """主字体 + 后备字体：逐字符挑第一个有字形的字体，测宽、画字都按片段分别用各自的字体。
    提供和 FreeTypeFont 一样的 getlength，wrap_text 可以直接用。
    快路径：整串都在主字体里（绝大多数情况）就直接用主字体，不逐字切片段。"""

    def __init__(self, path: str, px: int):
        self.paths = (path, *FALLBACKS.get(path, ()))
        self.px = px
        self.primary = _truetype(path, px)

    def _pick(self, ch: str) -> str:
        for p in self.paths:
            if has_glyph(p, ch):
                return p
        return self.paths[0]

    def runs(self, text: str) -> list[tuple[ImageFont.FreeTypeFont, str]]:
        if covers(self.paths[0], text):
            return [(self.primary, text)] if text else []
        out: list[list] = []
        for ch in text:
            p = self._pick(ch)
            if out and out[-1][0] == p:
                out[-1][1] += ch
            else:
                out.append([p, ch])
        return [(_truetype(p, self.px), s) for p, s in out]

    def getlength(self, text: str) -> float:
        return sum(f.getlength(s) for f, s in self.runs(text))

    def line_height(self, text: str) -> int:
        """行距 = 字号 × LINE_SPACING；text 里有后备字体的片段、且它按基线对齐后的墨迹下沿比主字体最低的
        下伸字母（gjpqy）还低时，行距加上低出的那几像素——小字号时取整后行距只比字高两三像素，
        后备字体的「，」「∯」「⟨」会碰到下一行。"""
        base = round(self.px * LINE_SPACING)
        low = [f.getbbox(s, anchor="ls")[3] for f, s in self.runs(text) if f is not self.primary]
        if not low:
            return base
        return base + max(0, max(low) - self.primary.getbbox(DESC_PROBE, anchor="ls")[3])

    def draw(self, draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, fill: RGB) -> None:
        x, y = xy
        baseline = y + self.primary.getmetrics()[0]   # 后备字体的片段对齐主字体的基线
        for f, s in self.runs(text):
            if f is self.primary:
                draw.text((x, y), s, font=f, fill=fill)
            else:
                draw.text((x, baseline), s, font=f, fill=fill, anchor="ls")
            x += f.getlength(s)


def _font(path: str, px: int) -> MixedFont:
    return MixedFont(path, px)


# ---------- 行内代码胶囊：原图剪下来嵌回译文（Task 22）----------

PILL_BASE = 0xE000      # 排版时译文里第 k 个胶囊换成私用区字符 U+E000+k：折行、截断都把它当一个不可拆的字
PILL_LIMIT = 0x100      # 最多这么多个（一段里的胶囊远少于此）
PILL_GAP_WIDE = 0.25    # 胶囊和紧挨着的汉字（全角标点、另一个胶囊）之间的间隙，单位：字号
PILL_GAP_NARROW = 0.1   # 和紧挨着的英文字母、半角标点之间：胶囊自己有内边距，留一点就够；挨着空格不再加
PILL_LINE_GAP = 2       # 有胶囊的行和上下行之间至少留这么多像素（胶囊常比字高）
PILL_CROP_PAD = 1       # 剪图时向外多剪 1 像素：pills.py 的框是色带里面，不含 1 像素的描边
_WIDE_RE = re.compile(r"[　-〿＀-￯]")


def _is_pill(ch: str) -> bool:
    return PILL_BASE <= ord(ch) < PILL_BASE + PILL_LIMIT


def _has_pill_chars(text: str) -> bool:
    return any(_is_pill(c) for c in text)


class PillFont:
    """译文里嵌着胶囊小图的「字体」：和 MixedFont 一样提供 getlength / line_height，wrap_text、缩字号、截断都直接可用。
    文字片段交给 base（MixedFont）；每个私用区字符 U+E000+k 是第 k 个胶囊的图，宽 = 图宽 × scale
    （max_w 给了且更宽时再等比缩到 max_w：比一整行还宽的胶囊单独占一行）。
    胶囊和紧挨着的非空白字之间留间隙（PILL_GAP_*），量宽和画都按同一套规则。"""

    def __init__(self, base: MixedFont, images: Mapping[int, Image.Image], scale: float,
                 max_w: int | None = None, cache: dict | None = None):
        self.base = base
        self.px = base.px
        self.primary = base.primary
        self.images = images
        self.scale = scale
        self.max_w = max_w
        self._cache = {} if cache is None else cache   # 缩放后的图：(k, 宽, 高) -> Image，同一段的各字号共用

    def within(self, max_w: int) -> "PillFont":
        return PillFont(self.base, self.images, self.scale, max(1, max_w), self._cache)

    def size(self, k: int) -> tuple[int, int]:
        w, h = self.images[k].size
        if self.scale != 1:
            w, h = max(1, round(w * self.scale)), max(1, round(h * self.scale))
        if self.max_w is not None and w > self.max_w:
            w, h = self.max_w, max(1, round(h * self.max_w / w))
        return w, h

    def image(self, k: int) -> Image.Image:
        im = self.images[k]
        w, h = self.size(k)
        if (w, h) == im.size:
            return im                       # 字号没缩：原图 1:1，不重采样
        key = (k, w, h)
        if key not in self._cache:
            self._cache[key] = im.resize((w, h), Image.LANCZOS)
        return self._cache[key]

    @staticmethod
    def _items(text: str) -> list[str | int]:
        """切成文字片段（str）和胶囊（int，第几个）。"""
        out: list[str | int] = []
        start = 0
        for i, ch in enumerate(text):
            if _is_pill(ch):
                if i > start:
                    out.append(text[start:i])
                out.append(ord(ch) - PILL_BASE)
                start = i + 1
        if start < len(text):
            out.append(text[start:])
        return out

    def _gap(self, prev: str | int, cur: str | int) -> float:
        """相邻两项（至少一个是胶囊）之间的间隙。"""
        a = prev[-1] if isinstance(prev, str) else None
        b = cur[0] if isinstance(cur, str) else None
        if (a is not None and a.isspace()) or (b is not None and b.isspace()):
            return 0.0
        other = a if a is not None else b
        wide = other is None or bool(SQUARE_RE.match(other) or _WIDE_RE.match(other))
        return (PILL_GAP_WIDE if wide else PILL_GAP_NARROW) * self.px

    def _length(self, text: str, gaps: bool) -> float:
        total, prev = 0.0, None
        for it in self._items(text):
            if gaps and prev is not None:
                total += self._gap(prev, it)
            total += self.base.getlength(it) if isinstance(it, str) else self.size(it)[0]
            prev = it
        return total

    def getlength(self, text: str) -> float:
        return self._length(text, True)

    def min_length(self, text: str) -> float:
        """不算间隙的宽度：估「至少几行」用，只能估小不能估大。"""
        return self._length(text, False)

    def line_height(self, text: str) -> int:
        """文字的行距（同 MixedFont）；胶囊行更高由 _stack 另算。"""
        return self.base.line_height(text)

    def pill_height(self, line: str) -> int:
        """这一行里最高的胶囊图的高度；没有胶囊为 0。"""
        return max((self.size(ord(c) - PILL_BASE)[1] for c in line if _is_pill(c)), default=0)

    def tallest(self) -> int:
        return max((self.size(k)[1] for k in self.images), default=0)

    def mid(self) -> float:
        """文字的竖直中线相对画字起点（左上角）的偏移：胶囊的中线对齐它。"""
        probe = "中" if has_glyph(self.base.paths[0], "中") else "H"
        top, bottom = self.primary.getbbox(probe)[1::2]
        return (top + bottom) / 2

    def draw(self, draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, fill: RGB,
             canvas: Image.Image) -> None:
        x, y = xy
        cy = y + self.mid()
        prev = None
        for it in self._items(text):
            if prev is not None:
                x += self._gap(prev, it)
            if isinstance(it, str):
                self.base.draw(draw, (x, y), it, fill)
                x += self.base.getlength(it)
            else:
                im = self.image(it)
                canvas.paste(im, (round(x), round(cy - im.height / 2)))
                x += im.width
            prev = it


AnyFont = MixedFont | PillFont


def _stack(font: AnyFont, lines: list[str], line_h: int) -> tuple[list[int], int, int]:
    """(各行字顶相对第一行字顶的偏移, 第一行胶囊伸出字顶多少, 最后一行胶囊伸出字底多少)。
    没有胶囊：行距一律 line_h（和以前一样）。有胶囊的行按胶囊实际画的位置算（和 PillFont.draw 一样：
    胶囊中线在字顶下 mid() 处，粘贴时取整最多差半像素）：胶囊伸出字顶 up = h/2 − mid，伸出字底 down = mid + h/2 − 字号；
    相邻两行：上一行最低处（字底或胶囊底）和下一行最高处之间至少留 PILL_LINE_GAP，且行距不小于 line_h。
    排得下排不下只看字（最后一行字底 = 偏移 + 字号）：首末行胶囊伸出的那一点和原图上胶囊伸出 OCR 行框一样，
    不算——否则单行段落的胶囊永远放不进原框；伸出量另外报给调用者（算 area）。"""
    px = font.px
    if not isinstance(font, PillFont) or not lines:
        return [i * line_h for i in range(len(lines))], 0, 0
    mid = font.mid()
    hs = [font.pill_height(l) for l in lines]
    ups = [max(0, math.ceil(h / 2 - mid + 0.5)) if h else 0 for h in hs]
    downs = [max(0, math.ceil(mid + h / 2 + 0.5 - px)) if h else 0 for h in hs]
    tops = [0]
    for i in range(1, len(lines)):
        if not hs[i - 1] and not hs[i]:
            d = line_h
        else:
            d = max(line_h, px + downs[i - 1] + ups[i] + PILL_LINE_GAP)
        tops.append(tops[-1] + d)
    return tops, ups[0], downs[-1]


def _text_h(font: AnyFont, lines: list[str], line_h: int, px: int) -> int:
    """排得下排不下用的高度：第一行字顶到最后一行字底（没有胶囊时就是 (行数 − 1) × line_h + px，和以前一样）。"""
    tops = _stack(font, lines, line_h)[0]
    return tops[-1] + px if tops else px - line_h


def _lines_if_fit(text: str, font: AnyFont, px: int, box_w: int, box_h: int, wrap: bool) -> list[str] | None:
    if isinstance(font, PillFont):
        font = font.within(box_w)
    line_h = font.line_height(text)
    if wrap:
        # 先用「去掉空白后的总宽 ÷ 行宽」估至少要几行（折出的每行去掉空白后都不宽于行宽，所以只会估少不会估多）：
        # 这么多行都超高就不必真去折行——缩字号时大多数字号在这一步就被排除
        dense = "".join(text.split())
        dense_w = font.min_length(dense) if isinstance(font, PillFont) else font.getlength(dense)
        at_least = max(1, int(np.ceil(dense_w / max(box_w, 1))))
        if (at_least - 1) * line_h + px > box_h + 2 * PAD:
            return None
    lines = wrap_text(text, font, box_w) if wrap else [text]
    height = _text_h(font, lines, line_h, px)
    if all(font.getlength(l) <= box_w for l in lines) and height <= box_h + 2 * PAD:
        return lines
    return None


def fit_text(text: str, font_path: str, box_w: int, box_h: int, start_px: int, wrap: bool,
             min_px: int = MIN_PX) -> Fit:
    """从 start_px 往下逐号试，放得下就用；到 min_px 仍放不下就按 min_px 截断加「…」。"""
    min_px = max(min_px, MIN_PX)
    for px in range(max(start_px, min_px), min_px - 1, -1):
        lines = _lines_if_fit(text, _font(font_path, px), px, box_w, box_h, wrap)
        if lines is not None:
            return Fit(px, lines, False, box_w)
    return _truncate(text, partial(_font, font_path), box_w, box_h, min_px, wrap)


def _truncate(text: str, make: Callable[[int], AnyFont], box_w: int, box_h: int, min_px: int, wrap: bool) -> Fit:
    """按 min_px 排，放不下的部分截掉，末尾加「…」。胶囊是一个字：要么整个留下，要么整个截掉。"""
    font = make(min_px)
    if isinstance(font, PillFont):
        font = font.within(box_w)
    line_h = font.line_height(text)
    lines = wrap_text(text, font, box_w) if wrap else [text]
    max_lines = max(1, (box_h + 2 * PAD - min_px) // line_h + 1)
    lines = lines[:max_lines]
    while len(lines) > 1 and _text_h(font, lines, line_h, min_px) > box_h + 2 * PAD:   # 有胶囊的行更高
        lines.pop()
    last = lines[-1]
    while last and font.getlength(last + "…") > box_w:
        last = last[:-1]
    lines[-1] = last + "…"
    return Fit(min_px, lines, True, box_w)


# ---------- 字号估计与全图归类 ----------

def line_span(text: str) -> float | None:
    """这一行墨迹的竖直跨度，单位是字号（见 SPAN_* 的标定）。
    一个字母、数字、方块字都没有（只有图标、箭头、项目符号被认成的符号）时返回 None：估不出。"""
    if SQUARE_RE.search(text):
        return SPAN_CJK
    if not any(c.isalnum() for c in text):
        return None
    asc = desc = comma = False
    for c in text:
        if c in ASC_CHARS or (c.isalpha() and not c.isascii()):
            asc = True
        if c in DESC_CHARS:
            desc = True
        elif c in COMMA_CHARS:
            comma = True
    return SPAN_X + (SPAN_ASC if asc else 0) + (SPAN_DESC if desc else SPAN_COMMA if comma else 0)


def estimate_em(line: OcrLine) -> float | None:
    """由 OCR 框高估原文字号（像素）。只有符号的行估不出，返回 None。"""
    span = line_span(line.text)
    return None if span is None else line.box.h / span


def _lower_median(values: list[float]) -> float:
    """偶数个取偏小的那个：OCR 框只会被图标、上标、并进来的碎片撑大，不会变小，偏小的更可信。"""
    s = sorted(values)
    return s[(len(s) - 1) // 2]


def start_sizes(segments: list[Segment]) -> dict[int, int]:
    """每段的起始字号：段内各行估计字号的中位数（只有符号的行不算），再在全图归类——从小到大排，
    和前一个相差超过 SIZE_CLASS_TOL、或和本类最小的相差超过 SIZE_CLASS_SPAN 就另起一类，同类各段一律用该类的中位数。
    按相邻间隙分类：同字号的 ±1 像素误差不会因为本类最小值偏小而被切开。
    公式段不参与；整段都只有符号的不参与归类，按自己的框高 × PX_PER_LINE_H。"""
    ems: dict[int, float] = {}
    out: dict[int, int] = {}
    for s in segments:
        if not s.lines or s.skip:
            continue
        known = [e for e in (estimate_em(l) for l in s.lines) if e is not None]
        if known:
            ems[s.idx] = _lower_median(known)
        else:
            out[s.idx] = max(1, round(_lower_median([l.box.h for l in s.lines]) * PX_PER_LINE_H))
    cluster: list[int] = []

    def close() -> None:
        px = round(_lower_median([ems[i] for i in cluster]))
        out.update({i: px for i in cluster})

    for i in sorted(ems, key=lambda k: ems[k]):
        if cluster and (ems[i] > ems[cluster[-1]] * SIZE_CLASS_TOL or ems[i] > ems[cluster[0]] * SIZE_CLASS_SPAN):
            close()
            cluster = []
        cluster.append(i)
    if cluster:
        close()
    return out


# ---------- 向空白处延伸 ----------

SCAN_CHUNK = 32   # 找空白时一块一块地扫（每块翻倍），碰到障碍就停：开销只和到第一个障碍的距离成正比


def _boxes_array(boxes: list[Box]) -> np.ndarray:
    """别的段的框（各向外扩 PAD），(n, 4) 的 x0 y0 x1 y1：每次扫描用向量运算挑出和扫描窗口相交的几个。"""
    if not boxes:
        return np.zeros((0, 4), dtype=np.int64)
    return np.array([(b.x - PAD, b.y - PAD, b.right + PAD, b.bottom + PAD) for b in boxes], dtype=np.int64)


def _dirty(img: Image.Image, x0: int, y0: int, x1: int, y1: int, bg: RGB, others: np.ndarray) -> np.ndarray:
    """区域里哪些像素不能占：和底色差得多（任何墨迹、边框、图标、已画的译文）或落在别的段的框里。"""
    crop = np.asarray(img.crop((x0, y0, x1, y1)), dtype=np.int16)
    mask = np.abs(crop - np.array(bg, dtype=np.int16)).max(axis=2) > NOISY_NEAR
    hit = others[(others[:, 0] < x1) & (others[:, 2] > x0) & (others[:, 1] < y1) & (others[:, 3] > y0)]
    for bx0, by0, bx1, by1 in hit.tolist():
        mask[max(by0, y0) - y0:min(by1, y1) - y0, max(bx0, x0) - x0:min(bx1, x1) - x0] = True
    return mask


def _first_dirty(img: Image.Image, bg: RGB, others: np.ndarray, start: int, stop: int,
                 across: tuple[int, int], horizontal: bool) -> int:
    """从 start 向 stop 逐列（horizontal）或逐行扫，across 是另一个方向的范围。
    返回第一条有障碍的列/行相对 start 的偏移；一直没有就返回 stop - start。"""
    pos, step = start, SCAN_CHUNK
    while pos < stop:
        end = min(pos + step, stop)
        if horizontal:
            flags = _dirty(img, pos, across[0], end, across[1], bg, others).any(axis=0)
        else:
            flags = _dirty(img, across[0], pos, across[1], end, bg, others).any(axis=1)
        if flags.any():
            return pos - start + int(np.argmax(flags))
        pos, step = end, step * 2
    return stop - start


def _room_right(img: Image.Image, box: Box, bg: RGB, others: np.ndarray, gap: int, reach: int | None) -> int:
    """向右可延伸到的右边界（已减去间距 gap，不小于原框）。reach：从框左边算最多用得上多宽，更远的不必扫。"""
    w, h = img.size
    y0, y1 = max(box.y, 0), min(box.bottom, h)
    if box.right >= w or y0 >= y1:
        return box.right
    # 多扫一个 gap：扫到 reach + gap 仍没碰到障碍时，真实结果（障碍 − gap）也一定 ≥ reach，用不上的部分不影响排字
    stop = w if reach is None else min(w, max(box.right, box.x + reach + gap))
    n = _first_dirty(img, bg, others, box.right, stop, (y0, y1), True)
    right = box.right + n - (gap if n < stop - box.right else 0)
    return max(min(right, w - gap), box.right)


def _room_down(img: Image.Image, box: Box, bg: RGB, others: np.ndarray, gap: int, x_end: int,
               reach: int | None) -> int:
    """在 [box.x, x_end) 这几列下方可延伸到的下边界（已减去间距 gap，不小于原框）。reach 同上（高度）。"""
    w, h = img.size
    x0, x1 = max(box.x, 0), min(x_end, w)
    if box.bottom >= h or x0 >= x1:
        return box.bottom
    stop = h if reach is None else min(h, max(box.bottom, box.y + reach + gap))
    n = _first_dirty(img, bg, others, box.bottom, stop, (x0, x1), False)
    bottom = box.bottom + n - (gap if n < stop - box.bottom else 0)
    return max(min(bottom, h - gap), box.bottom)


def room(img: Image.Image, box: Box, bg: RGB, others: list[Box], gap: int) -> tuple[int, int, int]:
    """可延伸到的 (右边界, 只向下时的下边界, 向右又向下时的下边界)，都已减去间距 gap，且不小于原框。"""
    arr = _boxes_array(others)
    right = _room_right(img, box, bg, arr, gap, None)
    return right, _room_down(img, box, bg, arr, gap, box.right, None), _room_down(img, box, bg, arr, gap, right, None)


TRAIL_GAP = 0.6   # 行末没认出的墨迹：离框（或上一点墨迹）不超过 0.6 倍行高才算连着
TRAIL_MAX = 3.0   # 整串最多伸出 3 倍行高；更长、或一直连到别的段，就不是漏认的几个字（图标、图片），不动
TRAIL_MIN_INK = 4  # 至少这么多个墨迹像素才算字（最小的「.」抗锯齿后也有 4 个以上）；零星的一两个点是噪点，不动
TRAIL_COLOR = 80   # 每个字形的颜色（同 sample_fg 取字芯）和这一段的文字颜色每个通道相差不超过这么多，才算这一段的字
TRAIL_GREY = 40    # 文字是灰的（三个通道最大最小差 ≤ 40）时，字形也要是灰的（差 ≤ 60）：彩色表情、蓝色对勾不是字
TRAIL_GREY_INK = 60
TRAIL_CORE = 0.1   # 字形颜色取离底色最远的 10% 像素（至少 1 个）的中位数


def _ink_colour(px: np.ndarray, bg: RGB) -> np.ndarray:
    """一小块墨迹像素 (n, 3) 的颜色：离底色最远的那 TRAIL_CORE 部分的中位数（避开抗锯齿边）。
    比 sample_fg 的 30% 更严：小字号的逗号、句号只有七八个像素，大半是抗锯齿的灰，取 30% 的中位数会偏灰
    （bench 05 的「,」是 111 灰，字芯其实是 0）。"""
    dist = np.abs(px.astype(int) - np.array(bg)).sum(axis=1)
    k = max(1, int(np.ceil(len(px) * TRAIL_CORE)))
    return np.median(px[np.argsort(-dist, kind="stable")[:k]], axis=0)


def _same_ink(c: np.ndarray, fg: RGB) -> bool:
    """字形颜色 c 和文字颜色 fg 像不像同一种：每个通道相差 ≤ TRAIL_COLOR，且文字是灰的时字形也是灰的。"""
    if np.abs(c - np.array(fg)).max() > TRAIL_COLOR:
        return False
    if max(fg) - min(fg) <= TRAIL_GREY and float(c.max() - c.min()) > TRAIL_GREY_INK:
        return False
    return True


PALETTE_MIN_INK = 30   # 段内一种字芯颜色至少有这么多墨迹像素才算「这段文字用的颜色」（一个逗号、一个红点不算）


def _glyph_runs(ink: np.ndarray, last: int) -> Iterator[tuple[int, int]]:
    """[0, last] 里连着的墨迹列（一个字形或一个词）：逐个给出 (起列, 止列)。"""
    i = 0
    while i <= last:
        if not ink[i]:
            i += 1
            continue
        j = i
        while j + 1 <= last and ink[j + 1]:
            j += 1
        yield i, j
        i = j + 1


def ink_palette(arr: np.ndarray, boxes: Iterable[Box], bg: RGB) -> list[np.ndarray]:
    """这段原文墨迹用到的字芯颜色：框里逐个字形取颜色（同 _ink_colour），相差在 TRAIL_COLOR 以内的归一类，
    墨迹像素够 PALETTE_MIN_INK 的类才算。语法高亮的代码有好几种；普通正文只有一种。"""
    H, W = arr.shape[:2]
    clusters: list[list] = []   # [颜色, 墨迹像素数]
    for b in boxes:
        x0, y0, x1, y1 = _clip(b, W, H)
        if x0 >= x1 or y0 >= y1:
            continue
        crop = arr[y0:y1, x0:x1].astype(np.int16)
        dots = np.abs(crop - np.array(bg, dtype=np.int16)).max(axis=2) > NOISY_NEAR
        ink = dots.any(axis=0)
        for i, j in _glyph_runs(ink, len(ink) - 1):
            px = crop[:, i:j + 1][dots[:, i:j + 1]]
            c = _ink_colour(px, bg)
            for cl in clusters:
                if np.abs(c - cl[0]).max() <= TRAIL_COLOR:
                    cl[1] += len(px)
                    break
            else:
                clusters.append([c, len(px)])
    return [c for c, n in clusters if n >= PALETTE_MIN_INK]


def _lazy_code_inks(arr: np.ndarray, seg: Segment, bg: RGB) -> Callable[[], list[np.ndarray]]:
    """这段的 code_inks，第一次调用时才扫（大多数段行末没有异色字形，根本用不到；评审 I5：
    每次整图重画都对 150 段全量取色板，2560×1440 从 0.3 秒涨到 1.2 秒）。同一段的几个框共用一份。"""
    cache: list[list[np.ndarray]] = []

    def get() -> list[np.ndarray]:
        if not cache:
            cache.append(code_inks(ink_palette(arr, [l.box for l in seg.lines], bg)))
        return cache[0]

    return get


CODE_MIN_CHROMA = 2  # 色板里至少 2 种彩色（不是灰）才算语法高亮的代码


def code_inks(palette: list[np.ndarray]) -> list[np.ndarray]:
    """色板里能当「这段的字」放宽用的颜色：至少 CODE_MIN_CHROMA 种彩色时（语法高亮：绿关键字、红字符串……）
    返回其中的彩色；否则返回空。
    - 灰色永远不算：黑字里夹灰色时间戳时，行末灰色 ✓✓ 仍是图标（评审 I4，上一轮修过的情形）；
    - 只有一种彩色（黑字 + 蓝链接）不算：链接后面的蓝色外链图标留着。
    bench 实测：两种以上彩色的段是 16 的代码行、文档里的彩色链接，以及维基/arXiv 里蓝、紫两种链接混排的段。"""
    chroma = [c for c in palette if float(c.max() - c.min()) > TRAIL_GREY]
    return chroma if len(chroma) >= CODE_MIN_CHROMA else []


def _with_trailing(arr: np.ndarray, b: Box, bg: RGB, others: np.ndarray, fg: RGB | None = None,
                   extra_inks: Callable[[], list[np.ndarray]] | None = None) -> Box:
    """b 右边紧跟着的一小串墨迹（和底色差 NOISY_NEAR 以上）算进来：到第一个宽于 TRAIL_GAP 倍行高的空白为止。
    碰到别的段的框、墨迹伸出 TRAIL_MAX 倍行高还没断，都不延伸（返回原框）。图片右边缘算断开。
    fg（这一段的文字颜色）给了的话，只算和它同色的字形：从左往右逐个字形（连着的墨迹列）看，
    碰到第一个颜色不同的（聊天里「好的👍」的表情、灰色 ✓✓、外链图标、红点）就停，它和它后面的都不抹。
    extra_inks：碰到和 fg 不同色的字形时才调用（取色板要扫整段，贵），返回也算这段的字的颜色（code_inks：
    语法高亮代码的彩色）——行末彩色的「');」照样抹，和哪种都不像的（表情、图标）照样停下。"""
    H, W = arr.shape[:2]
    y0, y1 = max(b.y, 0), min(b.bottom, H)
    gap = max(2, round(TRAIL_GAP * b.h))
    reach = round(TRAIL_MAX * b.h)
    stop = min(W, b.right + reach + gap + 1)
    hit = others[(others[:, 2] > b.right) & (others[:, 0] < stop) & (others[:, 1] < y1) & (others[:, 3] > y0)]
    blocked = len(hit) > 0 and int(hit[:, 0].min()) < stop
    if blocked:
        stop = int(hit[:, 0].min())
    if b.right >= stop or y0 >= y1:
        return b
    crop = arr[y0:y1, b.right:stop].astype(np.int16)
    dots = np.abs(crop - np.array(bg, dtype=np.int16)).max(axis=2) > NOISY_NEAR
    ink = dots.any(axis=0)
    last = -1
    for i, v in enumerate(ink.tolist()):
        if v:
            last = i
        elif i - last > gap:
            break
    else:   # 一直没碰到够宽的空白：只有到了图片右边缘才算断开
        if blocked or stop < W:
            return b
    if last < 0 or last + 1 > reach:
        return b
    if fg is not None:
        last = _same_colour_end(crop, dots, ink, last, bg, fg, extra_inks)
    if last < 0 or int(dots[:, :last + 1].sum()) < TRAIL_MIN_INK:
        return b
    return Box(b.x, b.y, b.w + last + 1, b.h)


def _same_colour_end(crop: np.ndarray, dots: np.ndarray, ink: np.ndarray, last: int, bg: RGB, fg: RGB,
                     extra_inks: Callable[[], list[np.ndarray]] | None = None) -> int:
    """[0, last] 里从左往右逐个字形（连着的墨迹列）看颜色，返回最后一个和 fg（或 extra_inks() 里某种颜色）同色的
    字形的右端列；第一个就不同返回 -1。extra_inks 只在碰到和 fg 不同色的字形时才调用，且只调一次。"""
    end, more = -1, None
    for i, j in _glyph_runs(ink, last):
        c = _ink_colour(crop[:, i:j + 1][dots[:, i:j + 1]], bg)
        if not _same_ink(c, fg):
            if extra_inks is None:
                break
            if more is None:
                more = extra_inks()
            if not any(_same_ink(c, tuple(int(v) for v in k)) for k in more):
                break
        end = j
    return end


def same_text(a: str, b: str) -> bool:
    """去掉所有空白、不分大小写后相同。"""
    return "".join(a.split()).casefold() == "".join(b.split()).casefold()


def _place(text: str, make: Callable[[int], AnyFont], regions: Iterable[tuple[int, int, bool]], start_px: int,
           min_px: int) -> Fit:
    """先用起始字号按顺序在各候选区域 (宽, 高, 是否折行) 里试；都放不下就逐号缩小，每个字号把各区域按顺序都试一遍
    ——区域之间不一定谁包含谁（原宽向下的又窄又高，向右又向下的又宽又矮），哪个先放得下就用哪个；
    到下限仍放不下，在面积最大的区域里截断。
    regions 可以是生成器：前面的区域在起始字号下放得下，后面的区域（要扫描空白）就不用算了。
    make(字号) 给出排字用的字体（MixedFont；有胶囊时是 PillFont）。"""
    min_px = max(min_px, MIN_PX)
    start = max(start_px, min_px)
    font = make(start)
    tried: list[tuple[int, int, bool]] = []
    for region in regions:
        if region in tried:
            continue
        tried.append(region)
        lines = _lines_if_fit(text, font, start, *region)
        if lines is not None:
            return Fit(start, lines, False, region[0])
    for px in range(start - 1, min_px - 1, -1):
        font = make(px)
        for region in tried:
            lines = _lines_if_fit(text, font, px, *region)
            if lines is not None:
                return Fit(px, lines, False, region[0])
    w, h, wrap = max(tried, key=lambda r: r[0] * r[1])   # 面积相同取先试的
    return _truncate(text, make, w, h, min_px, wrap)


def _regions(img: Image.Image, seg: Segment, text: str, make: Callable[[int], AnyFont], bg: RGB, others: np.ndarray,
             start_px: int, min_px: int, extend: bool) -> Iterator[tuple[int, int, bool]]:
    """候选区域，按尝试顺序逐个产生：原框 →（多行段）原宽向下延伸 → 向右又向下延伸。
    只扫「用得上」的范围：最宽 = 起始字号下整段排成一行的宽度 + 一个字，最高 = 每字一行的高度。"""
    box, multi = seg.box, len(seg.lines) > 1
    yield box.w, box.h, multi
    if not extend:
        return
    gap = max(1, round(EXT_GAP * start_px))
    start = max(start_px, min_px, MIN_PX)
    font = make(start)
    line_h = font.line_height(text)
    if isinstance(font, PillFont):   # 胶囊那一「字」单独一行时行更高
        line_h = max(line_h, font.tallest() + PILL_LINE_GAP)
    reach_w = int(np.ceil(font.getlength(text))) + start
    reach_h = len(text) * line_h + start
    if multi:     # 多行段先向下（保持左右边和原段落对齐），再向右
        yield box.w, _room_down(img, box, bg, others, gap, box.right, reach_h) - box.y, True
    # 单行向右延伸，一行放不下再折到下方空白
    right = _room_right(img, box, bg, others, gap, reach_w)
    yield right - box.x, _room_down(img, box, bg, others, gap, right, reach_h) - box.y, True


def draw_marker(draw: ImageDraw.ImageDraw, box: Box, width: int, height: int) -> None:
    """段落框右上角的小三角：这段结果带说明（显示的是原文，或译文的引用/术语可能未保持原样），悬停可看原因。"""
    right = min(box.right, width) - 1  # 框伸出图片右边时贴着图片右边画（画到图外的部分 Pillow 自己裁掉）
    top = max(box.y, 0)
    draw.polygon([(right - MARK_PX + 1, top), (right, top), (right, top + MARK_PX - 1)], fill=MARK_COLOR)


_BREAK_RE = re.compile(r"\s*\n\s*")
# 其他行分隔符也当换行：\v \f、NEL(\x85)、行分隔符 U+2028、段分隔符 U+2029
_LINE_SEP_RE = re.compile("\r\n|[\r\x0b\x0c\x85  ]")


def _ascii_visible(ch: str) -> bool:
    """ASCII 可见字符（字母、数字、+ # . 之类）：「C++\\nJava」两边都是，折行时要留空格。"""
    return ch.isascii() and ch.isprintable()   # 空白不会出现在这里：换行两侧的空白已被 _BREAK_RE 吃掉


def normalize_translation(text: str, target: str, pills: Mapping[str, str] | None = None) -> str:
    """译文里的换行不能带进排版（字体量宽把 \\n 当零宽、画出来重叠成一团）：
    \\r\\n、\\r 和其他行分隔符先归成 \\n，制表符当空格，再把「换行及两侧空白」折成一个空格（英文）；
    中文什么都不留——除非换行两侧都是 ASCII 可见字符（「Python\\nNumPy」「C++\\nJava」），那样留一个空格，免得两个词粘成一个。
    pills：text 里换成私用区字符的胶囊 -> 胶囊文字；换行旁边是胶囊时按胶囊文字的头尾字判断（和没换时一样）。"""
    text = _LINE_SEP_RE.sub("\n", text).replace("\t", " ")
    if target != "zh":
        return _BREAK_RE.sub(" ", text)
    pills = pills or {}

    def fold(m: re.Match) -> str:
        s, a, b = m.string, m.start(), m.end()
        left = s[a - 1] if a > 0 else ""
        right = s[b] if b < len(s) else ""
        left, right = pills.get(left, left)[-1:], pills.get(right, right)[:1]
        return " " if _ascii_visible(left) and _ascii_visible(right) else ""

    return _BREAK_RE.sub(fold, text)


def valid_pill_spans(text: str, pills: tuple[Pill, ...], spans: Iterable[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """只留下靠得住的胶囊位置：编号是这一段的胶囊、每个胶囊只用一次、互不重叠、在 text 范围里，
    且 text[起:止] 正是这个胶囊的文字（按键组合按纠错后的写法，和占位符换回来的一样）。其余的照旧当文字画。"""
    out: list[tuple[int, int, int]] = []
    used: set[int] = set()
    end = 0
    for a, b, k in sorted(spans):
        if not 0 <= k < min(len(pills), PILL_LIMIT) or k in used:
            continue
        if a < end or not 0 <= a < b <= len(text):
            continue
        if text[a:b] != fix_key_combos(pills[k].text):
            continue
        out.append((a, b, k))
        used.add(k)
        end = b
    return out


def pill_scale(px: int, start_px: int) -> float:
    """胶囊图的缩放比 = min(1, 绘制字号 / 起始字号)。
    字号没缩（px ≥ 起始字号）时 1:1 贴原图（不重采样，保证清晰），不放大；缩了就和文字同比例缩，
    胶囊和文字的大小比例始终和没缩时一样，缩一号胶囊也只缩一号的比例（连续，没有跳变）。
    不用「px / 本段原文估计字号」：起始字号是全图归类后的，和本段自己的估计可能差 10～20%，
    那样刚缩一号时胶囊会一下缩 20% 以上（评审 m2），而估计字号偏小时又会一直不缩。"""
    return min(1.0, px / start_px)


def _pill_text(text: str, pills: tuple[Pill, ...], spans: list[tuple[int, int, int]],
               src: Image.Image) -> tuple[str, dict[str, str], dict[int, Image.Image]]:
    """把 text 里的胶囊换成私用区字符，返回 (换过的 text, 私用区字符 -> 胶囊文字, 第几个 -> 原图剪下的胶囊)。
    胶囊框（色带里面）向外多剪 PILL_CROP_PAD 像素带上描边；剪出来是空的（框在图外）那个照旧当文字。"""
    parts: list[str] = []
    names: dict[str, str] = {}
    images: dict[int, Image.Image] = {}
    pos = 0
    for a, b, k in spans:
        x0, y0, x1, y1 = _clip(pills[k].box, src.width, src.height, PILL_CROP_PAD)
        if x0 >= x1 or y0 >= y1:
            continue
        ch = chr(PILL_BASE + k)
        parts += [text[pos:a], ch]
        names[ch] = text[a:b]
        images[k] = src.crop((x0, y0, x1, y1))
        pos = b
    parts.append(text[pos:])
    return "".join(parts), names, images


def _pill_maker(font_path: str, images: dict[int, Image.Image], start_px: int) -> Callable[[int], PillFont]:
    cache: dict = {}
    return lambda px: PillFont(_font(font_path, px), images, pill_scale(px, start_px), cache=cache)


def render(image: Image.Image, segments: list[Segment], translations: dict[int, str], target: str,
           *, marked: frozenset[int] | set[int] = frozenset(), keep_identical: bool = True,
           pill_spans: Mapping[int, Iterable[tuple[int, int, int]]] | None = None) -> RenderResult:
    """marked：要在右上角画小三角的段落编号（结果带说明的段；可以同时在 translations 里，角标画在译文之上）。
    keep_identical：译文和原文相同（去空白、不分大小写）的段不抹不画、原图不动（logo、人名、链接文字）。
    对比测试的「原样重画」关卡要看抹得干不干净，传 False。
    pill_spans：段落编号 -> 胶囊在译文里的位置（SegmentResult.pill_spans）。靠得住的那几处贴回原图上的胶囊，
    其余（没给、对不上、译文里本来就有私用区字符）照旧把胶囊文字当普通文字画。"""
    pill_spans = pill_spans or {}
    src = image.convert("RGB")
    arr = np.asarray(src)
    out = src.copy()
    draw = ImageDraw.Draw(out)
    font_path = FONT_ZH if target == "zh" else FONT_EN
    starts = start_sizes(segments)
    all_boxes = _boxes_array([s.box for s in segments])
    index = {id(s): i for i, s in enumerate(segments)}
    placements: dict[int, Placement] = {}
    for seg in segments:
        raw = translations.get(seg.idx)
        if seg.skip or not raw:
            continue
        tr = normalize_translation(raw, target)
        if not tr.strip():
            continue
        if keep_identical and same_text(tr, seg.text):
            continue
        spans = [] if not seg.pills or _has_pill_chars(raw) else \
            valid_pill_spans(raw, seg.pills, pill_spans.get(seg.idx, ()))
        make: Callable[[int], AnyFont] = partial(_font, font_path)
        names: dict[str, str] = {}
        if spans:
            text, names, images = _pill_text(raw, seg.pills, spans, src)
            if images:
                tr = normalize_translation(text, target, names)
                make = _pill_maker(font_path, images, starts[seg.idx])
        box = seg.box
        bg, noisy = sample_bg(arr, box, [p.box for p in seg.pills])
        raw_fg = sample_fg(arr, box, bg)
        fg = ensure_contrast(raw_fg, bg)
        others = np.delete(all_boxes, index[id(seg)], axis=0)
        # 行内代码胶囊连底色一起抹（胶囊可能伸出 OCR 框）；底色干净时，每个框右边紧跟着的、OCR 没认出的
        # 一小串墨迹（行末的「…”。」）也抹掉——它们是这一段的字，留着既难看又挡住向右延伸。
        # 只抹和这段文字同色的：表情、对勾、图标留着（它们照样挡住延伸）
        erase = ([box] if noisy else [l.box for l in seg.lines]) + [p.box for p in seg.pills]
        if not noisy:
            extra = _lazy_code_inks(arr, seg, bg)
            erase = [_with_trailing(arr, b, bg, others, raw_fg, extra) for b in erase]
        for b in erase:
            x0, y0, x1, y1 = _clip(b, out.width, out.height, PAD)
            draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=bg)
        start_px = starts[seg.idx]
        min_px = max(MIN_PX, round(start_px * MIN_SCALE))
        # 底色杂乱时不延伸：延伸区域不抹，杂乱底上画字看不清。
        # 在「当前」画面上找空白：前面各段已画的译文也是墨迹，后面的段不会延伸进去
        regions = _regions(out, seg, tr, make, bg, others, start_px, min_px, extend=not noisy)
        fit = _place(tr, make, regions, start_px, min_px)
        font = make(fit.px)
        if isinstance(font, PillFont) and fit.width is not None:
            font = font.within(fit.width)
        line_h = font.line_height(tr)
        tops, above, below = _stack(font, fit.lines, line_h)
        y = box.y if len(fit.lines) > 1 else box.y + max(0, (box.h - fit.px) // 2 - 1)
        for top, line in zip(tops, fit.lines):
            if isinstance(font, PillFont):
                font.draw(draw, (box.x, y + top), line, fg, out)
            else:
                font.draw(draw, (box.x, y + top), line, fg)
        text_w = max((int(np.ceil(font.getlength(l))) for l in fit.lines), default=0)
        area = Box(box.x, y - above, max(text_w, 1), above + tops[-1] + fit.px + below)
        shown = fit.lines
        n_pills = 0
        if names:   # 记录（快照、悬停）里胶囊那几处写回胶囊文字
            n_pills = sum(c in names for l in fit.lines for c in l)
            shown = ["".join(names.get(c, c) for c in l) for l in fit.lines]
        placements[seg.idx] = Placement(seg.idx, box, fit.px, shown, fg, bg, fit.truncated, area, n_pills)
    for seg in segments:
        if seg.idx in marked:
            draw_marker(draw, seg.box, out.width, out.height)
    return RenderResult(out, placements)

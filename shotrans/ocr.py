"""调用 Windows 自带 OCR。英文、中文两个引擎都在就都跑、按汉字占比选底稿再互相修补；只装了一个就只用那一个。"""
from __future__ import annotations

import asyncio
import io
import re
import unicodedata
from dataclasses import dataclass

from PIL import Image

from .i18n import t
from .models import Box, OcrLine, OcrWord

CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
LATIN_RE = re.compile(r"[A-Za-z]")


class OcrError(Exception):
    pass


LANG_EN = "en-US"
LANG_ZH = "zh-Hans-CN"


class OcrLines(list):
    """recognize 的返回值：就是 list[OcrLine]，多一个 missing（没装的 OCR 语言包标签，按 en-US、zh-Hans-CN 顺序）。"""

    def __init__(self, lines=(), missing: tuple[str, ...] = ()):
        super().__init__(lines)
        self.missing = tuple(missing)


def is_cjk_char(ch: str) -> bool:
    """汉字和全角标点都算「中文字符」：它们两边不加空格。"""
    return bool(CJK_RE.match(ch)) or "　" <= ch <= "〿" or "＀" <= ch <= "￯"


def join_words(words: list[str]) -> str:
    """把 OCR 的词拼成一行：前后两边都不是中文才加空格。"""
    out = ""
    for w in words:
        if not w:
            continue
        if out and not is_cjk_char(out[-1]) and not is_cjk_char(w[0]):
            out += " "
        out += w
    return out


def cjk_ratio(text: str) -> float:
    cjk = len(CJK_RE.findall(text))
    latin = len(LATIN_RE.findall(text))
    return cjk / (cjk + latin) if cjk + latin else 0.0


def pick_result(en_lines: list[OcrLine], zh_lines: list[OcrLine]) -> list[OcrLine]:
    zh_text = "".join(l.text for l in zh_lines)
    return zh_lines if cjk_ratio(zh_text) >= 0.3 else en_lines


# ---- 两引擎逐词拼接（Task 21）----
# 整图二选一后，英文为主的图用 en 结果：夹在英文里的中文被 en 引擎认成乱码（「确定」→「6ßE」）或整段丢掉，
# 而 zh 引擎在同一位置认得很好（只是它的英文差：bOth、引00d=Blood、丨=I）。所以拿 en 结果当底稿，
# 只在「zh 认出一串汉字、en 在那里是乱码或什么都没有」的地方换成 zh 的词。
LINE_V_OVERLAP = 0.5   # en 行与 zh 行竖直重叠 ≥ 较矮行高的一半才算同一行
WORD_X_OVERLAP = 0.5   # en 词与汉字串横向重叠 ≥ 该 en 词宽的一半，才算「这个词就是那串汉字」
INSERT_MIN_HANZI = 2   # en 整段丢了时，至少 2 个汉字才插入（单个字常是 zh 引擎把图标、竖线认成的字）
INSERT_REACH = 1.0     # 插入时汉字串离 en 行的横向距离 ≤ 1 倍行高（en 丢了行尾一段时，串在 en 行框外面）
NOISE_HANZI = "丨"      # zh 引擎把竖线、大写 I 认成的「丨」：不算汉字
QUOTE_CHARS = set("\"“”'‘’「」")
EDGE_PUNCT = "\"'`([{<)]}>.,;:!"   # en 词首尾可以剥掉、替换后原样保留的 ASCII 标点
TRAIL_PUNCT = EDGE_PUNCT + "?"     # 词尾的 ? 也是标点（What?、this?)）；词首、词中的 ? 仍算乱码（「"?f"」）
BOX_LIKE_HANZI = set("口回田囗日目曰皿")   # zh 把图标、方框认成的字：一串全是它们时不插入（「回田Settings」）
GARBLE_CHARS = set("%&?#@$^*|~<>{}[]=+\\")
LEGIT_SYMBOL_RE = re.compile(r"(?<=\d)%|(?<=[A-Z])&(?=[A-Z])|(?<=[A-Za-z])\++$")   # 15%、R&D、C++/A+


def _is_hanzi_word(text: str) -> bool:
    """全是中文字符（汉字、全角标点）且至少有一个真汉字（「丨」不算）的词。"""
    return bool(text) and all(is_cjk_char(c) for c in text) and bool(CJK_RE.search(text.replace(NOISE_HANZI, "")))


def _is_cjk_punct(text: str) -> bool:
    """全角/中文标点（不含汉字）：汉字串中间可以夹着它。"""
    return bool(text) and all(is_cjk_char(c) and not CJK_RE.match(c) for c in text)


def _split_edges(text: str) -> tuple[str, str, str]:
    """拆成（首部 ASCII 标点、核心、尾部 ASCII 标点）。全是标点时核心为空、全算首部。
    尾部比首部多剥一个 ?：「this?)」核心是 this，「"?f"」核心仍是 ?f。"""
    rest = text.lstrip(EDGE_PUNCT)
    core = rest.rstrip(TRAIL_PUNCT)
    if not core:
        return text, "", ""
    return text[:len(text) - len(rest)], core, rest[len(core):]


def is_garbled(text: str) -> bool:
    """en 引擎的词「看起来是乱码」：剥掉首尾引号/括号/标点后，核心里有非 ASCII 的字母（ß、É、ä）或 % & ? # 之类。
    - 核心里一个字母数字都没有（「—」「•」「>」「"」）不算：全是符号，没有可换的，换了反而把 zh 认错的
      「一」「冫」塞进来（bench 16「asyncio — Asynchronous」、arXiv「next >」实测）；
    - 非 ASCII 的标点符号（— • © °）不算：en 引擎正常就会输出它们；
    - 15%、R&D、C++、A+ 里的 % & + 不算（bench 05「15%」被 zh 认成「巧」）。"""
    core = _split_edges(text)[1]
    if not any(c.isalnum() for c in core):
        return False
    core = LEGIT_SYMBOL_RE.sub("", core)
    return any(c in GARBLE_CHARS or (ord(c) > 127 and unicodedata.category(c).startswith("L")) for c in core)


STRONG_GARBLE_CHARS = set("%&?^~|")   # 代码、公式里不常见的符号；+ = # [ ] @ < > { } $ * \ 在代码记号里很常见


def is_strong_garbled(text: str) -> bool:
    """确实是乱码（不只是代码记号或带重音的正常词）：核心里有 % & ? ^ ~ | （iRB%lJ、ÉAAERB&IJ、?f），
    或有非 ASCII 字母且不像一个正常拼写的词——正常词是全字母、首字母之后没有大写（café、naïve、José、Zürich）；
    「6ßE」夹数字、「ßEAA」「ÉBB」小写后跟大写或全大写，都算乱码。"""
    if not is_garbled(text):
        return False
    core = _split_edges(text)[1]   # 15%、R&D 这类合法写法 is_garbled 已经排除了
    if any(c in STRONG_GARBLE_CHARS for c in core):
        return True
    word_like = core.isalpha() and not any(c.isupper() for c in core[1:])
    return not word_like and any(ord(c) > 127 and unicodedata.category(c).startswith("L") for c in core)


def _x_overlap(a: Box, x0: int, x1: int) -> int:
    return max(0, min(a.right, x1) - max(a.x, x0))


def _v_overlap(a: Box, b: Box) -> int:
    return min(a.bottom, b.bottom) - max(a.y, b.y)


def _v_overlap_ok(a: Box, b: Box) -> bool:
    return _v_overlap(a, b) >= LINE_V_OVERLAP * min(a.h, b.h)


@dataclass(frozen=True)
class _Run:
    """zh 行里的一串汉字（中间可夹全角标点）。before/after：紧挨着它的 zh 引号词（没有为 None）。"""

    words: tuple[OcrWord, ...]
    line_box: Box
    before: OcrWord | None
    after: OcrWord | None
    line_words: tuple[OcrWord, ...] = ()   # 整个 zh 行的词：乱码 en 词比汉字串宽时，要连同旁边的 zh 词一起换进来

    @property
    def x0(self) -> int:
        return self.words[0].box.x

    @property
    def x1(self) -> int:
        return self.words[-1].box.right

    @property
    def hanzi(self) -> int:
        return sum(len(CJK_RE.findall(w.text.replace(NOISE_HANZI, ""))) for w in self.words)


def _hanzi_runs(line: OcrLine) -> list[_Run]:
    ws = line.words
    runs, i = [], 0
    while i < len(ws):
        if not _is_hanzi_word(ws[i].text):
            i += 1
            continue
        j = last = i
        while j + 1 < len(ws) and (_is_hanzi_word(ws[j + 1].text) or _is_cjk_punct(ws[j + 1].text)):
            j += 1
            if _is_hanzi_word(ws[j].text):
                last = j
        before = ws[i - 1] if i > 0 and ws[i - 1].text in QUOTE_CHARS else None
        after = ws[last + 1] if last + 1 < len(ws) and ws[last + 1].text in QUOTE_CHARS else None
        runs.append(_Run(ws[i:last + 1], line.box, before, after, ws))
        i = last + 1
    return runs


def _h_distance(box: Box, x0: int, x1: int) -> int:
    return max(0, box.x - x1, x0 - box.right)


def _has_content(text: str) -> bool:
    """有字母、数字或汉字（不是纯引号、标点）。"""
    return any(c.isalnum() for c in text)


def _clean_zh_latin(text: str) -> bool:
    """zh 引擎的拉丁词能不能带进英文底稿：不夹中文字符（a十b、x二1、引00d），大小写不怪（bOth、tOday）。"""
    return not any(is_cjk_char(c) for c in text) and not ODD_CASE_RE.search(text)


def _keep_edge_punct(hit: list[OcrWord], new: list[OcrWord]) -> list[OcrWord]:
    """被换掉的 en 词首尾的引号/括号/标点原样保留（「"6ßE」→「"确定」）：第一个词的首部接在最前、最后一个词的尾部
    接在最后；中间词的首部接在中心不早于它左边的第一个新词前面，尾部接在中心早于它右边的最后一个新词后面
    （「ßEAA "ÉBB"」的左引号不能丢）。框仍用 zh 的（排序、胶囊都看它）。"""
    pre, post = [""] * len(new), [""] * len(new)
    centers = [w.box.x + w.box.w / 2 for w in new]
    for k, h in enumerate(hit):
        lead, _, trail = _split_edges(h.text)
        if lead:
            i = 0 if k == 0 else next((i for i, c in enumerate(centers) if c >= h.box.x), len(new) - 1)
            pre[i] = pre[i] + lead
        if trail:
            i = len(new) - 1 if k == len(hit) - 1 else max(
                (i for i, c in enumerate(centers) if c < h.box.right), default=0)
            post[i] = post[i] + trail
    return [OcrWord(pre[i] + w.text + post[i], w.box) for i, w in enumerate(new)]


def _patch_en_line(line: OcrLine, runs: list[_Run]) -> OcrLine:
    en_words = list(line.words)          # 还没被换掉的 en 词
    added: list[OcrWord] = []
    used: set[int] = set()               # 已经换进来的 zh 词（id）：一个宽乱码词盖住两串时，第二串不再插一次
    for run in sorted(runs, key=lambda r: r.x0):
        if any(id(w) in used for w in run.words):
            continue
        hit = [w for w in en_words if _x_overlap(w.box, run.x0, run.x1) >= WORD_X_OVERLAP * w.box.w]
        touching = [w for w in en_words if _x_overlap(w.box, run.x0, run.x1) > 0]
        new = list(run.words)
        if hit:
            if not all(is_garbled(w.text) for w in hit):
                continue                 # 有干净的英文词（Blood、I）：宁可保留 en
            # 只碰到一点（不到一半）的「强乱码」词也一起换掉，否则它留着、又换进汉字，同一处出现两遍。
            # 只因 + = # [ ] @ 这类代码记号、或像 café 这样的正常拼写才算乱码的词不并入（复审 I7：a+b 会变成 a十b）
            hit += [w for w in touching if w not in hit and is_strong_garbled(w.text)]
            hit.sort(key=lambda w: w.box.x)
            # 乱码词比汉字串宽（en 把「用户名Python」认成一个词「ßPython」）：中心落在这些词范围里的 zh 词
            # 一起换进来，不然旁边的英文就丢了。只带汉字词和干净的拉丁词：纯标点不要（en 词自己的引号保留着），
            # 夹汉字的（a十b、x二1、引00d）、大小写怪的（bOth）不要——宁可只换汉字串
            hx0, hx1 = hit[0].box.x, max(w.box.right for w in hit)
            ids = {id(w) for w in new}
            new += [w for w in run.line_words if id(w) not in ids and _has_content(w.text)
                    and (_is_hanzi_word(w.text) or _clean_zh_latin(w.text))
                    and hx0 <= w.box.x + w.box.w / 2 < hx1]
            new.sort(key=lambda w: w.box.x)
            used.update(id(w) for w in new)
            new = _keep_edge_punct(hit, new)
            en_words = [w for w in en_words if w not in hit]
        elif (not touching and run.hanzi >= INSERT_MIN_HANZI
              and not all(c in BOX_LIKE_HANZI for w in run.words for c in CJK_RE.findall(w.text))):
            for q, side in ((run.before, 0), (run.after, 1)):
                if q is not None and not any(_x_overlap(w.box, q.box.x, q.box.right) for w in en_words):
                    new.insert(len(new) if side else 0, q)
        else:
            continue
        added.extend(new)
    if not added:
        return line
    words = sorted(en_words + added, key=lambda w: w.box.x)
    box = line.box
    for w in added:
        box = box.union(w.box)
    return OcrLine(join_words([w.text for w in words]), box, tuple(words))


def patch_en_with_zh(en_lines: list[OcrLine], zh_lines: list[OcrLine]) -> list[OcrLine]:
    """英文底稿补中文：zh 认出的汉字串，en 在同一位置是乱码就替换（保留 en 词首尾的引号/括号/标点），
    en 在那里什么都没认出来（且串至少 2 个汉字）就插入（连同紧挨着的 zh 引号）。en 是干净英文的不动。
    每串汉字只交给一个 en 行：竖直重叠够、横向最近（插入时距离 ≤ 1 倍行高）的那行。"""
    plan: dict[int, list[_Run]] = {}
    for zl in zh_lines:
        for run in _hanzi_runs(zl):
            best = None
            for i, el in enumerate(en_lines):
                if not el.words or not _v_overlap_ok(el.box, zl.box):
                    continue
                d = _h_distance(el.box, run.x0, run.x1)
                # 距离相同（上下行贴得很近、都是 0）时取竖直重叠更大的那行
                key = (d, -_v_overlap(el.box, zl.box))
                if d <= INSERT_REACH * el.box.h and (best is None or key < best[0]):
                    best = (key, i)
            if best is not None:
                plan.setdefault(best[1], []).append(run)
    return [_patch_en_line(el, plan[i]) if i in plan else el for i, el in enumerate(en_lines)]


SEG_REACH = 0.25   # 中文底稿补英文：en 词左右最多伸出 zh 词段 0.25 倍行高（OCR 框的误差）


def _has_ascii_alnum(text: str) -> bool:
    return any(c.isascii() and c.isalnum() for c in text)


def _latin_segments(line: OcrLine) -> list[tuple[OcrWord, ...]]:
    """zh 行里连续的不含汉字的词段，首尾都是含 ASCII 字母/数字的词（中间可夹「、」之类）。
    首尾的纯标点不算进来：「模式，2层Bi LSTM」的「，」要是算进词段，换成 en 的词时就把它丢了（bench 02 实测）。"""
    ws = line.words
    segs, i = [], 0
    while i < len(ws):
        if CJK_RE.search(ws[i].text) or not _has_ascii_alnum(ws[i].text):
            i += 1
            continue
        j = last = i
        while j + 1 < len(ws) and not CJK_RE.search(ws[j + 1].text):
            j += 1
            if _has_ascii_alnum(ws[j].text):
                last = j
        segs.append(ws[i:last + 1])
        i = last + 1
    return segs


ODD_CASE_RE = re.compile(r"[a-z][A-Z]")   # 词中间小写后跟大写：zh 引擎的 bOth、tOday、shOUld


def _letters_only(text: str) -> str:
    """去掉空白和「、」（zh 引擎在拉丁词中间插的顿号）：比较两个引擎认的是不是同一串字母。
    其他全角标点（．，：）要两边一致：zh「1．0」对 en「10」不能信 en（en 漏了小数点）。"""
    return "".join(c for c in text if not c.isspace() and c != "、")


def patch_zh_with_en(zh_lines: list[OcrLine], en_lines: list[OcrLine]) -> list[OcrLine]:
    """中文底稿补英文：zh 行里的拉丁词段，若同位置的 en 词去掉空格、「、」后与它是同一串字母（只差大小写、
    多出的空格或「、」），就换成 en 的词（bOth→both、tar、get_t→target_t、Bi LSTM→BiLSTM）。
    字母本身不同的不换：两个引擎在 0/O、1/l/I 上各有对错（真实数据里 en 把「1层」认成「I层」、「OK」认成「0K」）。"""
    out = []
    for zl in zh_lines:
        if not zl.words:
            out.append(zl)
            continue
        en_words = [w for el in en_lines if _v_overlap_ok(el.box, zl.box) for w in el.words]
        tol = SEG_REACH * zl.box.h
        words, changed = list(zl.words), False
        for seg in _latin_segments(zl):
            x0, x1 = seg[0].box.x, seg[-1].box.right
            hit = sorted((w for w in en_words if _x_overlap(w.box, x0, x1) > 0), key=lambda w: w.box.x)
            if not hit or any(w.box.x < x0 - tol or w.box.right > x1 + tol for w in hit):
                continue
            zt, et = join_words([w.text for w in seg]), join_words([w.text for w in hit])
            if zt == et or _letters_only(zt).lower() != _letters_only(et).lower():
                continue
            if _letters_only(zt) != _letters_only(et) and not ODD_CASE_RE.search(zt):
                continue                 # 只差大小写、而 zh 的大小写看着正常（A-V-G+P）：信 zh
            k = next(i for i, w in enumerate(words) if w is seg[0])
            words[k:k + len(seg)] = hit
            changed = True
        if changed:
            box = zl.box
            for w in words:
                box = box.union(w.box)
            out.append(OcrLine(join_words([w.text for w in words]), box, tuple(words)))
        else:
            out.append(zl)
    return out


def combine_results(en_lines: list[OcrLine], zh_lines: list[OcrLine]) -> list[OcrLine]:
    """整图先二选一当底稿（pick_result），再用另一个引擎的词修补底稿。"""
    base = pick_result(en_lines, zh_lines)
    if base is en_lines:
        return patch_en_with_zh(en_lines, zh_lines)
    return patch_zh_with_en(zh_lines, en_lines)


def upscale_factor(width: int, height: int, max_dim: int) -> int:
    """小图放大两倍识别更准；放大后不能超过引擎的尺寸上限。"""
    longest = max(width, height)
    if longest <= 1500 and longest * 2 <= max_dim:
        return 2
    return 1


def available_languages() -> list[str]:
    from winrt.windows.media.ocr import OcrEngine

    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]


def _max_dim() -> int:
    from winrt.windows.media.ocr import OcrEngine

    return int(OcrEngine.max_image_dimension)


async def _recognize_png(png: bytes, lang_tag: str):
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    engine = OcrEngine.try_create_from_language(Language(lang_tag))
    if engine is None:
        raise OcrError(t("ocr.lang_missing", tag=lang_tag))
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    try:
        writer.write_bytes(png)
    except TypeError:  # 老版本 pywinrt 不认 bytes，只认整数序列
        writer.write_bytes(list(png))
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    return await engine.recognize_async(bitmap)


def _to_lines(result, scale: int) -> list[OcrLine]:
    lines = []
    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        rects = [w.bounding_rect for w in words]
        x0 = min(r.x for r in rects)
        y0 = min(r.y for r in rects)
        x1 = max(r.x + r.width for r in rects)
        y1 = max(r.y + r.height for r in rects)
        box = _scaled(x0, y0, x1, y1, scale)
        ws = tuple(OcrWord(w.text, _scaled(r.x, r.y, r.x + r.width, r.y + r.height, scale))
                   for w, r in zip(words, rects) if w.text)
        lines.append(OcrLine(join_words([w.text for w in words]), box, ws))
    return lines


def _scaled(x0: float, y0: float, x1: float, y1: float, scale: int) -> Box:
    """放大图上的坐标换算回原图；宽高至少 1。"""
    return Box(int(x0 / scale), int(y0 / scale),
               max(1, round((x1 - x0) / scale)), max(1, round((y1 - y0) / scale)))


def recognize(image: Image.Image) -> OcrLines:
    langs = set(available_languages())
    have = {LANG_EN: LANG_EN in langs, LANG_ZH: LANG_ZH in langs}
    if not any(have.values()):
        raise OcrError(t("ocr.no_engine", en=LANG_EN, zh=LANG_ZH))
    missing = tuple(tag for tag in (LANG_EN, LANG_ZH) if not have[tag])
    max_dim = _max_dim()
    img = image.convert("RGB")
    if max(img.size) > max_dim:
        raise OcrError(t("ocr.too_big", size=img.size, max=max_dim))
    scale = upscale_factor(img.width, img.height, max_dim)
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    png = buf.getvalue()

    if not missing:
        async def both():
            return await _recognize_png(png, LANG_EN), await _recognize_png(png, LANG_ZH)

        en, zh = asyncio.run(both())
        return OcrLines(combine_results(_to_lines(en, scale), _to_lines(zh, scale)))
    # 只装了一个：只用这一个引擎的结果，不做两引擎拼接
    tag = LANG_EN if have[LANG_EN] else LANG_ZH
    return OcrLines(_to_lines(asyncio.run(_recognize_png(png, tag)), scale), missing)

"""发送前把不能翻的内容换成 ⟨n⟩，译文回来再换回去。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable


class MissingPlaceholder(Exception):
    """译文里少了占位符。args[0] 是缺失的占位符列表。"""


_URL_MID = r"A-Za-z0-9\-._~:/?#@!$&'*+,;=%"   # 只认 ASCII，不吞相邻中文
_URL_END = r"A-Za-z0-9\-_~/?#@!$&'*+=%"       # 不以 .,;: 结尾
_URL = re.compile(
    rf"(?:https?://|www\.)[{_URL_MID}]*[{_URL_END}]"
)
_EMAIL = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_SURNAME = r"[A-Z][A-Za-z'’\-]*"
_CITE_ITEM = rf"{_SURNAME}(?: et al\.| (?:and|&) {_SURNAME})?,?\s+(?:19|20)\d{{2}}[a-z]?"
_CITE = re.compile(rf"\({_CITE_ITEM}(?:\s*;\s*{_CITE_ITEM})*\)")

_LATER = [
    re.compile(r"\[\d+(?:\s*[-–,]\s*\d+)*\]"),                      # [12] [3–5] [1, 2]
    _CITE,                                                           # (Smith et al., 2020)
    re.compile(r"\b[pP]\s*[<>=≤≥]\s*0?\.\d+"),                       # p < 0.05
    re.compile(r"\b[rtFzβ]\s*(?:\(\d+(?:,\s*\d+)?\))?\s*=\s*-?\d*\.\d+"),  # r = .43, t(29) = 2.1
    re.compile(r"\b(?:90|95|99)%\s*CI\b"),                           # 95% CI
]
_SLOT_RE = re.compile(r"⟨\s*(\d+)\s*⟩")

# ---- 按键组合（Task 20）：Windows OCR 常把 l 认成 1/I、i 认成 l（「Ctr1+A1t+Q」「Shlft」）----
_L = r"[lI1|]"
_I = r"[iIl1|]"
_MOD_FIX = [  # (误识别的写法, 正确写法)：只在组合里纠正
    (re.compile(rf"Ctr{_L}", re.IGNORECASE), "Ctrl"),
    (re.compile(rf"A{_L}t", re.IGNORECASE), "Alt"),
    (re.compile(rf"Sh{_I}ft", re.IGNORECASE), "Shift"),
    (re.compile(rf"W{_I}n", re.IGNORECASE), "Win"),
]
_MOD = rf"(?:Ctr{_L}|A{_L}t|Sh{_I}ft|W{_I}n|Cmd|Fn|Option|Opt)"
_KEY = (rf"(?:{_MOD}|F\d{{1,2}}|Esc|Tab|Enter|Space|Backspace|Delete|Del|Insert|Ins|Home|End|PgUp|PgDn"
        r"|PageUp|PageDown|Up|Down|Left|Right|PrtSc|[A-Za-z0-9]|[`\-=\[\];',./\\])")
# 以修饰键开头、至少一个「+ 键」；最后一个键后面不能紧跟字母数字（Ctrl+Click 不是组合）
_COMBO = re.compile(rf"(?<![A-Za-z0-9]){_MOD}(?:\s*\+\s*{_KEY})+(?![A-Za-z0-9])", re.IGNORECASE)


def _fix_combo(m: re.Match) -> str:
    keys = re.split(r"(\s*\+\s*)", m.group(0))
    out = []
    for k in keys:
        for pat, good in _MOD_FIX:
            if pat.fullmatch(k):
                k = k if k.lower() == good.lower() else good   # 本来就对（含大小写）不动
                break
        out.append(k)
    return "".join(out)


def fix_key_combos(text: str) -> str:
    """把按键组合里修饰键的常见误识别纠正过来（Ctr1→Ctrl、A1t→Alt、Shlft→Shift、W1n→Win）。
    只在「修饰键 + 键」的组合里改：普通文字里的 A1、Win 不动。"""
    return _COMBO.sub(_fix_combo, text)


@dataclass(frozen=True)
class Protected:
    """pills：胶囊（literals）换成的占位符 -> 它是 literals 里的第几项（Task 22：译文回来后据此知道每个胶囊落在哪）。"""
    text: str
    slots: dict[str, str]
    pills: dict[str, int] = field(default_factory=dict)


def ignores_case(term: str) -> bool:
    """智能大小写：英文词条全小写才不分大小写；含任何大写字母（US、GPU、PyTorch）就只认原样。中文等照原样。"""
    return term.isascii() and term == term.lower()


def _cap_positions(term: str) -> tuple[int, ...]:
    """含大写的英文词条里，可以首字母大写的位置：每个词（开头或紧跟在非字母数字后面）开头的小写字母。
    「naive Bayes」也认「Naive Bayes」，「API gateway」也认「API Gateway」；词中间的字母和本来就大写的字母照旧只认原样。"""
    return tuple(i for i, c in enumerate(term)
                 if "a" <= c <= "z" and (i == 0 or not term[i - 1].isalnum()))


@lru_cache(maxsize=8192)
def term_pattern(term: str) -> re.Pattern:
    """术语表词条的匹配规则（relevant() 挑词条、protect() 换占位符共用）。每个词条只编译一次：
    re 自带的缓存只有 512 条，几千条的术语表每次翻译都会重新编译。"""
    if term.isascii():
        if ignores_case(term):
            body, flags = re.escape(term), re.IGNORECASE
        else:
            caps = set(_cap_positions(term))
            body = "".join(f"[{c}{c.upper()}]" if i in caps else re.escape(c) for i, c in enumerate(term))
            flags = 0
        return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", flags)
    return re.compile(re.escape(term))


@lru_cache(maxsize=8192)
def prefilter_needle(term: str) -> tuple[str, bool]:
    """快速预筛：(要找的子串, 是否在 fold_case(text) 里找)。term_pattern 能匹配到，这个子串就一定在。
    不分大小写的词条、以及有可大写首字母的词条，在折叠后的文字里找小写形式；其余（US、PyTorch、中文）在原文里找原样。"""
    if term.isascii() and (ignores_case(term) or _cap_positions(term)):
        return term.lower(), True
    return term, False


# re 的不分大小写会把 ſ 当成 s、İ/ı 当成 i（lower() 不会）：预筛用的折叠要比正则认得宽，不能漏
_FOLD = str.maketrans({"ſ": "s", "İ": "i", "ı": "i"})


def fold_case(text: str) -> str:
    """给「不分大小写的词条」做子串预筛用：词条能被 term_pattern 匹配到，就一定是 fold_case(text) 的子串。"""
    return text.translate(_FOLD).lower()


PillText = str | tuple[str, int | None]


def _literal_spans(text: str, literals: Iterable[PillText]) -> list[tuple[int, int, int]]:
    """每个胶囊在 text 里只占一处：给了起始下标（且那里正是这段字）就用那一处；
    否则取第一处还没被占、且两头不粘着别的字母数字的（「id」不吃 identifier、hide 里的 id）。
    同样的字有几个胶囊就换几处，不多换（单个汉字的胶囊不会把段里所有同样的字都换掉）。
    返回 (起, 止, 是 literals 里的第几项)，按位置排序；找不到位置的那项不在里面。"""
    items: list[tuple[str, int | None, int]] = []
    for i, lit in enumerate(literals):
        s, start = (lit, None) if isinstance(lit, str) else lit
        if s and s.strip():
            items.append((fix_key_combos(s), start, i))
    spans: list[tuple[int, int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y, _ in spans)

    # 先放有位置的，再按长度从长到短放没位置（或位置对不上）的：长的先占，短的不会切进长的里面
    rest = []
    for s, start, i in items:
        if start is not None and text[start:start + len(s)] == s and free(start, start + len(s)):
            spans.append((start, start + len(s), i))
        else:
            rest.append((s, i))
    for s, i in sorted(rest, key=lambda r: len(r[0]), reverse=True):
        for m in re.finditer(re.escape(s), text):
            a, b = m.span()
            if not free(a, b):
                continue
            if s[0].isascii() and s[0].isalnum() and a > 0 and text[a - 1].isascii() and text[a - 1].isalnum():
                continue
            if s[-1].isascii() and s[-1].isalnum() and b < len(text) and text[b].isascii() and text[b].isalnum():
                continue
            spans.append((a, b, i))
            break
    return sorted(spans)


def protect(text: str, keep_terms: Iterable[str] = (), literals: Iterable[PillText] = ()) -> Protected:
    """按键组合先纠正误识别（fix_key_combos），再整个换成占位符：译文里还原的是纠正后的写法。
    literals：要原样保留的原文片段（行内代码胶囊的文字，Task 20），最先换成占位符。每项是文字，
    或 (文字, 在 text 里的起始下标)；每项只换一处（见 _literal_spans）。纠错不改长度，下标仍然对得上。"""
    text = fix_key_combos(text)
    slots: dict[str, str] = {}

    def sub(m: re.Match) -> str:
        key = f"⟨{len(slots) + 1}⟩"
        slots[key] = m.group(0)
        return key

    out, pos = [], 0
    pills: dict[str, int] = {}
    for a, b, i in _literal_spans(text, literals):
        key = f"⟨{len(slots) + 1}⟩"
        slots[key] = text[a:b]
        pills[key] = i
        out += [text[pos:a], key]
        pos = b
    text = "".join(out) + text[pos:]
    terms = sorted({t for t in keep_terms if t and t.strip()}, key=len, reverse=True)
    patterns = [_URL, _EMAIL, *[term_pattern(t) for t in terms], _COMBO, *_LATER]
    for pat in patterns:
        text = pat.sub(sub, text)
    return Protected(text, slots, pills)


def restore(translated: str, slots: dict[str, str]) -> str:
    text = _SLOT_RE.sub(lambda m: f"⟨{m.group(1)}⟩", translated)
    missing = [k for k in slots if k not in text]
    if missing:
        raise MissingPlaceholder(missing)
    for k, v in slots.items():
        text = text.replace(k, v)
    return text


PillSpan = tuple[int, int, int]   # (起, 止, 第几个胶囊)：译文 text[起:止] 是这个胶囊的文字


def restore_spans(translated: str, slots: dict[str, str], pills: dict[str, int]) -> tuple[str, tuple[PillSpan, ...]]:
    """同 restore，另外给出每个胶囊（pills 里的占位符）在还原后的译文里的位置（Task 22：绘制时贴回原图那块胶囊）。
    拿不准的不给（绘制时就照旧当文字画）：译文里同一个胶囊的占位符出现了不止一次；
    或逐个换回后的文字和 restore 的结果对不上（某个保留原样的内容里本身带着像占位符的字）——那就一个也不给。"""
    final = restore(translated, slots)
    text = _SLOT_RE.sub(lambda m: f"⟨{m.group(1)}⟩", translated)
    out: list[str] = []
    spans: list[PillSpan] = []
    seen: dict[int, int] = {}
    pos = n = 0
    for m in re.finditer(r"⟨\d+⟩", text):
        key = m.group(0)
        if key not in slots:
            continue
        out.append(text[pos:m.start()])
        n += m.start() - pos
        v = slots[key]
        if key in pills:
            spans.append((n, n + len(v), pills[key]))
            seen[pills[key]] = seen.get(pills[key], 0) + 1
        out.append(v)
        n += len(v)
        pos = m.end()
    out.append(text[pos:])
    if "".join(out) != final:
        return final, ()
    return final, tuple(s for s in spans if seen[s[2]] == 1)

"""术语表：「原文 => 译法」指定译法；只写一个词表示保持原样不翻。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .protect import fold_case, prefilter_needle, term_pattern


@dataclass(frozen=True)
class Glossary:
    mappings: tuple[tuple[str, str], ...] = ()
    keep: tuple[str, ...] = ()


def parse_glossary(text: str) -> Glossary:
    mappings: list[tuple[str, str]] = []
    keep: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            src, dst = (p.strip() for p in line.split("=>", 1))
            if src and dst:
                mappings.append((src, dst))
        else:
            keep.append(line)
    return Glossary(tuple(mappings), tuple(keep))


def load_glossary(path: Path) -> Glossary:
    if not path.exists():
        return Glossary()
    return parse_glossary(path.read_text(encoding="utf-8-sig"))


def relevant(g: Glossary, text: str, direction: str) -> tuple[list[tuple[str, str]], list[str]]:
    """只挑这段文字里真出现过的词条，提示词不会因为术语表变长而变慢。
    几千条的术语表：先用子串查找（C 实现，很快）筛掉不可能出现的词条，只对剩下的跑正则（整词、大小写规则）。
    一个映射（指定译法）若每一处都落在另一个更长的命中词条里面（「无监督学习」里的「监督学习」、「毛利率」里的「利率」），
    就不写进提示词；它在别处还单独出现过就照旧给。比的时候 keep 词也算「更长的词条」（DAIC 在 DAIC-WOZ 里不给映射）。
    keep 词本身从不因此去掉：它们不进提示词，只交给 protect() 换占位符，免费翻译层（微软）的映射由客户端自己换成记号再换回（见 translate/microsoft.py）。"""
    folded = fold_case(text)
    spans: dict[str, list[tuple[int, int]]] = {}

    def present(term: str) -> bool:
        if term not in spans:
            needle, in_folded = prefilter_needle(term)
            spans[term] = ([m.span() for m in term_pattern(term).finditer(text)]
                           if needle in (folded if in_folded else text) else [])
        return bool(spans[term])

    pairs = g.mappings if direction == "en2zh" else tuple((d, s) for s, d in g.mappings)
    maps = [(s, d) for s, d in pairs if present(s)]
    keep = [k for k in g.keep if present(k)]
    inside = _contained({sp for v in spans.values() for sp in v})
    alone = {t for t, v in spans.items() if any(sp not in inside for sp in v)}
    return [(s, d) for s, d in maps if s in alone], keep


def _contained(spans: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """落在另一个更长的位置段里面的那些段。按起点升序、同起点终点降序扫一遍：
    起点更早、终点不早于它的段一定更长；同起点的只有终点更远的才算更长。"""
    out: set[tuple[int, int]] = set()
    far = -1                           # 起点比当前组早的所有段里最远的终点
    order = sorted(spans, key=lambda s: (s[0], -s[1]))
    i = 0
    while i < len(order):
        a = order[i][0]
        j = i
        while j < len(order) and order[j][0] == a:
            j += 1
        group_far = order[i][1]          # 同起点里最长的那段的终点
        for sa, sb in order[i:j]:
            if far >= sb or group_far > sb:
                out.add((sa, sb))
        far = max(far, group_far)
        i = j
    return out

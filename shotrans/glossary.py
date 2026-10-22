"""术语表：「原文 => 译法」指定译法；只写一个词表示保持原样不翻。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .protect import term_pattern


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
    """只挑这段文字里真出现过的词条，提示词不会因为术语表变长而变慢。"""
    pairs = g.mappings if direction == "en2zh" else tuple((d, s) for s, d in g.mappings)
    maps = [(s, d) for s, d in pairs if term_pattern(s).search(text)]
    keep = [k for k in g.keep if term_pattern(k).search(text)]
    return maps, keep

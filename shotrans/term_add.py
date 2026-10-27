"""浮窗里「添加术语」的纯逻辑：检查一条新词条、写进术语表文件（注释、行尾、BOM 原样保留），挑出用到它的段落。
词条一律按术语表的方向存：英文 => 中文；中文为空 = 保持原样、不翻。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .glossary_edit import HANZI, GlossaryDoc, Row, check_rows, load_doc, save_doc
from .models import Segment
from .protect import term_pattern


def side_of(text: str) -> str:
    """选中的文字该填哪个框：含汉字填「中文」，否则填「英文」。"""
    return "zh" if HANZI.search(text) else "en"


@dataclass(frozen=True)
class TermCheck:
    error: str | None = None                         # empty_en / empty_zh / bad_newline / bad_src：不能保存
    existing: tuple[int, str, str] | None = None     # 同一英文（见 same_term）已有的那一行：(行号, 原文, 译法)
    warnings: tuple[str, ...] = ()                   # dup_dst / single_hanzi：问一下再存


def same_term(a: str, b: str) -> bool:
    """两个英文词条在运行时会不会抢同一处文字：按术语表的大小写规则，一个能整个匹配另一个就算同一个
    （depression 和 Depression 是；US 和 us 也是，因为全小写的 us 不分大小写；GPU 和 Gpu 不是）。"""
    a, b = a.strip(), b.strip()
    return bool(a and b and (term_pattern(a).fullmatch(b) or term_pattern(b).fullmatch(a)))


def check_term(doc: GlossaryDoc, en: str, zh: str) -> TermCheck:
    en, zh = en.strip(), zh.strip()
    if not en:
        return TermCheck("empty_en")
    if len(en.splitlines()) > 1 or len(zh.splitlines()) > 1:
        return TermCheck("bad_newline")
    if en.startswith("#") or "=>" in en or "=>" in zh:
        return TermCheck("bad_src")
    hit = next(((i, r) for i, r in enumerate(doc.rows) if r.kind == "entry" and same_term(r.src, en)), None)
    existing = (hit[0], hit[1].src.strip(), hit[1].dst.strip()) if hit else None
    # 警告按「存完之后」的表算：被替换的那一行不算，新词条放最后
    rows = [r for i, r in enumerate(doc.rows) if hit is None or i != hit[0]] + [Row.entry(en, zh)]
    new = len(rows) - 1
    warnings = tuple(dict.fromkeys(x.code for x in check_rows(rows)
                                   if x.row == new and x.code in ("dup_dst", "single_hanzi")))
    return TermCheck(None, existing, warnings)


def put_term(doc: GlossaryDoc, en: str, zh: str) -> None:
    """同一英文（见 same_term）已有就改那一行（位置不变），没有就加在最后。"""
    en, zh = en.strip(), zh.strip()
    for r in doc.rows:
        if r.kind == "entry" and same_term(r.src, en):
            r.src, r.dst = en, zh
            return
    doc.rows.append(Row.entry(en, zh))


def save_term(path: Path, en: str, zh: str) -> None:
    """每次都从磁盘重读再写（设置窗口、记事本可能刚改过）。读不了（不是 UTF-8、没权限）就抛，文件不动。"""
    doc = load_doc(path)
    put_term(doc, en, zh)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_doc(doc, path)


def mentions(segments: list[Segment], en: str, zh: str) -> list[Segment]:
    """用到这个词条的段（英文或中文那边出现了，整词、大小写规则与运行时一致）。跳过的段不算。"""
    pats = [term_pattern(s) for s in (en.strip(), zh.strip()) if s]
    return [s for s in segments if not s.skip and any(p.search(s.text) for p in pats)]

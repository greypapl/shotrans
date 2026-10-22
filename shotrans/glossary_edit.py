"""术语表编辑器的纯逻辑：解析时保留注释和空行、原样往返；保存前检查；导入（追加）、导出。
格式与 glossary.parse_glossary 一致：「原文 => 译法」指定译法；只写一个词表示保持原样；# 开头是注释；
「a =>」「=> b」这种一边为空的箭头行运行时整行忽略，这里标成 ignored，原样保留、不参与检查。
切行与运行时一样用 str.splitlines()（\\n、\\r\\n、单独的 \\r 等都算行尾），每行记住自己的行尾：
没改的行原样写回；改过的行和新行用文件里出现最多的行尾（空文件用 \\n）；文件末尾有没有换行保持原样。"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HANZI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
NEWLINES = ("\n", "\r\n", "\r")


@dataclass
class Row:
    kind: str                       # entry / comment / blank / ignored
    src: str = ""
    dst: str = ""
    raw: str = ""                   # 文件里的原行（不含行尾）：条目没改动时原样写回
    orig: tuple[str, str] | None = None
    eol: str = ""                   # 文件里这一行原来的行尾；新行、导入的行为空（用文档的行尾）

    @classmethod
    def entry(cls, src: str, dst: str = "") -> "Row":
        return cls("entry", src, dst)

    @classmethod
    def comment(cls, text: str) -> "Row":
        return cls("comment", raw=text)

    @classmethod
    def blank(cls, raw: str = "") -> "Row":
        return cls("blank", raw=raw)

    @classmethod
    def ignored(cls, raw: str) -> "Row":
        return cls("ignored", raw=raw)

    def unchanged(self) -> bool:
        return self.kind != "entry" or (self.orig is not None and (self.src, self.dst) == self.orig)

    def line(self) -> str | None:
        if self.unchanged():
            return self.raw
        src, dst = self.src.strip(), self.dst.strip()
        if not src and not dst:
            return None  # 新增后没填：不写
        return f"{src} => {dst}" if dst else src


@dataclass
class GlossaryDoc:
    rows: list[Row] = field(default_factory=list)
    newline: str = "\n"
    trailing_newline: bool = True
    bom: bool = False


def parse_line(line: str) -> Row:
    s = line.strip()
    if not s:
        return Row.blank(line)
    if s.startswith("#"):
        return Row.comment(line)
    if "=>" in s:
        src, dst = (p.strip() for p in s.split("=>", 1))
        if not (src and dst):
            return Row.ignored(line)  # 运行时整行忽略（不是「保持原样」），这里也不当条目
    else:
        src, dst = s, ""
    return Row("entry", src, dst, line, (src, dst))


def _split_eol(chunk: str) -> tuple[str, str]:
    parts = chunk.splitlines()
    body = parts[0] if parts else ""
    return body, chunk[len(body):]


def parse_doc(text: str) -> GlossaryDoc:
    bom = text.startswith("\ufeff")
    if bom:
        text = text[1:]
    rows: list[Row] = []
    for chunk in text.splitlines(keepends=True):  # 与运行时 parse_glossary 的 splitlines() 同一套切法
        body, eol = _split_eol(chunk)
        r = parse_line(body)
        r.eol = eol
        rows.append(r)
    counts = Counter(r.eol for r in rows if r.eol in NEWLINES)
    newline = counts.most_common(1)[0][0] if counts else "\n"
    trailing = not rows or rows[-1].eol != ""
    return GlossaryDoc(rows, newline, trailing, bom)


def dump_doc(doc: GlossaryDoc, with_bom: bool | None = None) -> str:
    out: list[list[str]] = []
    for r in doc.rows:
        ln = r.line()
        if ln is not None:
            out.append([ln, r.eol if r.unchanged() and r.eol else doc.newline])
    if out and not doc.trailing_newline:
        out[-1][1] = ""
    body = "".join(ln + eol for ln, eol in out)
    bom = doc.bom if with_bom is None else with_bom
    return ("\ufeff" if bom else "") + body


@dataclass(frozen=True)
class Issue:
    row: int
    code: str      # empty_src / bad_newline / bad_src / dup_src / dup_dst / single_hanzi
    blocking: bool


def check_rows(rows: list[Row]) -> list[Issue]:
    issues: list[Issue] = []
    by_src: dict[str, list[int]] = {}
    by_dst: dict[str, dict[str, list[int]]] = {}
    for i, r in enumerate(rows):
        if r.kind != "entry":
            continue
        src, dst = r.src.strip(), r.dst.strip()
        if not src and not dst:
            continue  # 新增后没填的空行：保存时丢掉，不算问题
        if not src:
            issues.append(Issue(i, "empty_src", True))
            continue
        if len(src.splitlines()) > 1 or len(dst.splitlines()) > 1:
            issues.append(Issue(i, "bad_newline", True))  # 粘贴进来的换行：写回后断成两行，意思变了
            continue
        if src.startswith("#") or "=>" in src:
            issues.append(Issue(i, "bad_src", True))  # 写回后会变成注释、或在别处断开：意思变了
            continue
        key = src.lower()  # 英文不分大小写；中文 lower() 不变
        by_src.setdefault(key, []).append(i)
        if dst:
            by_dst.setdefault(dst, {}).setdefault(key, []).append(i)
            if len(dst) == 1 and HANZI.match(dst):
                issues.append(Issue(i, "single_hanzi", False))
    for idxs in by_src.values():
        if len(idxs) > 1:
            issues += [Issue(i, "dup_src", False) for i in idxs]
    for srcs in by_dst.values():
        if len(srcs) > 1:  # 同一译法对应多个不同原文：中译英时反向有歧义
            issues += [Issue(i, "dup_dst", False) for idxs in srcs.values() for i in idxs]
    return sorted(issues, key=lambda x: (x.row, x.code))


def has_blocking(issues: list[Issue]) -> bool:
    return any(x.blocking for x in issues)


def import_text(doc: GlossaryDoc, text: str) -> int:
    new = parse_doc(text.lstrip("\ufeff"))
    for r in new.rows:
        r.eol = ""  # 导入的行一律用本文档的行尾，不把别的文件的换行混进来
    doc.rows.extend(new.rows)
    return sum(1 for r in new.rows if r.kind == "entry")


def load_doc(path: Path) -> GlossaryDoc:
    try:
        data = Path(path).read_bytes()  # 不先 exists()：它会把读不了的情况当成不存在，保存时就把原文件覆盖了
    except FileNotFoundError:
        return GlossaryDoc()
    return parse_doc(data.decode("utf-8"))  # 不是 UTF-8 抛 UnicodeDecodeError；BOM 由 parse_doc 识别


def save_doc(doc: GlossaryDoc, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(dump_doc(doc))
            f.flush()
            os.fsync(f.fileno())  # 先落盘再替换：断电时不会换上一个没写完的文件
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)  # 成功时已被换走；失败时别留 .tmp
        except OSError:
            pass


def export_doc(doc: GlossaryDoc, path: Path) -> None:
    path.write_text(dump_doc(doc, with_bom=False), encoding="utf-8", newline="")

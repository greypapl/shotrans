"""发布前检查：个人数据扫描、体积上限、打 zip、SHA-256。命中时只报规则名和位置，绝不输出命中的值。"""
from __future__ import annotations

import hashlib
import io
import re
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

LIMIT_BYTES = 50 * 1024 * 1024
MAX_ZIP_DEPTH = 5
# 固定规则：(规则名, UTF-8 字节正则, UTF-16-LE 字节正则)。UTF-16 形式手写：每个字面字符后跟 \x00。
FIXED = (
    ("lan_ip", rb"172\.25\.", rb"1\x007\x002\x00\.\x002\x005\x00\.\x00"),
    # 只有 UTF-16 形式要求 sk- 前面不是 ASCII 字母/数字（Qt6Core.dll 里连写的 CLDR 时区名如 "Irkoetsk-tyd…" 会误报）。
    # UTF-8 形式不能加：marshal 存短字符串时内容前一个字节是长度，长度 48–57/65–90/97–122 时恰好是数字/字母，
    # PYZ 里的 key 常量会被漏掉。
    ("api_key", rb"sk-[A-Za-z0-9_-]{20,}",
     rb"(?<![A-Za-z0-9]\x00)s\x00k\x00-\x00(?:[A-Za-z0-9_-]\x00){20,}"),
    ("author_path", rb"(?i:C:[\\/]+Users[\\/]+greypapl)",
     rb"(?i:C\x00:\x00(?:[\\/]\x00)+U\x00s\x00e\x00r\x00s\x00(?:[\\/]\x00)+g\x00r\x00e\x00y\x00p\x00a\x00p\x00l\x00)"),
)
Extractor = tuple[Callable[[Path], bool], Callable[[Path], Iterable[tuple[str, bytes]]]]


@dataclass(frozen=True)
class Hit:
    path: str
    rule: str


_MISSING = object()


def _default_at(defaults: dict | None, keys: tuple) -> object:
    """按表名路径在默认值树里找；路径里有列表下标、某一层不是表、键不存在，都算对不上（返回 _MISSING）。"""
    node: object = defaults
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return _MISSING
        node = node[k]
    return node


def _walk_config(node, path: str, keys: tuple, defaults: dict | None, out: list[tuple[str, str]]) -> None:
    """走遍整棵 TOML 树（嵌套表、列表）；字符串值长度 >= 8 才成规则。列表项名写成 key[0]。
    与默认值树里同一路径的值完全相等的不成规则（公开默认值不是个人数据）；对不上的路径一律照扫。"""
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_config(v, f"{path}.{k}" if path else k, keys + (k,), defaults, out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_config(v, f"{path}[{i}]", keys + (i,), defaults, out)
    elif isinstance(node, str) and len(node) >= 8:
        d = _default_at(defaults, keys)
        if not (isinstance(d, str) and d == node):
            out.append((f"config_value_{path}", node))


def _config_values(cfg: Path | None, defaults: dict | None = None) -> list[tuple[str, str]]:
    if cfg is None or not cfg.exists():
        return []
    with open(cfg, "rb") as f:
        data = tomllib.load(f)
    out: list[tuple[str, str]] = []
    _walk_config(data, "", (), defaults, out)
    return out


def default_rules(legacy_config: Path | None, defaults: dict | None = None) -> list[tuple[str, re.Pattern[bytes]]]:
    """固定规则 + 旧配置里每个长度 >= 8 的字符串值；每条各出 UTF-8 与 UTF-16-LE 两种形式（规则名相同）。
    defaults：程序默认配置的表树（config_to_dict(Config()) 的形状）；旧配置里与同一路径默认值完全相等的值跳过。"""
    rules: list[tuple[str, re.Pattern[bytes]]] = []
    for name, p8, p16 in FIXED:
        rules.append((name, re.compile(p8)))
        rules.append((name, re.compile(p16)))
    for name, v in _config_values(legacy_config, defaults):
        rules.append((name, re.compile(re.escape(v.encode("utf-8")))))
        rules.append((name, re.compile(re.escape(v.encode("utf-16-le")))))
    return rules


def _scan_bytes(path: str, data: bytes, rules, found: set[tuple[str, str]]) -> None:
    """同一（路径, 规则名）只记一次；只记名字和位置，不保留匹配到的内容。"""
    for name, pat in rules:
        if pat.search(data) is not None:
            found.add((path, name))


def _is_container(data: bytes) -> bool:
    """字节本身是 zip 归档（不看扩展名：.whl/.jar/.egg 等都是）。"""
    return data[:4] == b"PK\x03\x04" and zipfile.is_zipfile(io.BytesIO(data))


def _scan_blob(path: str, data: bytes, rules, found, depth: int = 0, force_zip: bool = False) -> int:
    """扫一份字节；是 zip 就逐条递归（名 外层!内层!条目），否则扫原始字节。返回叶子条目数。
    嵌套超过 MAX_ZIP_DEPTH 层直接抛，不静默跳过。force_zip：按 zip 打开，打不开就抛（顶层 .zip 文件用）。"""
    if force_zip or _is_container(data):
        if depth >= MAX_ZIP_DEPTH:
            raise RuntimeError(f"zip 嵌套超过 {MAX_ZIP_DEPTH} 层：{path}")
        n = 0
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if not info.is_dir():
                    n += _scan_blob(f"{path}!{info.filename}", z.read(info), rules, found, depth + 1)
        return n
    _scan_bytes(path, data, rules, found)
    return 1


def scan_tree(root: Path, rules, extractors: Iterable[Extractor] = ()) -> tuple[list[Hit], int]:
    """遍历 root 下所有文件。任何字节上是 zip 的文件（不论扩展名）或 zip 内的 zip 条目都当容器逐条递归扫，
    最多 MAX_ZIP_DEPTH 层，超过抛 RuntimeError；非容器扫原始字节。扩展名 .zip 的文件必须能按 zip 打开，否则抛。
    extractor 命中的文件（优先于 zip 判断）先扫外层本身，再扫 iter_fn 给的条目（条目内不再递归）。
    条目数（叶子）：普通文件 1；容器（外层及嵌套的）自身不计，只计其内部叶子；extractor 命中的计外层 1 + 条目数。
    读不了就抛出。"""
    extractors = list(extractors)
    found: set[tuple[str, str]] = set()
    count = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        hit_ext = next((it for m, it in extractors if m(p)), None)
        if hit_ext is not None:
            _scan_bytes(rel, p.read_bytes(), rules, found)
            count += 1
            for entry, data in hit_ext(p):
                _scan_bytes(f"{rel}!{entry}", data, rules, found)
                count += 1
        else:
            count += _scan_blob(rel, p.read_bytes(), rules, found, force_zip=p.suffix.lower() == ".zip")
    return [Hit(path, rule) for path, rule in sorted(found)], count


def format_hits(hits: list[Hit]) -> str:
    return "\n".join(f"{h.rule}  {h.path}" for h in hits)


def largest_files(root: Path, n: int = 20) -> list[tuple[str, int]]:
    files = [(p.relative_to(root).as_posix(), p.stat().st_size) for p in root.rglob("*") if p.is_file()]
    return sorted(files, key=lambda x: (-x[1], x[0]))[:n]


def check_size(zip_path: Path, limit: int, root: Path) -> str | None:
    size = zip_path.stat().st_size
    if size <= limit:
        return None
    rows = "\n".join(f"  {s / 1048576:8.2f} MB  {p}" for p, s in largest_files(root))
    return f"zip 体积 {size} 字节超过上限 {limit} 字节。最大的 20 个文件：\n{rows}"


def make_zip(src_dir: Path, zip_path: Path, top_name: str) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                z.write(p, f"{top_name}/{p.relative_to(src_dir).as_posix()}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

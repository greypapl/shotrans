"""最小的 TOML 写入：只支持「节 → 键 → 标量（str/bool/int/float）」两层，够写配置用。值为 None 的键跳过。"""
from __future__ import annotations

import math
import re

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_ESC = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _str(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _key(k: str) -> str:
    return k if _BARE.match(k) else _str(k)


def _value(v) -> str:
    if isinstance(v, bool):  # 先于 int：bool 是 int 的子类
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError(f"TOML 配置里不写非有限数：{v!r}")
        return repr(v)  # 6.0 → "6.0"，0.1 → "0.1"，1e16 → "1e+16"（都是合法 TOML 浮点数）
    if isinstance(v, str):
        return _str(v)
    raise TypeError(f"不支持写入的类型：{type(v).__name__}")


def dump_toml(data: dict, header: str = "") -> str:
    lines = [f"# {ln}" if ln else "#" for ln in header.splitlines()]
    if lines:
        lines.append("")
    for section, table in data.items():
        if not isinstance(table, dict):
            raise TypeError(f"节 {section} 必须是 dict")
        lines.append(f"[{_key(section)}]")
        for k, v in table.items():
            if v is None:
                continue
            lines.append(f"{_key(k)} = {_value(v)}")
        lines.append("")
    return "\n".join(lines)

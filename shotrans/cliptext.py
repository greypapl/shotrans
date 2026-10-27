"""复制后翻译：剪贴板文字 → 段落。
空行一定分段；单个换行只有上一行以句末标点结尾、或下一行像列表项开头时才分段，
否则接成一行（PDF 里复制出来的硬换行、断词连字符，拼法同截图分段的 join_lines）。"""
from __future__ import annotations

from .layout import is_list_start, join_lines
from .models import Box, OcrLine, Segment

MAX_CHARS = 20000  # 再长就请用户分几次复制：一次发太多，等很久才有第一段
SENTENCE_END = "。！？；：.!?;:」”"


def split_paragraphs(text: str) -> list[str]:
    groups: list[list[str]] = []
    cur: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if cur:
                groups.append(cur)
                cur = []
            continue
        if cur and (cur[-1].endswith(tuple(SENTENCE_END)) or is_list_start(line)):
            groups.append(cur)
            cur = []
        cur.append(line)
    if cur:
        groups.append(cur)
    return [p for p in (join_lines(g) for g in groups) if p]


def to_segments(paragraphs: list[str]) -> list[Segment]:
    """翻译路由要的是 Segment：没有截图，框一律给一个占位的 1×1。"""
    return [Segment(i, p, [OcrLine(p, Box(0, 0, 1, 1))]) for i, p in enumerate(paragraphs, 1)]

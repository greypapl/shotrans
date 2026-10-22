"""行内代码「胶囊」（Task 20）：句子里带浅色底的圆角框（`Ctrl+Alt+Q`、工具栏按钮名），框里的字原样保留不翻。

按词找：每个词取框里最多的颜色当它的底色 c，看 c 是不是一条「贴着这一行、上下都有边」的色带——
1. 竖直：从词框上沿往上、下沿往下逐行看，c 色占词宽的 PILL_ROW 以上算还在色带里；色带上下各最多伸出
   PILL_PAD 倍行高就要碰到边（边那一行本身是一种均匀的、和 c 不同的颜色），总高不超过 PILL_MAX_H 倍行高。
   普通白底上的字往上下是一大片白，碰不到边，不是胶囊；网页的灰色标签页、页眉色带上下留白太多，也不是。
2. 边外：越过边再往外 PILL_BEYOND 像素的那一行也和 c 不同——表格单元格上下有线、线外还是白的，不是胶囊。
3. 横向：用色带里字上下的留白那几行，从词的中间往左右找色带的两头（c 色占这几行的一半以下就到头）；
   左头离胶囊里第一个词不超过 PILL_HUG_LEFT 倍行高，右头离最后一个词不超过 PILL_HUG_RIGHT 倍行高
   （右边常有 OCR 没认出的图标、符号，放宽些）。
同一个色带里的连续几个词合成一个胶囊。整段全是胶囊的（独立的按钮、选中的列表项）不算，由 layout 处理。"""
from __future__ import annotations

import numpy as np

from .models import Box, OcrLine, Pill
from .ocr import join_words

PILL_TOL = 8           # 和底色 c 每个通道相差不超过这么多算「c 色」（浅灰胶囊 240 和白底 252 只差 12）
PILL_ROW = 0.6         # 一行里 c 色占词宽的这么多以上，算还在色带里
PILL_PAD = 0.5         # 色带在字上下各最多伸出 0.5 倍行高
PILL_MAX_H = 1.8       # 色带总高不超过 1.8 倍行高
PILL_EDGE_UNIFORM = 0.8  # 边那一行要这么多像素是同一种颜色（字的笔画、上下一行的字不算边）
PILL_BEYOND = 2        # 越过边再往外几像素看「外面」（越过 1 像素的描边）
PILL_COL = 0.5         # 横向：留白行里 c 色占一半以上的列算还在胶囊里
PILL_HUG_LEFT = 1.5
PILL_HUG_RIGHT = 3.0
PILL_SAT = 30          # 胶囊底色要是灰的（三个通道最大最小差 ≤ 30）：代码胶囊是浅灰/深灰（含 GitHub 那种略带蓝的灰），
                       # Ctrl+F 的黄/橙高亮、选中文字的蓝底是彩色的，不是胶囊


def _mode_color(px: np.ndarray) -> np.ndarray:
    """最多的那种颜色（字的笔画颜色分散在抗锯齿过渡里，底色是同一种）。"""
    flat = px.reshape(-1, 3).astype(np.int32)
    packed = (flat[:, 0] << 16) | (flat[:, 1] << 8) | flat[:, 2]   # 一维整数比按行 unique 快得多
    colors, counts = np.unique(packed, return_counts=True)
    v = int(colors[int(np.argmax(counts))])
    return np.array([v >> 16, (v >> 8) & 255, v & 255])


def _near(px: np.ndarray, c: np.ndarray) -> np.ndarray:
    return np.abs(px.astype(int) - c).max(axis=-1) <= PILL_TOL


def _edge(arr: np.ndarray, c: np.ndarray, x0: int, x1: int, start: int, step: int, limit: int) -> int | None:
    """从 start 行起按 step 方向逐行走，返回色带的边那一行（第一行 c 色占比 < PILL_ROW）；
    走了 limit 行还没到边、到了图片边缘、边不均匀、或边外和 c 一样，都返回 None。"""
    h = arr.shape[0]
    # 一次取出要走的那几行（到图片边缘为止），一起算每行的 c 色占比
    end = start + step * (limit + 1)
    ys = np.arange(start, end, step)
    ys = ys[(ys >= 0) & (ys < h)]
    if len(ys) == 0 or ys[0] != start:
        return None
    frac = _near(arr[ys, x0:x1], c).mean(axis=1)
    hits = np.nonzero(frac < PILL_ROW)[0]
    if len(hits) == 0:
        return None          # 走完 limit 行、或走到图片边缘还没到边
    y = int(ys[hits[0]])
    row = arr[y, x0:x1]
    med = np.median(row.reshape(-1, 3), axis=0)
    if _near(row, med).mean() < PILL_EDGE_UNIFORM or _near(med, c):
        return None
    yb = y + step * PILL_BEYOND
    if 0 <= yb < h and _near(np.median(arr[yb, x0:x1].reshape(-1, 3), axis=0), c):
        return None
    return y


def pill_around(arr: np.ndarray, wb: Box, line_h: int) -> Box | None:
    """词框 wb 所在的胶囊色带的外框；不在胶囊里返回 None。"""
    H, W = arr.shape[:2]
    x0, x1 = max(wb.x, 0), min(wb.right, W)
    y0, y1 = max(wb.y, 0), min(wb.bottom, H)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    c = _mode_color(arr[y0:y1, x0:x1])
    if c.max() - c.min() > PILL_SAT:
        return None
    limit = int(np.ceil(PILL_PAD * line_h))
    top = _edge(arr, c, x0, x1, y0 - 1, -1, limit)
    bottom = _edge(arr, c, x0, x1, y1, 1, limit)
    if top is None or bottom is None or bottom - top - 1 > PILL_MAX_H * line_h:
        return None
    pad_rows = [*range(top + 1, y0), *range(y1, bottom)]
    if not pad_rows:
        return None
    band = arr[pad_rows]                                  # (行数, W, 3)
    inside = _near(band, c).mean(axis=0) >= PILL_COL      # 每一列是不是还在胶囊里
    mid = (x0 + x1) // 2
    if not inside[mid]:
        return None
    left = mid
    while left > 0 and inside[left - 1]:
        left -= 1
    right = mid
    while right < W - 1 and inside[right + 1]:
        right += 1
    return Box(left, top + 1, right + 1 - left, bottom - top - 1)


def find_pills(arr: np.ndarray, line: OcrLine) -> list[Pill]:
    """这一行里的胶囊（按 x 顺序）。没有词级框的行（旧快照）找不出，返回空。"""
    words = line.words
    if not words:
        return []
    h = line.box.h

    def centre_in(w, rs):
        cx, cy = w.box.x + w.box.w / 2, w.box.y + w.box.h / 2
        return next((r for r in rs if r is not None and r.contains(cx, cy)), None)

    rects: list[Box | None] = []
    for w in words:
        r = centre_in(w, rects)   # 已经找到的胶囊盖住了这个词：不用再找
        rects.append(r if r is not None else pill_around(arr, w.box, h))
    # 第二遍：自己找不出（如胶囊开头的红色圆点，框里最多的是圆点的颜色）、但落在别的词找到的胶囊里的，也算
    rects = [r if r is not None else centre_in(w, rects) for w, r in zip(words, rects)]
    pills: list[Pill] = []
    i = 0
    while i < len(words):
        r = rects[i]
        j = i
        while r is not None and j + 1 < len(words) and rects[j + 1] is not None and _overlap(rects[j + 1], r):
            j += 1
            r = r.union(rects[j])
        if r is not None and r.x >= words[i].box.x - PILL_HUG_LEFT * h \
                and r.right <= words[j].box.right + PILL_HUG_RIGHT * h:
            pills.append(Pill(join_words([w.text for w in words[i:j + 1]]), r))
        i = j + 1
    return pills


def _overlap(a: Box, b: Box) -> bool:
    return min(a.right, b.right) > max(a.x, b.x) and min(a.bottom, b.bottom) > max(a.y, b.y)

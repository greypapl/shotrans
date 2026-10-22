"""识别错字率（CER）。"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)              # ﬁ → fi
    s = re.sub(r"(\w)-\s*\n\s*([a-z])", r"\1\2", s)    # 行末断词：depres-\nsion
    return re.sub(r"\s+", " ", s).strip()


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    """顺序敏感的错字率：编辑距离 / 真值长度（都先 normalize）。双栏、表格的真值顺序和
    识别顺序不同时会虚高，只作参考。"""
    ref, hyp = normalize(ref), normalize(hyp)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / len(ref)


def _bag(s: str) -> Counter:
    return Counter(c for c in normalize(s) if not c.isspace())


def bag_errors(ref: str, hyp: str) -> tuple[int, int]:
    """(缺, 多)：真值里有、识别结果里少了的字符数；识别结果里多出来的字符数。不计空白。"""
    r, h = _bag(ref), _bag(hyp)
    return sum((r - h).values()), sum((h - r).values())


def bag_cer(ref: str, hyp: str) -> float:
    """与顺序无关的错字率（中途关卡用它）。
    定义：两边先 normalize，去掉所有空白，各自看成「字符袋」（只数每个字符出现几次，不管位置）；
    错字率 = max(缺, 多) / 真值字符数。
    为什么是 max 而不是 缺+多：允许任意调换顺序时，把识别结果改成真值最少要 max(缺, 多) 次增删改
    （一次替换同时消掉一个「缺」和一个「多」），这和普通错字率按编辑次数计数的口径一致。
    不计空白：拼接时中英文之间加不加空格、换行变空格，都不是识别错误。
    空真值：识别结果也空算 0，否则算 1（同 cer）。"""
    total = sum(_bag(ref).values())
    missing, extra = bag_errors(ref, hyp)
    if not total:
        return 0.0 if not extra else 1.0
    return max(missing, extra) / total

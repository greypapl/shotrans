"""对有道的盲评。
make ：生成 bench/out/blind.html，每张图左右随机摆放「有道」和「本软件」，答案藏在 blind_key.json。
score：读你导出的 bench/out/blind_answers.json，统计「不比有道差」的张数。"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "bench" / "out"
YOUDAO = ROOT / "bench" / "youdao"


def assign_sides(ids: list[str], rng: random.Random) -> dict[str, dict[str, str]]:
    key = {}
    for i in ids:
        a, b = ("ours", "youdao") if rng.random() < 0.5 else ("youdao", "ours")
        key[i] = {"甲": a, "乙": b}
    return key


def score(key: dict, answers: dict) -> tuple[int, int]:
    """返回（不比有道差的张数，已作答张数）。"平" 算不比有道差。"""
    good = total = 0
    for i, sides in key.items():
        ans = answers.get(i)
        if ans not in ("甲", "乙", "平"):
            continue
        total += 1
        if ans == "平" or sides[ans] == "ours":
            good += 1
    return good, total


MARGIN = 8          # 选框线 + 圆形手柄（约 8-10px）的清除余量
MIN_RUN = 40        # 一条边框线至少多长（像素）才算
REL_RUN = 0.4       # 且不短于最长红线的这个比例
GAP = 12            # 手柄处允许的断口
COVER = 0.85        # 一段红线里红像素占比，红字笔画会被这条挡掉
EDGE_STRIP = 12     # 判断「图边上有没有红」的带宽
EDGE_RED = 3        # 带里（放宽的）红像素 >= 这么多才算有


def _red(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGB"))
    return (a[..., 0] >= 200) & (a[..., 1] <= 90) & (a[..., 2] <= 90)


def _pink(img: Image.Image) -> np.ndarray:
    """放宽的红：连抗锯齿后发粉的手柄残边也算（只用来判断图边上有没有选框痕迹）。"""
    a = np.asarray(img.convert("RGB")).astype(int)
    return (a[..., 0] >= 200) & (a[..., 0] - a[..., 1] >= 70) & (a[..., 0] - a[..., 2] >= 70)


def _lines(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """沿行扫描，返回红色长线簇 (起行, 止行, 最小x, 最大x)。"""
    best = []
    for r in range(mask.shape[0]):
        xs = np.flatnonzero(mask[r])
        if len(xs) < MIN_RUN * COVER:
            best.append(None)
            continue
        cand = None
        for g in np.split(xs, np.flatnonzero(np.diff(xs) > GAP) + 1):
            length = int(g[-1] - g[0] + 1)
            if length >= MIN_RUN and len(g) / length >= COVER and (cand is None or length > cand[0]):
                cand = (length, int(g[0]), int(g[-1]))
        best.append(cand)
    longest = max((c[0] for c in best if c), default=0)
    thr = max(MIN_RUN, REL_RUN * longest)
    out: list[list[int]] = []
    for r, c in enumerate(best):
        if not c or c[0] < thr:
            continue
        if out and r - out[-1][1] <= 1:
            out[-1][1] = r
            out[-1][2] = min(out[-1][2], c[1])
            out[-1][3] = max(out[-1][3], c[2])
        else:
            out.append([r, r, c[1], c[2]])
    return [tuple(o) for o in out]


def _split(lines, other, size):
    """把簇分成（前边, 后边）；只有一条时按它离垂直方向红线两端哪头近、没有就按在图的哪一半来判断。"""
    if not lines:
        return None, None
    if len(lines) >= 2:
        return lines[0], lines[-1]
    ln = lines[0]
    c = (ln[0] + ln[1]) / 2
    if other:
        lo = min(o[2] for o in other)
        hi = max(o[3] for o in other)
        near_front = abs(c - lo) <= abs(c - hi)
    else:
        near_front = c < size / 2
    return (ln, None) if near_front else (None, ln)


def _analyze(img: Image.Image):
    m = _red(img)
    h, w = m.shape
    hs = _lines(m)
    vs = _lines(m.T)
    top, bottom = _split(hs, vs, h)
    left, right = _split(vs, hs, w)
    x0 = left[1] + 1 if left else 0
    x1 = right[0] if right else w
    y0 = top[1] + 1 if top else 0
    y1 = bottom[0] if bottom else h
    found = {"left": left is not None, "right": right is not None,
             "top": top is not None, "bottom": bottom is not None}
    if not any(found.values()) or x1 - x0 <= 2 * MARGIN or y1 - y0 <= 2 * MARGIN:
        return None, {k: False for k in found}, _pink(img)
    return (x0, y0, x1, y1), found, _pink(img)


def selection_box(img: Image.Image):
    """有道截图里红色选框的内侧区域 (x0,y0,x1,y1)；找不到边框线返回 None（=整张图）。"""
    return _analyze(img)[0]


def fair_crop_box(img: Image.Image) -> tuple[int, int, int, int]:
    box, found, m = _analyze(img)
    h, w = m.shape
    x0, y0, x1, y1 = box or (0, 0, w, h)
    strips = {"left": m[:, :EDGE_STRIP], "right": m[:, w - EDGE_STRIP:],
              "top": m[:EDGE_STRIP], "bottom": m[h - EDGE_STRIP:]}
    shrink = {k: found[k] or int(strips[k].sum()) >= EDGE_RED for k in strips}
    if shrink["left"]:
        x0 += MARGIN
    if shrink["right"]:
        x1 -= MARGIN
    if shrink["top"]:
        y0 += MARGIN
    if shrink["bottom"]:
        y1 -= MARGIN
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return 0, 0, w, h
    return x0, y0, x1, y1


def residual_red(crop: Image.Image) -> bool:
    """裁剪后四边 4px 带里还有一团红（选框/手柄没去干净）。"""
    m = _pink(crop)
    s = 4
    return any(int(part.sum()) >= EDGE_RED for part in (m[:, :s], m[:, -s:], m[:s], m[-s:]))


def _src(i: str, who: str) -> str:
    return f"blind/{i}_{who}.png"


def make() -> None:
    ids = sorted(p.stem.split("_")[0] for p in OUT.glob("*_ours.png") if (YOUDAO / f"{p.stem.split('_')[0]}.png").exists())
    key = assign_sides(ids, random.Random())
    (OUT / "blind_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "blind").mkdir(exist_ok=True)
    for i in ids:
        yd = Image.open(YOUDAO / f"{i}.png").convert("RGB")
        ours = Image.open(OUT / f"{i}_ours.png").convert("RGB")
        box = fair_crop_box(yd)
        note = ""
        if ours.size != yd.size:
            note = f"  [警告：两边尺寸不同 有道{yd.size} 本软件{ours.size}]"
        yc, oc = yd.crop(box), ours.crop(box)
        yc.save(OUT / "blind" / f"{i}_youdao.png")
        oc.save(OUT / "blind" / f"{i}_ours.png")
        if residual_red(yc):
            note += "  [警告：有道裁剪边缘仍有红色残留]"
        print(f"{i}: 原图 {yd.size[0]}x{yd.size[1]} 裁剪框 {box}{note}")
    rows = []
    for i in ids:
        rows.append(
            f"<section><h2>{i}</h2><div class='pair'>"
            f"<figure><figcaption>甲</figcaption><img src='{_src(i, key[i]['甲'])}'></figure>"
            f"<figure><figcaption>乙</figcaption><img src='{_src(i, key[i]['乙'])}'></figure></div>"
            + "".join(f"<label><input type='radio' name='{i}' value='{v}'> {t}</label>"
                      for v, t in (("甲", "甲更好"), ("乙", "乙更好"), ("平", "差不多")))
            + "</section>")
    html = ("<!doctype html><meta charset='utf-8'><title>截图翻译盲评</title>"
            "<style>body{font-family:'Microsoft YaHei';margin:16px}.pair{display:flex;gap:12px;flex-wrap:wrap}"
            "figure{margin:0}img{max-width:100%;border:1px solid #ccc}section{margin-bottom:32px}"
            "label{margin-right:16px}textarea{width:100%;height:120px}</style>"
            "<h1>截图翻译盲评</h1><p>每张图选哪边翻得好（看准确、通顺、排版）。全部选完点最下面的按钮，"
            "把文本框里的内容保存为 bench/out/blind_answers.json。</p>"
            + "".join(rows)
            + "<button onclick=\"const a={};document.querySelectorAll('input:checked').forEach(e=>a[e.name]=e.value);"
              "document.getElementById('o').value=JSON.stringify(a,null,1)\">生成结果</button>"
              "<textarea id='o'></textarea>")
    (OUT / "blind.html").write_text(html, encoding="utf-8")
    print(f"已生成 {OUT / 'blind.html'}（{len(ids)} 张）")


def main(argv: list[str]) -> None:
    if argv[:1] == ["make"]:
        make()
    elif argv[:1] == ["score"]:
        key = json.loads((OUT / "blind_key.json").read_text(encoding="utf-8"))
        answers = json.loads((OUT / "blind_answers.json").read_text(encoding="utf-8"))
        good, total = score(key, answers)
        print(f"{total} 张里 {good} 张不比有道差（标准：20 张里至少 12 张）")
    else:
        print("用法：python -m tools.blind make | score")


if __name__ == "__main__":
    main(sys.argv[1:])

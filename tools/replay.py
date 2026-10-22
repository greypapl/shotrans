"""离线复现：python -m tools.replay <调试快照文件夹>

用快照里保存的原始识别行，按当前代码重新分组（group_lines）、用保存的译文重新排版，结果存成同目录的
replay.png，并按段打印字号、是否截断和框。不需要识别，也不需要翻译服务。

快照里的 segments 只用来对账：旧译文按「原文文字」（空白归一后）对到新分段上；文字对不上再看框的重叠
（IoU≥0.5）——但按位置配上的还要原文足够像（编辑相似度≥0.8），否则可能把一段被拆成 60/40 的前一半配上整段的
旧译文；按位置配上的一律警告。仍对不上的新段不贴译文，并打印警告。每段还会报告分组/排版和快照相比是 same 还是 changed。

行内代码胶囊（Task 22）：版本 3 的快照存了胶囊在译文里的位置（pill_spans）；重新分段后按文字和框把胶囊编号对到新段上
（remap_pill_spans，对不上的那处照旧当文字画），再交给 render（render 还会核对文字）；版本 1、2 没存，按 guess_pill_spans 重算——胶囊文字在译文里恰好出现一次才算
（带 note 的结果不猜：腾讯不带占位符重翻时胶囊没受保护）。"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from shotrans.layout import group_lines
from shotrans.models import Box, OcrLine, OcrWord, Pill, SegmentResult
from shotrans.protect import fix_key_combos
from shotrans.render import RenderResult, render

IOU_MATCH = 0.5
TEXT_SIMILAR = 0.8          # 按位置配上时，原文的编辑相似度至少这么多才套用旧译文
SCHEMA_VERSIONS = (1, 2, 3)  # 认得的快照格式版本（shotrans/snapshot.py 写的 schema_version）；
                             # 2 = 识别行带词级框 words，1 没有（照常重放，只是认不出行内代码胶囊）；
                             # 3 = 结果带胶囊位置 pill_spans（2 没有：重放时重算，见 guess_pill_spans）


class SnapshotError(ValueError):
    pass


REQUIRED_KEYS = ("mode", "target", "ocr_lines", "segments", "results", "placements")


def load_snapshot(path) -> dict:
    """读快照并校验格式版本。没有 schema_version 的是加版本号之前导出的（字段与版本 1 相同），
    只要必需的字段都在就照常重放；版本号不认得、或缺字段，给出明确提示。"""
    file = Path(path) / "snapshot.json"
    snap = json.loads(file.read_text(encoding="utf-8"))
    v = snap.get("schema_version")
    if v is not None and v not in SCHEMA_VERSIONS:
        raise SnapshotError(f"快照格式版本 {v!r} 不认得（本工具只认 {', '.join(map(str, SCHEMA_VERSIONS))}）："
                            f"{file}。请用导出这份快照的那一版代码重放，或更新本工具。")
    missing = [k for k in REQUIRED_KEYS if k not in snap]
    if missing:
        raise SnapshotError(f"快照缺少字段 {', '.join(missing)}（格式版本 {v!r}）：{file}。这不是完整的调试快照。")
    return snap


def _glued(text: str, a: int, b: int) -> bool:
    """text[a:b] 两头的 ASCII 字母数字是不是粘着别的 ASCII 字母数字（「id」在 identifier 里）。同 protect._literal_spans。"""
    def an(c: str) -> bool:
        return c.isascii() and c.isalnum()
    return (an(text[a]) and a > 0 and an(text[a - 1])) or (an(text[b - 1]) and b < len(text) and an(text[b]))


def guess_pill_spans(text: str, pills: tuple[Pill, ...]) -> tuple[tuple[int, int, int], ...]:
    """旧快照没存胶囊位置时重算：胶囊文字（按键组合按纠错后的写法）在译文里恰好出现一次才给位置——
    只数两头不粘着别的字母数字的那几处（评审 m4：胶囊「id」不能对到 identifier 中间）。
    出现几次（不知道是哪一处）或没出现的不给——那个胶囊照旧当文字画。"""
    out = []
    for k, p in enumerate(pills):
        needle = fix_key_combos(p.text)
        hits = [m.start() for m in re.finditer(re.escape(needle), text)] if needle else []
        hits = [a for a in hits if not _glued(text, a, a + len(needle))]
        if len(hits) == 1:
            out.append((hits[0], hits[0] + len(needle), k))
    return tuple(out)


def remap_pill_spans(spans, saved_pills: list[dict], pills: tuple[Pill, ...]) -> tuple[tuple[int, int, int], ...]:
    """快照里的胶囊编号是当时那一段的；重放时按当前代码重新分段，新段的胶囊顺序、个数可能不同（评审 m3）。
    按「文字和框都一样」把旧编号换成新编号；新段里找不到同一个胶囊的那处不给位置（照旧当文字画）。"""
    out = []
    for a, b, k in spans:
        if not 0 <= k < len(saved_pills):
            continue
        old = saved_pills[k]
        j = next((j for j, p in enumerate(pills)
                  if p.text == old.get("text") and [p.box.x, p.box.y, p.box.w, p.box.h] == old.get("box")), None)
        if j is not None:
            out.append((a, b, j))
    return tuple(out)


def _norm(text: str) -> str:
    return " ".join(text.split())


def similarity(a: str, b: str) -> float:
    """编辑相似度 = 1 − 编辑距离 / 较长者长度（空白归一后比较）。两个都为空算 1。"""
    a, b = _norm(a), _norm(b)
    if not a and not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1 - prev[-1] / max(len(a), len(b))


def _iou(a: Box, b: Box) -> float:
    w = min(a.right, b.right) - max(a.x, b.x)
    h = min(a.bottom, b.bottom) - max(a.y, b.y)
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


@dataclass
class SegReport:
    idx: int                 # 新分段编号
    saved_idx: int | None    # 对上的快照分段编号；None = 没对上
    how: str                 # "text" / "iou" / "none"
    status: str              # "same" / "changed"
    text: str


@dataclass
class ReplayReport:
    result: RenderResult
    segs: list[SegReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _match(new_segs, saved_segs, warnings: list[str] | None = None,
           why_not: dict[int, str] | None = None) -> dict[int, tuple[int | None, str]]:
    """新段 idx -> (快照段 idx 或 None, "text"/"iou"/"none")。每个快照段最多被用一次。
    按位置配（IoU ≥ IOU_MATCH）还要原文编辑相似度 ≥ TEXT_SIMILAR：配上的写一条警告进 warnings；
    位置对得上、但原文不像而没配的，把原因记进 why_not（附在「无对应译文」那条警告后面）。"""
    warnings = [] if warnings is None else warnings
    why_not = {} if why_not is None else why_not
    saved_box = {s["idx"]: Box(*s["box"]) for s in saved_segs}
    saved_text = {s["idx"]: s["text"] for s in saved_segs}
    used: set[int] = set()
    out: dict[int, tuple[int | None, str]] = {}
    for seg in new_segs:  # 先全部按原文文字配
        cands = [s["idx"] for s in saved_segs if s["idx"] not in used and _norm(s["text"]) == _norm(seg.text)]
        if cands:
            best = max(cands, key=lambda i: _iou(saved_box[i], seg.box))
            used.add(best)
            out[seg.idx] = (best, "text")
    for seg in new_segs:  # 剩下的按框重叠配，原文还要足够像
        if seg.idx in out:
            continue
        cands = [(_iou(saved_box[i], seg.box), i) for i in saved_box if i not in used]
        cands = sorted((c for c in cands if c[0] >= IOU_MATCH), reverse=True)
        sims = {i: similarity(seg.text, saved_text[i]) for _, i in cands}
        ok = [i for _, i in cands if sims[i] >= TEXT_SIMILAR]
        head = f"新段 {seg.idx}「{_norm(seg.text)[:30]}」"
        if ok:
            best = ok[0]
            used.add(best)
            out[seg.idx] = (best, "iou")
            warnings.append(f"按位置匹配，原文有差异：{head}套用快照段 {best} 的译文（原文相似度 {sims[best]:.2f}）")
        else:
            out[seg.idx] = (None, "none")
            if cands:
                i = cands[0][1]
                why_not[seg.idx] = f"（按位置能对上快照段 {i}，但原文相似度 {sims[i]:.2f} < {TEXT_SIMILAR}，不套用它的译文）"
    return out


def _same_grouping(seg, saved_seg) -> bool:
    return (_norm(seg.text) == _norm(saved_seg["text"])
            and [seg.box.x, seg.box.y, seg.box.w, seg.box.h] == saved_seg["box"]
            and [(ln.text, [ln.box.x, ln.box.y, ln.box.w, ln.box.h]) for ln in seg.lines]
            == [(ln["text"], ln["box"]) for ln in saved_seg["lines"]])


def _same_placement(p, saved_p) -> bool:
    if p is None or saved_p is None:
        return p is None and saved_p is None
    if not ([p.box.x, p.box.y, p.box.w, p.box.h], p.px, list(p.lines), p.truncated, list(p.fg), list(p.bg)) == \
            (saved_p["box"], saved_p["px"], list(saved_p["lines"]), saved_p["truncated"], list(saved_p["fg"]), list(saved_p["bg"])):
        return False
    saved_area = saved_p.get("area")   # 旧快照没有 area：不比
    if saved_area is None:
        return True
    return p.area is not None and [p.area.x, p.area.y, p.area.w, p.area.h] == saved_area


def replay_report(path) -> ReplayReport:
    path = Path(path)
    snap = load_snapshot(path)
    lines = [OcrLine(ln["text"], Box(*ln["box"]),
                     tuple(OcrWord(w["text"], Box(*w["box"])) for w in ln.get("words", ())))
             for ln in snap["ocr_lines"]]
    with Image.open(path / "original.png") as im:
        image = im.convert("RGB")
    segments = group_lines(lines, snap["mode"], image)
    saved_results = {r["idx"]: SegmentResult(r["idx"], r["text"], r["service"], r["note"],
                                             tuple(tuple(s) for s in r.get("pill_spans", ())))
                     for r in snap["results"]}
    has_spans = {r["idx"] for r in snap["results"] if "pill_spans" in r}
    saved_by_idx = {s["idx"]: s for s in snap["segments"]}
    saved_placements = {p["idx"]: p for p in snap["placements"]}
    match_warnings: list[str] = []
    why_not: dict[int, str] = {}
    matches = _match(segments, snap["segments"], match_warnings, why_not)

    translations: dict[int, str] = {}
    spans: dict[int, tuple] = {}
    marked: set[int] = set()
    for seg in segments:
        sid, _ = matches[seg.idx]
        r = saved_results.get(sid) if sid is not None else None
        if r is not None:
            if r.is_translation:
                translations[seg.idx] = r.text
                if sid in has_spans:
                    spans[seg.idx] = remap_pill_spans(r.pill_spans, saved_by_idx[sid].get("pills", []), seg.pills)
                elif r.note is None:   # 腾讯不带占位符重翻的（NOTE_UNPROTECTED）胶囊没保护，不猜
                    spans[seg.idx] = guess_pill_spans(r.text, seg.pills)
            if r.note:
                marked.add(seg.idx)
    result = render(image, segments, translations, snap["target"], marked=frozenset(marked), pill_spans=spans)

    report = ReplayReport(result, warnings=match_warnings)
    for seg in segments:
        sid, how = matches[seg.idx]
        if sid is None:
            status = "changed"
            if not seg.skip:
                report.warnings.append(f"分段与快照不同：新段 {seg.idx}「{_norm(seg.text)[:30]}」无对应译文"
                                       + why_not.get(seg.idx, ""))
        else:
            same = _same_grouping(seg, saved_by_idx[sid]) and \
                _same_placement(result.placements.get(seg.idx), saved_placements.get(sid))
            status = "same" if same else "changed"
        report.segs.append(SegReport(seg.idx, sid, how, status, seg.text))
    return report


def replay(path) -> RenderResult:
    return replay_report(path).result


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("用法：python -m tools.replay <调试快照文件夹>")
        return 2
    path = Path(argv[0])
    try:
        report = replay_report(path)
    except SnapshotError as e:
        print(e)
        return 2
    report.result.image.save(path / "replay.png")
    for s in report.segs:
        p = report.result.placements.get(s.idx)
        if p is None:
            head = f"段{s.idx}: 无译文排版"
        else:
            head = f"段{s.idx}: 字号={p.px} 截断={'是' if p.truncated else '否'} 框={p.box.x},{p.box.y},{p.box.w},{p.box.h}"
        print(f"{head} 与快照：{s.status}")
    for w in report.warnings:
        print(w)
    n_same = sum(s.status == "same" for s in report.segs)
    print(f"共 {len(report.segs)} 段，与快照相同 {n_same} 段，警告 {len(report.warnings)} 条")
    print(f"已写入 {path / 'replay.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

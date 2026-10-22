"""跑对比测试图。
--stage ocr ：只测识别 + 覆盖（中途关卡）。把识别出的原文原样重画回去，看抹得干不干净。
--stage full：识别 + 真实翻译（走和托盘程序同一套服务链）+ 重绘，记录首段/全部耗时和用到的服务。
结果写到 bench/out/，汇总在 bench/out/report.md。"""
from __future__ import annotations

import argparse
import statistics
import time
import tomllib
from pathlib import Path

from PIL import Image

from shotrans.layout import detect_direction, group_lines
from shotrans.ocr import recognize
from shotrans.render import contrast_ratio, render
from tools.metrics import bag_cer, bag_errors, cer

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "bench"
OUT = BENCH / "out"


def load_cases() -> list[dict]:
    return tomllib.loads((BENCH / "cases.toml").read_text(encoding="utf-8"))["case"]


def analyse(case_id: str, img: Image.Image) -> dict:
    t0 = time.perf_counter()
    lines = recognize(img)
    ocr_ms = (time.perf_counter() - t0) * 1000
    segs = group_lines(lines, "para", img)
    text = "\n".join(s.text for s in segs)
    truth = BENCH / "truth" / f"{case_id}.txt"
    row = {"id": case_id, "segs": segs, "text": text, "ocr_ms": ocr_ms,
           "direction": detect_direction(text), "cer": None, "bag_cer": None,
           "bag_missing": None, "bag_extra": None}
    if truth.exists():
        ref = truth.read_text(encoding="utf-8")
        row["cer"], row["bag_cer"] = cer(ref, text), bag_cer(ref, text)
        row["bag_missing"], row["bag_extra"] = bag_errors(ref, text)
    return row


def placement_stats(placements) -> tuple[float | None, int]:
    ratios = [contrast_ratio(p.fg, p.bg) for p in placements.values()]
    return (min(ratios) if ratios else None), sum(p.truncated for p in placements.values())


def stage_ocr(case_id: str, img: Image.Image) -> dict:
    row = analyse(case_id, img)
    target = "en" if row["direction"] == "en2zh" else "zh"  # 原文语言：原样重画
    tr = {s.idx: s.text for s in row["segs"] if not s.skip}
    r = render(img, row["segs"], tr, target, keep_identical=False)  # 原样重画：每段都要抹了再画
    r.image.save(OUT / f"{case_id}_ocr.png")
    row["min_contrast"], row["truncated"] = placement_stats(r.placements)
    return row


def _fmt(v, spec):
    return "-" if v is None else format(v, spec)


def write_report(rows: list[dict], path: Path) -> None:
    lines = ["# 对比测试报告", "",
             "- 错字率（顺序无关）= max(缺, 多) / 真值字符数：按字符袋计、不计空白。**关卡用它。**",
             "- 错字率（按顺序）= 编辑距离 / 真值长度：双栏、表格的真值顺序和阅读顺序不同时会虚高，仅作参考。", "",
             "| 用例 | 方向 | 错字率（顺序无关） | 缺/多 | 错字率（按顺序，参考） | 识别ms | 首段ms | 全部ms "
             "| 最低对比度 | 截断段数 | 服务 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("missing"):
            lines.append(f"| {r['id']} | 缺图 | | | | | | | | | |")
            continue
        miss_extra = "-" if r.get("bag_missing") is None else f"{r['bag_missing']}/{r['bag_extra']}"
        lines.append(
            f"| {r['id']} | {r['direction']} | {_fmt(r.get('bag_cer'), '.2%')} | {miss_extra} | "
            f"{_fmt(r['cer'], '.2%')} | {r['ocr_ms']:.0f} | "
            f"{_fmt(r.get('first_ms'), '.0f')} | {_fmt(r.get('total_ms'), '.0f')} | "
            f"{_fmt(r['min_contrast'], '.2f')} | {r['truncated']} | {r.get('service', '-')} |")
    done = [r for r in rows if not r.get("missing")]
    cers = [r["cer"] for r in done if r["cer"] is not None]
    bags = [r["bag_cer"] for r in done if r.get("bag_cer") is not None]
    contrasts = [r["min_contrast"] for r in done if r["min_contrast"] is not None]
    lines += ["", "## 汇总", "",
              f"- 平均错字率（顺序无关，{len(bags)} 张有真值）："
              f"{_fmt(statistics.mean(bags) if bags else None, '.2%')}（关卡标准 ≤ 3%）",
              f"- 平均错字率（按顺序，参考）：{_fmt(statistics.mean(cers) if cers else None, '.2%')}",
              f"- 识别耗时最大值：{max((r['ocr_ms'] for r in done), default=0):.0f} ms",
              f"- 全部译文的最低对比度：{_fmt(min(contrasts) if contrasts else None, '.2f')}（标准 ≥ 4.5）",
              f"- 被截断的段落总数：{sum(r['truncated'] for r in done)}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def config_file() -> Path:
    """和托盘程序读同一份配置：数据目录有 config.toml 就用它，否则用程序目录里的旧 config.local.toml。"""
    from shotrans import paths

    p = paths.config_path()
    return p if p.exists() else ROOT / "config.local.toml"


def glossary_file() -> Path:
    from shotrans import paths

    p = paths.glossary_path()
    return p if p.exists() else ROOT / "术语表.txt"


_ROUTER = None


def _router():
    """和托盘程序同一套接线（shotrans.app.build_router），不另写一份。"""
    global _ROUTER
    if _ROUTER is None:
        from shotrans.app import build_router
        from shotrans.config import load_config
        from shotrans.glossary import load_glossary

        _ROUTER = build_router(load_config(config_file()), load_glossary(glossary_file()))
    return _ROUTER


def stage_full(case_id: str, img: Image.Image) -> dict:
    """识别 + 真实翻译 + 重绘，记录首段/全部耗时和用到的服务。"""
    row = analyse(case_id, img)
    results, first = [], []
    t0 = time.perf_counter()

    def on(res):
        # 首段耗时只算真译文：服务不可用时立刻回的原文（service "none"）不算「出字」
        if not first and res.service != "none":
            first.append(time.perf_counter())
        results.append(res)

    _router().translate(row["segs"], row["direction"], on)
    row["total_ms"] = (time.perf_counter() - t0) * 1000
    row["first_ms"] = (first[0] - t0) * 1000 if first else None
    tr = {r.idx: r.text for r in results if r.is_translation}  # 和浮窗（Session）同一条规则
    target = "zh" if row["direction"] == "en2zh" else "en"
    spans = {x.idx: x.pill_spans for x in results if x.is_translation and x.pill_spans}
    r = render(img, row["segs"], tr, target, marked=frozenset(x.idx for x in results if x.note), pill_spans=spans)
    row["notes"] = {x.idx: x.note for x in results if x.note}
    r.image.save(OUT / f"{case_id}_ours.png")
    row["min_contrast"], row["truncated"] = placement_stats(r.placements)
    row["service"] = ",".join(sorted({x.service for x in results})) or "-"
    return row


STAGES = {"ocr": stage_ocr, "full": stage_full}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=sorted(STAGES), required=True)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in load_cases():
        p = BENCH / "images" / f"{c['id']}.png"
        if not p.exists():
            rows.append({"id": c["id"], "missing": True})
            continue
        rows.append(STAGES[args.stage](c["id"], Image.open(p).convert("RGB")))
        print("完成", c["id"])
    write_report(rows, OUT / "report.md")
    print("报告：", OUT / "report.md")


if __name__ == "__main__":
    main()

"""调试快照：把一次截图翻译的原图、识别结果、译文、排版结果存成一个文件夹，方便离线复现。

分两步，方便放进后台线程：
- build_snapshot()：在界面线程里把 Session 变成普通数据（PIL 图片 + dict），不含任何 Qt 对象；
- write_snapshot()：只收普通数据，写文件。

绝不写入配置值和密钥：快照只取 Session 里的内容，函数签名里就没有 config。"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

import shotrans
from . import paths, render
from .models import Box
from .session import Session

log = logging.getLogger(__name__)

_PKG_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Snapshot:
    original: Image.Image
    rendered: Image.Image
    data: dict
    stamp: str  # 文件夹名：YYYYmmdd-HHMMSS毫秒-短编号


def _box(b: Box) -> list[int]:
    return [b.x, b.y, b.w, b.h]


def _git(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", *args], cwd=_PKG_ROOT, capture_output=True, text=True, timeout=2,
                              stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None


def git_short_sha() -> str | None:
    """短提交号；工作区有未提交改动时末尾加 +dirty（只看 截图翻译/ 目录）。"""
    r = _git("rev-parse", "--short", "HEAD")
    out = r.stdout.strip() if r is not None and r.returncode == 0 else ""
    if not out:
        return None
    st = _git("status", "--porcelain", "--", ".")
    if st is not None and st.returncode == 0 and st.stdout.strip():
        out += "+dirty"
    return out


def build_snapshot(session: Session, now: float | None = None) -> Snapshot:
    now = time.time() if now is None else now
    rr = session._rendered()
    pending = any(not sg.skip and sg.idx not in session.results for sg in session.segments)
    data = {
        "schema_version": 3,
        "app": {"git": None, "python": sys.version.split()[0]},  # git 在写文件的线程里补（可能要等几百毫秒）
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "image_size": [session.image.width, session.image.height],
        "mode": session.mode,
        "direction": session.direction,
        "target": session.target,
        # 版本 2（Task 20）：识别行带上词级框（识别行内代码胶囊要用）；没有词级框的行是空列表
        "ocr_lines": [{"text": ln.text, "box": _box(ln.box),
                       "words": [{"text": w.text, "box": _box(w.box)} for w in ln.words]} for ln in session.lines],
        "segments": [{"idx": s.idx, "text": s.text, "skip": s.skip,
                      "lines": [{"text": ln.text, "box": _box(ln.box)} for ln in s.lines],
                      "box": _box(s.box),
                      "pills": [{"text": p.text, "box": _box(p.box)} for p in s.pills]} for s in session.segments],
        # 版本 3（Task 22）：结果带胶囊在译文里的位置 pill_spans [[起, 止, 第几个胶囊], ...]，排版带贴回的胶囊个数 pills
        "results": [{"idx": r.idx, "text": r.text, "service": r.service, "note": r.note,
                     "pill_spans": [list(s) for s in r.pill_spans]}
                    for _, r in sorted(session.results.items())],
        "placements": [{"idx": p.idx, "box": _box(p.box), "px": p.px, "lines": list(p.lines),
                        "fg": list(p.fg), "bg": list(p.bg), "truncated": p.truncated,
                        "area": None if p.area is None else _box(p.area), "pills": p.pills}
                       for _, p in sorted(rr.placements.items())],
        "service_status": session.service_status(),
        "generation": session.generation,
        "pending": pending,  # 还有要翻的段没出译文：这份快照是翻译中途导出的
        "render_constants": {"px_per_line_h": render.PX_PER_LINE_H, "min_px": render.MIN_PX,
                             "line_spacing": render.LINE_SPACING},
    }
    ms = int(now * 1000) % 1000
    stamp = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}{ms:03d}-{session.id[:6]}"
    return Snapshot(session.image.copy(), rr.image.copy(), data, stamp)


def write_snapshot(snap: Snapshot, root: Path | None = None) -> Path:
    root = paths.debug_dir() if root is None else root
    folder = root / snap.stamp
    folder.mkdir(parents=True, exist_ok=True)
    snap.original.save(folder / "original.png")
    snap.rendered.save(folder / "rendered.png")
    snap.data["app"]["version"] = shotrans.__version__
    snap.data["app"]["git"] = None if getattr(sys, "frozen", False) else git_short_sha()
    (folder / "snapshot.json").write_text(json.dumps(snap.data, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder

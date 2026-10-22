"""收集随包分发的第三方许可证：python -m tools.licenses <目录>。只读本机已装包的元数据，不联网。"""
from __future__ import annotations

import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

RUNTIME_ROOTS = ("PySide6", "Pillow", "numpy", "httpx", "winrt-runtime", "winrt-Windows.Foundation",
                 "winrt-Windows.Foundation.Collections", "winrt-Windows.Globalization",
                 "winrt-Windows.Graphics.Imaging", "winrt-Windows.Media.Ocr", "winrt-Windows.Storage.Streams")
_LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.I)
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_WINRT_NOTE = "pywinrt: MIT — https://github.com/pywinrt/pywinrt"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_dists() -> list[metadata.Distribution]:
    """从 RUNTIME_ROOTS 出发按 requires 求传递闭包（忽略 extra 依赖），按规范化名去重、排序。"""
    seen: dict[str, metadata.Distribution] = {}
    todo = list(RUNTIME_ROOTS)
    while todo:
        name = todo.pop()
        key = _norm(name)
        if key in seen:
            continue
        try:
            d = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = d
        for req in d.requires or []:
            if "extra ==" in req.replace("extra==", "extra =="):
                continue
            m = _REQ_NAME.match(req)
            if m:
                todo.append(m.group(1))
    return sorted(seen.values(), key=lambda d: _norm(d.metadata["Name"]))


def _license_name(d: metadata.Distribution) -> str:
    md = d.metadata
    for field in ("License-Expression", "License"):
        v = (md.get(field) or "").strip()
        if v and len(v) < 80:
            return v
    cls = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    return ", ".join(cls) or "UNKNOWN"


def _license_files(d: metadata.Distribution) -> list[Path]:
    """dist-info 里的 LICENSE*/COPYING*/NOTICE*/AUTHORS*，以及 licenses/ 子目录下的所有文件。"""
    out = []
    for f in d.files or []:
        parts = Path(str(f)).parts
        if not parts or not parts[0].endswith(".dist-info"):
            continue
        if _LICENSE_FILE.match(parts[-1]) or (len(parts) > 2 and parts[1] == "licenses"):
            p = Path(d.locate_file(f))
            if p.is_file():
                out.append(p)
    return out


def collect(dest: Path) -> list[dict]:
    rows = []
    tp = dest / "third_party"
    for d in runtime_dists():
        name = d.metadata["Name"]
        folder = tp / _norm(name)
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        for p in _license_files(d):
            shutil.copy2(p, folder / p.name)
            files.append(p.name)
        if not files:
            urls = [u for u in (d.metadata.get_all("Project-URL") or [])] + [d.metadata.get("Home-page") or ""]
            if _norm(name).startswith("winrt"):
                urls.append(_WINRT_NOTE)  # winrt 包不带许可文件：写明 MIT 与官方仓库
            (folder / "NO_LICENSE_FILE.txt").write_text(
                f"{name} {d.version}\n安装包里没有许可证文件 / No license file in the installed package.\n"
                f"许可 / License: {_license_name(d)}\n" + "\n".join(u for u in urls if u) + "\n",
                encoding="utf-8")
            files.append("NO_LICENSE_FILE.txt")
        rows.append({"name": name, "version": d.version, "license": _license_name(d), "files": files})
    # 官方安装包是 LICENSE.txt，conda 环境是 LICENSE_PYTHON.txt
    for cand in ("LICENSE.txt", "LICENSE_PYTHON.txt"):
        py = Path(sys.base_prefix) / cand
        if py.exists():
            (tp / "python").mkdir(parents=True, exist_ok=True)
            shutil.copy2(py, tp / "python" / "LICENSE.txt")
            break
    try:
        pyi = metadata.distribution("pyinstaller")
        for p in _license_files(pyi):
            (tp / "pyinstaller").mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, tp / "pyinstaller" / p.name)
    except metadata.PackageNotFoundError:
        pass
    lines = [f"{r['name']} {r['version']}  —  {r['license']}" for r in rows]
    lines.append(f"Python {sys.version.split()[0]}  —  PSF-2.0")
    (dest / "组件清单.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dest = Path(argv[0])
    rows = collect(dest)
    print(f"已收集 {len(rows)} 个组件的许可证到 {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

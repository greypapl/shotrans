"""生成对比测试图。用法：python -m tools.make_bench [用例id ...]
PDF 用例：按 clip 截图，同时导出原文作为识别真值。
web 用例：用 Edge 无头模式截网页（可选深色）。
qt 用例：用 Qt 画几个典型的软件界面/报错弹窗。"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "bench"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def pdf_path(case: dict, bench: Path | None = None) -> Path:
    """用例里的 pdf：相对路径按 bench 目录解析（如 pdfs/N19-1423.pdf），绝对路径原样用。"""
    p = Path(case["pdf"])
    return p if p.is_absolute() else (BENCH if bench is None else bench) / p


def make_pdf(case: dict, png: Path, truth: Path) -> None:
    path = pdf_path(case)
    if not path.is_file():
        raise FileNotFoundError(f"用例 {case.get('id', '?')} 要的 PDF 不存在：{path}\n"
                                "PDF 不进 git，请按 bench/cases.toml 开头的说明自己准备好放进 bench/pdfs/。")
    import pymupdf as fitz

    doc = fitz.open(str(path))
    page = doc[case["page"] - 1]
    rect = fitz.Rect(*case["clip"])
    zoom = case.get("zoom", 4 / 3)  # 4/3 ≈ 96 dpi，相当于 100% 缩放看 PDF
    page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect).save(str(png))
    truth.write_text(page.get_text("text", clip=rect), encoding="utf-8")


def make_web(case: dict, png: Path) -> None:
    w, h = case.get("size", [1280, 800])
    args = [str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars",
            f"--window-size={w},{h}", f"--screenshot={png}"]
    if case.get("dark"):
        args += ["--force-dark-mode", "--enable-features=WebContentsForceDark"]
    args.append(case["url"])
    subprocess.run(args, check=True, timeout=90)


def _qt_widget(name: str):
    from PySide6.QtWidgets import (
        QCheckBox, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox, QWidget,
    )

    if name == "dialog_en":
        return QMessageBox(QMessageBox.Icon.Critical, "Error",
                           "The file could not be opened because it is being used by another "
                           "process. Close the other program and try again.",
                           QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    if name == "dialog_zh":
        return QMessageBox(QMessageBox.Icon.Warning, "保存失败",
                           "无法保存文件，因为磁盘空间不足。请清理磁盘后重试。",
                           QMessageBox.StandardButton.Ok)
    if name == "settings_en":
        w = QWidget()
        w.setWindowTitle("Settings")
        form = QFormLayout(w)
        form.addRow(QCheckBox("Enable automatic updates"))
        form.addRow(QCheckBox("Send anonymous usage statistics"))
        form.addRow("Proxy server", QLineEdit("proxy.example.com"))
        form.addRow("Port", QLineEdit("8080"))
        form.addRow(QDialogButtonBox(QDialogButtonBox.StandardButton.Apply
                                     | QDialogButtonBox.StandardButton.Cancel))
        return w
    raise ValueError(f"未知的 qt 用例：{name}")


def make_qt(case: dict, png: Path) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    w = _qt_widget(case["widget"])
    w.resize(520, 220)
    w.show()
    app.processEvents()
    w.grab().save(str(png))
    w.close()


def main(argv: list[str]) -> None:
    cases = tomllib.loads((BENCH / "cases.toml").read_text(encoding="utf-8"))["case"]
    (BENCH / "images").mkdir(parents=True, exist_ok=True)
    (BENCH / "truth").mkdir(parents=True, exist_ok=True)
    for c in cases:
        if argv and c["id"] not in argv:
            continue
        png = BENCH / "images" / f"{c['id']}.png"
        if c["kind"] == "pdf":
            make_pdf(c, png, BENCH / "truth" / f"{c['id']}.txt")
        elif c["kind"] == "web":
            make_web(c, png)
        elif c["kind"] == "qt":
            make_qt(c, png)
        print("已生成", png.name)


if __name__ == "__main__":
    main(sys.argv[1:])

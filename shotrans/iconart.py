"""程序图标：蓝底圆角方块上一个白色「译」字。托盘图标和 exe 图标（assets/icon.ico）用同一份绘制代码。
需要 QGuiApplication 已存在（QPixmap 的要求）。"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ICON_PATH = Path(__file__).resolve().parent / "assets" / "icon.ico"
BLUE = "#2563eb"


def icon_pixmap(size: int) -> QPixmap:
    """按 64px 的原设计等比缩放：圆角 14/64、字号 30/64。"""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(BLUE))
    p.setPen(Qt.PenStyle.NoPen)
    r = size * 14 / 64
    p.drawRoundedRect(0, 0, size, size, r, r)
    p.setPen(QColor("white"))
    font = QFont("Microsoft YaHei")
    font.setPixelSize(max(1, round(size * 40 / 64)))   # 30pt@96dpi ≈ 40px：与原 64px 图标观感一致
    font.setWeight(QFont.Weight.Bold)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "译")
    p.end()
    return pix


def make_icon() -> QIcon:
    icon = QIcon()
    for s in ICO_SIZES:
        icon.addPixmap(icon_pixmap(s))
    return icon


def _to_pil(pix: QPixmap) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pix.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def write_ico(path: Path) -> None:
    frames = [_to_pil(icon_pixmap(s)) for s in sorted(ICO_SIZES, reverse=True)]
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, format="ICO", sizes=[f.size for f in frames], append_images=frames[1:])

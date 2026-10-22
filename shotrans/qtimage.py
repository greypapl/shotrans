"""PIL 图片和 QImage 互转。"""
from __future__ import annotations

from PIL import Image
from PySide6.QtGui import QImage


def pil_to_qimage(img: Image.Image) -> QImage:
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    q = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    return q.copy()  # 复制一份，脱离 data 的生命周期


def qimage_to_pil(q: QImage) -> Image.Image:
    q = q.convertToFormat(QImage.Format.Format_RGBA8888)
    data = bytes(q.constBits())
    return Image.frombuffer(
        "RGBA", (q.width(), q.height()), data, "raw", "RGBA", q.bytesPerLine(), 1
    ).convert("RGB")

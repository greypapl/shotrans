"""逐张 1:1 显示测试图，配合有道截图翻译做盲评对照。

用法：python -m tools.show_bench
  1. 用有道 Ctrl+Alt+D 框选整张图，翻译结果会盖在本窗口的图上；
  2. 按 F9（全局热键，不用点窗口）：把屏幕上图所在区域存成 bench/youdao/NN.png 并跳到下一张；
  3. ←/→ 手动翻页，Esc 退出。启动时自动跳到第一张还没存的图，可随时停、下次接着做。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel

ROOT = Path(__file__).resolve().parents[1] / "bench"
IMAGES = ROOT / "images"
YOUDAO = ROOT / "youdao"
HOTKEY = "F9"
HINT = "用有道 Ctrl+Alt+D 框选整张图，翻译出来后按 F9 保存并下一张"


def first_unsaved(paths: list[Path], out_dir: Path) -> int:
    """第一张还没有 out_dir/NN.png 的图的下标；全都存过了就停在最后一张。"""
    for i, p in enumerate(paths):
        if not (out_dir / p.name).exists():
            return i
    return max(len(paths) - 1, 0)


class Viewer(QLabel):
    def __init__(self, paths: list[Path], out_dir: Path = YOUDAO,
                 grab: Callable[[int, int, int, int], QPixmap] | None = None):
        super().__init__()
        self.paths, self.out_dir = paths, out_dir
        self._grab = grab or self._grab_screen
        self.i = first_unsaved(paths, out_dir)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet("background:#808080")
        self._show()

    def _title(self, prefix: str = "") -> str:
        p = self.paths[self.i]
        return f"{prefix}{p.stem}（{self.i + 1}/{len(self.paths)}）— {HINT}"

    def _show(self, prefix: str = "") -> None:
        pix = QPixmap(str(self.paths[self.i]))
        pix.setDevicePixelRatio(self.devicePixelRatioF())  # 一个图像像素 = 一个设备像素，不缩放
        self.setPixmap(pix)
        self.setWindowTitle(self._title(prefix))

    def _grab_screen(self, x: int, y: int, w: int, h: int) -> QPixmap:
        screen = self.screen()
        origin = screen.geometry().topLeft()
        return screen.grabWindow(0, x - origin.x(), y - origin.y(), w, h)

    def capture(self) -> bool:
        """把图当前所在的屏幕区域存成 out_dir/NN.png，然后跳下一张。"""
        src = self.pixmap()
        dpr = src.devicePixelRatio() or 1.0
        pw, ph = src.width(), src.height()  # pixmap.width() 是物理像素
        top_left = self.mapToGlobal(QPoint(0, 0))
        got = self._grab(top_left.x(), top_left.y(), math.ceil(pw / dpr), math.ceil(ph / dpr))
        img = got.toImage()
        if img.width() < pw or img.height() < ph:
            self.setWindowTitle(f"截图失败（区域 {img.width()}x{img.height()} 小于原图 {pw}x{ph}，图是否被挤出屏幕？）")
            return False
        img = img.copy(0, 0, pw, ph)  # 向上取整多出来的边裁掉，尺寸与原图严格一致
        name = self.paths[self.i].name
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if not img.save(str(self.out_dir / name)):
            self.setWindowTitle(f"保存失败：{self.out_dir / name}")
            return False
        done = self.paths[self.i].stem
        if self.i < len(self.paths) - 1:
            self.i += 1
            self._show(f"已保存 {done} ✓  →  ")
        else:
            self.setWindowTitle(f"已保存 {done} ✓  全部 {len(self.paths)} 张做完了，可以按 Esc 退出")
        return True

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Right and self.i < len(self.paths) - 1:
            self.i += 1
            self._show()
        elif e.key() == Qt.Key.Key_Left and self.i > 0:
            self.i -= 1
            self._show()
        elif e.key() == Qt.Key.Key_Escape:
            self.close()


def main() -> None:
    paths = sorted(IMAGES.glob("*.png"))
    if not paths:
        print(f"{IMAGES} 里没有测试图")
        return
    from shotrans.hotkey import GlobalHotkey

    app = QApplication(sys.argv)
    v = Viewer(paths)
    hk = GlobalHotkey(HOTKEY, v.capture, hotkey_id=0xB0E9)
    app.installNativeEventFilter(hk)
    if hk.register():
        print(f"已注册全局热键 {HOTKEY}")
    else:
        print(f"全局热键 {HOTKEY} 注册失败（被别的程序占用？）")
    v.showMaximized()
    try:
        app.exec()
    finally:
        hk.unregister()


if __name__ == "__main__":
    main()

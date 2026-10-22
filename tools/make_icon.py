"""重新生成 shotrans/assets/icon.ico（改了 shotrans/iconart.py 的绘制代码后运行）：python -m tools.make_icon"""
from PySide6.QtWidgets import QApplication

from shotrans import iconart


def main() -> int:
    app = QApplication.instance() or QApplication([])
    iconart.write_ico(iconart.ICON_PATH)
    print(f"已写入 {iconart.ICON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
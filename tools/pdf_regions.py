"""列出 PDF 某页每个文字块的坐标，用来挑测试图的 clip。
用法：python -m tools.pdf_regions <pdf路径> <页码(从1开始)>"""
import sys

import pymupdf as fitz


def main() -> None:
    doc = fitz.open(sys.argv[1])
    page = doc[int(sys.argv[2]) - 1]
    print(f"页面大小 {page.rect}")
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, text = b[:5]
        print(f"[{x0:.0f}, {y0:.0f}, {x1:.0f}, {y1:.0f}]  {text[:70]!r}")


if __name__ == "__main__":
    main()

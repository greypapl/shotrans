"""标定 shotrans/render.py 的 SPAN_* 常数（按字形类别估字号）：python -m tools.calibrate_em

做法：用本机字体按已知字号画一行字，交给本机 Windows OCR（shotrans.ocr.recognize）识别，量「字号 / OCR 框高」。
OCR 的框贴着墨迹，所以这个比值取决于这一行有哪些字形类别（只有 x 高度 / 有上伸 / 有下伸 / 都有 / 方块字）。
每类取所有字体、字号的中位数，倒数就是该类的墨迹跨度（单位：字号），和 render.line_span 的模型值并排打印。

2026-10-12 的结果（segoeui / arial / msyh，11~27 号奇数，每类 4 句）：
  只有 x 高度 1.667 → 跨度 0.60；只有上伸 1.227 → 0.82；只有下伸 1.20 → 0.83；上伸+下伸 1.0 → 1.05；汉字 0.95 → 1.05；
  边界字符：x 高度+逗号 1.389 → 0.72；上伸+逗号 1.056 → 0.95。各类模型值与实测相差都在 3% 以内。
日文假名、韩文没有单独标定：按方块字处理（与汉字同为等高方块字形）。
运行一次约 1~2 分钟（每句一次 OCR）。"""
from __future__ import annotations

import statistics
from collections import defaultdict

from PIL import Image, ImageDraw, ImageFont

from shotrans.ocr import recognize
from shotrans.render import line_span

FONTS = {
    "segoe": r"C:\Windows\Fonts\segoeui.ttf",
    "arial": r"C:\Windows\Fonts\arial.ttf",
    "msyh": r"C:\Windows\Fonts\msyh.ttc",
}
SIZES = range(11, 29, 2)
SAMPLES = {
    "上伸+下伸": ["Language model training", "Bibliographic Tools", "Connected Papers", "Deploy the package"],
    "只有上伸": ["Demos", "About arXivLabs", "Access Code Note", "the remote sensor"],
    "只有下伸": ["among your groups", "pray every summer", "map query engine", "gory ropy guess"],
    "只有 x 高度": ["was some name", "new course arc", "even more acmes", "union nervous ones"],
    "x 高度+逗号": ["new course, arc", "new course; arc"],
    "上伸+逗号": ["Code, Data, Media", "Demos, more"],
    "汉字": ["抑郁症的早期识别", "怎么导出", "还有两点", "已重启好现在运行的是新版本"],
}


def measure(path: str, text: str, px: int) -> float | None:
    """画一行字交给 OCR，返回 字号 / 框高；没认成恰好一行就返回 None。"""
    font = ImageFont.truetype(path, px)
    img = Image.new("RGB", (int(font.getlength(text)) + 80, px * 3 + 40), (255, 255, 255))
    ImageDraw.Draw(img).text((30, 20), text, font=font, fill=(0, 0, 0))
    lines = recognize(img)
    return px / lines[0].box.h if len(lines) == 1 else None


def main() -> None:
    ratios: dict[str, list[float]] = defaultdict(list)
    for cls, texts in SAMPLES.items():
        for fname, path in FONTS.items():
            if cls == "汉字" and fname != "msyh":
                continue
            for px in SIZES:
                for t in texts:
                    r = measure(path, t, px)
                    if r is not None:
                        ratios[cls].append(r)
    print(f"{'类别':10} {'例数':>4} {'字号/框高 中位数':>14} {'→ 跨度':>8} {'模型跨度':>8} {'相差':>7}")
    for cls, texts in SAMPLES.items():
        v = ratios[cls]
        if not v:
            print(f"{cls:10} 没有可用样本")
            continue
        med = statistics.median(v)
        model = line_span(texts[0])
        print(f"{cls:10} {len(v):>4} {med:>14.3f} {1 / med:>8.3f} {model:>8.3f} {(model * med - 1):>+7.1%}")


if __name__ == "__main__":
    main()

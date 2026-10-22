"""全项目共享的数据类型。坐标单位都是截图像素。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def union(self, other: "Box") -> "Box":
        x0, y0 = min(self.x, other.x), min(self.y, other.y)
        x1, y1 = max(self.right, other.right), max(self.bottom, other.bottom)
        return Box(x0, y0, x1 - x0, y1 - y0)

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom


@dataclass(frozen=True)
class OcrWord:
    """OCR 的一个词（中文常是一两个字）和它的框。"""

    text: str
    box: Box


@dataclass(frozen=True)
class OcrLine:
    """words：这一行的词级框（Task 20，识别行内代码胶囊用）。可以为空：旧快照、测试里手写的行没有词级框。
    不参与相等比较：有没有词级框不改变「这是同一行」。"""

    text: str
    box: Box
    words: tuple[OcrWord, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class Pill:
    """行内代码「胶囊」（Task 20）：带底色的圆角框里的连续几个词。text 是这几个词拼起来的原文
    （是段落 text 的子串），box 是胶囊底色的外框（可能比 OCR 框大：OCR 没认出的符号也在里面）。
    start：text 在段落原文里的起始下标（分段时填；只换这一处，不碰别处同样的字）。不知道时为 None，
    不参与相等比较。"""

    text: str
    box: Box
    start: int | None = field(default=None, compare=False)


@dataclass
class Segment:
    """一个待翻译单位：按段模式下是一段，按行模式下是一行。idx 从 1 开始。"""

    idx: int
    text: str
    lines: list[OcrLine]
    skip: bool = False  # 公式行、logo 碎片、作者行：不翻，保持原样
    pills: tuple[Pill, ...] = ()  # 行内代码胶囊：内容原样保留（翻译前换成占位符），绘制时连底色一起抹掉、贴回译文里它的位置

    @property
    def box(self) -> Box:
        b = self.lines[0].box
        for ln in self.lines[1:]:
            b = b.union(ln.box)
        return b


# 腾讯丢了占位符时改用不带占位符的原文重翻：text 是译文，但引用、术语表「保持原样」的词可能被翻了
NOTE_UNPROTECTED = "引用/术语可能未保持原样"


@dataclass(frozen=True)
class SegmentResult:
    """一段的翻译结果。service 是 "vllm" / "fallback" / "tencent" / "none"。
    note 不为空时要在段落右上角画角标、悬停附上原因：
    - 一般的 note（占位符丢失、服务不可用等）：text 是原文，不画译文；
    - NOTE_UNPROTECTED：text 是译文，照常画，只是提醒引用/术语可能没保持原样。
    pill_spans（Task 22）：行内代码胶囊在 text 里的位置 (起, 止, 是段落 pills 里的第几个)，text[起:止] 就是
    那个胶囊的文字（复制、历史、悬停照常用 text，胶囊文字照常在里面）。绘制时这一处贴回原图上那块胶囊；
    为空（缓存是旧的、腾讯不带占位符重翻、拿不准位置）就照旧把胶囊文字当普通文字画。"""

    idx: int
    text: str
    service: str
    note: str | None = None
    pill_spans: tuple[tuple[int, int, int], ...] = ()

    @property
    def is_translation(self) -> bool:
        """text 是不是要画上去的译文。"""
        return self.service != "none" and (self.note is None or self.note == NOTE_UNPROTECTED)

"""一次截图翻译的全部状态。不碰界面，方便测试。

generation：每次重新分组 / 换方向就加一，旧的翻译结果回来时据此丢弃。"""
from __future__ import annotations

import time
import uuid

from PIL import Image

from .i18n import N_, t
from .layout import detect_direction, group_lines
from .models import NOTE_UNPROTECTED, OcrLine, Segment, SegmentResult
from .render import RenderResult, render
from .translate.router import NOTE_DOWN, NOTE_LOST

_NOTE_KEYS = {NOTE_LOST: N_("note.lost"), NOTE_DOWN: N_("note.down"), NOTE_UNPROTECTED: N_("note.unprotected")}


def note_text(note: str) -> str:
    """段落附注的显示文字：已知的附注按当前语言显示；不认识的原样显示。"""
    key = _NOTE_KEYS.get(note)
    return t(key) if key else note


class Session:
    def __init__(self, image: Image.Image):
        self.id = uuid.uuid4().hex  # 历史记录据此把同一张截图的多次翻译合成一条
        self.image = image.convert("RGB")
        self.lines: list[OcrLine] = []
        self.mode = "para"
        self.direction = "en2zh"
        self.direction_forced = False
        self.segments: list[Segment] = []
        self.results: dict[int, SegmentResult] = {}
        self._undo: dict[int, list[SegmentResult]] = {}
        self.show_original = False
        self.generation = 0
        self._rendered_cache: RenderResult | None = None

    @property
    def target(self) -> str:
        return "zh" if self.direction == "en2zh" else "en"

    # ---------- 分组与方向 ----------
    def set_lines(self, lines: list[OcrLine]) -> None:
        self.lines = list(lines)
        self._regroup()

    def _regroup(self) -> None:
        self.segments = group_lines(self.lines, self.mode, self.image)
        if not self.direction_forced:
            self.direction = detect_direction(" ".join(s.text for s in self.segments))
        self._reset_results()

    def _reset_results(self) -> None:
        self.results.clear()
        self._undo.clear()
        self.generation += 1
        self._rendered_cache = None

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self._regroup()

    def flip_direction(self) -> None:
        self.direction = "zh2en" if self.direction == "en2zh" else "en2zh"
        self.direction_forced = True
        self._reset_results()

    # ---------- 结果 ----------
    def apply(self, generation: int, result: SegmentResult) -> bool:
        if generation != self.generation:
            return False
        self.results[result.idx] = result
        self._rendered_cache = None
        return True

    def apply_retranslation(self, result: SegmentResult) -> None:
        old = self.results.get(result.idx)
        if old is not None:
            self._undo.setdefault(result.idx, []).append(old)
        self.results[result.idx] = result
        self._rendered_cache = None

    def can_undo(self, idx: int) -> bool:
        return bool(self._undo.get(idx))

    def undo_retranslation(self, idx: int) -> bool:
        stack = self._undo.get(idx)
        if not stack:
            return False
        self.results[idx] = stack.pop()
        self._rendered_cache = None
        return True

    # ---------- 显示 ----------
    def _rendered(self) -> RenderResult:
        if self._rendered_cache is None:
            tr = {i: r.text for i, r in self.results.items() if r.is_translation}
            marked = frozenset(i for i, r in self.results.items() if r.note)
            spans = {i: r.pill_spans for i, r in self.results.items() if r.is_translation and r.pill_spans}
            self._rendered_cache = render(self.image, self.segments, tr, self.target, marked=marked, pill_spans=spans)
        return self._rendered_cache

    def rendered_image(self) -> Image.Image:
        """译文盖在原图上的那张图（不管当前看的是原文还是译文）。"""
        return self._rendered().image

    def display_image(self) -> Image.Image:
        return self.image if self.show_original else self._rendered().image

    def toggle_view(self) -> None:
        self.show_original = not self.show_original

    def segment_at(self, x: float, y: float) -> Segment | None:
        for s in self.segments:
            if s.box.contains(x, y):
                return s
        return None

    def hover_text(self, seg: Segment) -> str:
        """译文视图下给原文（译文被截断时连同译文全文）；原文视图下给译文。
        结果带 note（占位符丢失、服务不可用、引用/术语可能未保持原样）时，末尾附上原因。"""
        r = self.results.get(seg.idx)
        note = "\n" + t("note.wrap", note=note_text(r.note)) if r is not None and r.note else ""
        if self.show_original:
            return r.text + note if r else ""
        p = self._rendered().placements.get(seg.idx)
        if p is not None and p.truncated and r is not None:
            return f"{r.text}\n——\n{seg.text}{note}"
        return seg.text + note

    def translation_of(self, seg: Segment) -> str:
        r = self.results.get(seg.idx)
        return r.text if r else seg.text

    def all_translation(self) -> str:
        return "\n\n".join(self.translation_of(s) for s in self.segments)

    def all_original(self) -> str:
        return "\n\n".join(s.text for s in self.segments)

    def service_status(self) -> str:
        services = {r.service for r in self.results.values()}
        # 取「最差」的那层：none > tencent > fallback > vllm
        if "none" in services:
            return "none"
        if "tencent" in services:
            return "tencent"
        if "fallback" in services:
            return "fallback"
        if "vllm" in services:
            return "vllm"
        if not self.segments:
            return "empty"  # 一行字都没识别出来
        return "pending" if any(not s.skip for s in self.segments) else "skipped"  # 只有公式/数字行

    def history_entry(self) -> dict:
        return {
            "session": self.id,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "direction": self.direction,
            "service": self.service_status(),
            "segments": [{"src": s.text, "dst": self.results[s.idx].text if s.idx in self.results else None}
                         for s in self.segments],
        }

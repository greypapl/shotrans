"""翻译路由：vLLM → 备用（用户自填的 OpenAI 兼容接口）→ 腾讯交互翻译，三层都可不配置（传 None 就跳过）；
vLLM 失败后 60 秒内跳过 vLLM（熔断只针对 vLLM）。

- 翻到一半断了：已完成的段落保留，剩下的交给下一个服务。
- vLLM 没按编号格式输出：剩下的段落改成一段一个请求。
- 占位符丢了：同一服务单独重翻这一段；还丢就显示原文。
  腾讯例外：还丢就再用不带占位符的原文翻一次，照常显示译文，注明「引用/术语可能未保持原样」，不进缓存。
- 都失败（或没有可用服务）：显示原文，注明「翻译服务不可用」。

大模型层（vLLM、备用）分块并发：段落按阅读顺序切块（每块约 chunk_chars 字、至多 chunk_max_segments 段），
同一服务最多 max_concurrency 块同时在途（第一块先走一步，见 head_start）。各块在工作线程里跑，结果经队列交回调用 translate 的线程，
on_result 始终只在这一个线程里串行调用。某块整体失败：不再发新块，等在途的块都结束后，所有没译完的段统一交给下一层。
腾讯一次批量返回，不分块。"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from ..glossary import Glossary, relevant
from ..models import NOTE_UNPROTECTED, Segment, SegmentResult
from ..protect import MissingPlaceholder, Protected, protect, restore_spans
from .base import Job, TranslateError

log = logging.getLogger(__name__)

CACHE_MAX = 2000  # 译文缓存上限（条）。每条存 (译文, 胶囊位置)：缓存命中时胶囊照样贴回原图

NOTE_LOST = "占位符丢失，显示原文"
NOTE_DOWN = "翻译服务不可用"
__all__ = ["Router", "NOTE_LOST", "NOTE_DOWN", "NOTE_UNPROTECTED"]

# 只有这些错误说明 vLLM 连不上/不稳，值得熔断；format（没按格式输出）不算。
# auth（key 无效或未填）也熔断：key 不会自己变好，每张截图都去撞一次 401 只会拖慢。
CONNECTIVITY_KINDS = frozenset({"connect", "first_token", "stall", "http", "auth"})


@dataclass
class _Item:
    seg: Segment
    prot: Protected
    maps: list[tuple[str, str]]
    keep: tuple[str, ...] = ()
    lits: tuple[tuple[str, int | None], ...] = ()   # 行内代码胶囊的 (文字, 在段落里的起始下标)（原样保留）

    @property
    def sig(self) -> tuple:
        """这一段用到的术语表词条和胶囊；术语表改了、或同样的原文这次有/没有胶囊，缓存键就变，不会读到旧译文。"""
        return (tuple(self.maps), self.keep, self.lits)


def _merge_maps(items) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for it in items:
        for pair in it.maps:
            if pair not in out:
                out.append(pair)
    return out


class Router:
    def __init__(self, vllm, fallback, glossary: Glossary, breaker_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, tencent=None,
                 chunk_chars: int = 600, chunk_max_segments: int = 8, max_concurrency: int = 4,
                 head_start: float = 0.5):
        self.vllm = vllm          # 可为 None：没填 key，不启用 vLLM
        self.fallback = fallback  # 可为 None：不启用第二层
        self.tencent = tencent    # 可为 None：不启用第三层
        self.glossary = glossary
        # 分块并发（只用于大模型层）；max_concurrency = 1 就是在调用者线程里一块接一块地顺序翻
        self.chunk_chars = max(1, chunk_chars)
        self.chunk_max_segments = max(1, chunk_max_segments)
        self.max_concurrency = max(1, max_concurrency)
        self.head_start = max(0.0, head_start)  # 秒：其余块最多等第一块这么久（见 _run_layer）
        self.breaker_seconds = breaker_seconds
        self.clock = clock
        # 熔断状态可能被翻译线程和重翻线程同时访问；只有浮点/字符串赋值，GIL 下各自原子，不加锁。
        self._vllm_failed_at: float | None = None
        self._vllm_last_kind: str | None = None
        # 译文缓存：LRU，最多 cache_max 条（托盘程序常驻，不限量会一直涨）。读写是复合操作，加锁。
        self.cache_max = CACHE_MAX
        self._cache: OrderedDict[tuple, tuple[str, tuple]] = OrderedDict()
        self._cache_lock = threading.Lock()

    def _scrub(self, text: str) -> str:
        """日志里去掉配置的 key（原样、repr 转义、ascii 转义三种写法都去掉）。key 本不该出现在报错里，这里是第二道防线。"""
        for svc in (self.vllm, self.fallback):
            key = getattr(svc, "api_key", "") or ""
            key = key.strip() if isinstance(key, str) else ""
            if len(key) < 6:
                continue
            for form in {key, repr(key)[1:-1], ascii(key)[1:-1]}:
                text = text.replace(form, "***")
        return text

    # ---------- 熔断 ----------
    def vllm_down(self) -> bool:
        return self._vllm_failed_at is not None and self.clock() - self._vllm_failed_at < self.breaker_seconds

    def _mark_vllm_failed(self, kind: str | None = None) -> None:
        self._vllm_failed_at = self.clock()
        self._vllm_last_kind = kind

    def vllm_last_error_kind(self) -> str | None:
        """熔断期间返回让 vLLM 熔断的那次失败的类型（如 "auth"）；不在熔断期返回 None。"""
        return self._vllm_last_kind if self.vllm_down() else None

    # ---------- 缓存 ----------
    def _cache_get(self, key: tuple) -> tuple[str, tuple] | None:
        with self._cache_lock:
            val = self._cache.get(key)
            if val is not None:
                self._cache.move_to_end(key)  # 刚用过的排到最后，最久没用的先被挤掉
            return val

    def _cache_put(self, key: tuple, val: tuple[str, tuple]) -> None:
        with self._cache_lock:
            self._cache[key] = val
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max:
                self._cache.popitem(last=False)

    def _usable(self, svc) -> bool:
        """配置了，且（是 vLLM 时）不在熔断期。"""
        if svc is None:
            return False
        return not (svc is self.vllm and self.vllm_down())

    def _chain(self) -> list:
        return [s for s in (self.vllm, self.fallback, self.tencent) if self._usable(s)]

    # ---------- 主流程 ----------
    def _make_item(self, s: Segment, direction: str) -> _Item:
        maps, keep = relevant(self.glossary, s.text, direction)
        lits = tuple((p.text, p.start) for p in s.pills)   # 带位置：只换胶囊那一处
        return _Item(s, protect(s.text, keep, lits), maps, tuple(keep), lits)

    def translate(self, segments: list[Segment], direction: str,
                  on_result: Callable[[SegmentResult], None],
                  cancel: threading.Event | None = None) -> None:
        cancel = cancel or threading.Event()
        if cancel.is_set():
            return
        chain = self._chain()
        items: dict[int, _Item] = {}
        for s in segments:
            if s.skip:
                continue
            it = self._make_item(s, direction)
            hit = None
            for svc in chain:
                val = self._cache_get((s.text, direction, svc.name, it.sig))
                if val is not None:
                    hit = (svc.name, val)
                    break
            if hit:
                on_result(SegmentResult(s.idx, hit[1][0], hit[0], pill_spans=hit[1][1]))
            else:
                items[s.idx] = it
        for svc in chain:
            if not items or cancel.is_set():
                break
            self._run_layer(svc, items, direction, on_result, cancel)
        if cancel.is_set():
            return
        if items:
            for idx, it in items.items():
                if cancel.is_set():
                    break
                on_result(SegmentResult(idx, it.seg.text, "none", NOTE_DOWN))

    def _finish(self, svc, it: _Item, out: str, direction: str,
                on_result: Callable[[SegmentResult], None]) -> bool:
        try:
            final, spans = restore_spans(out, it.prot.slots, it.prot.pills)
        except MissingPlaceholder:
            return False
        self._cache_put((it.seg.text, direction, svc.name, it.sig), (final, spans))
        on_result(SegmentResult(it.seg.idx, final, svc.name, pill_spans=spans))
        return True

    def _single(self, svc, it: _Item, direction: str, cancel) -> tuple[str | None, str | None]:
        """返回 (译文或 None, 出错类型或 None)。"""
        box: list[str] = []
        try:
            svc.translate_stream([Job(it.seg.idx, it.prot.text)], direction, it.maps,
                                 lambda i, t: box.append(t), cancel)
        except TranslateError as e:
            log.warning("%s 单段失败：%s", svc.name, self._scrub(str(e)))
            return None, e.kind
        return (box[0] if box else None), None

    def _unprotected(self, svc, it: _Item, direction: str, cancel) -> SegmentResult | None:
        """不带占位符（引用、保持原样的词都不保护）直接翻原文；只给腾讯用。失败返回 None。不进缓存。"""
        box: list[str] = []
        try:
            svc.translate_stream([Job(it.seg.idx, it.seg.text)], direction, it.maps,
                                 lambda i, t: box.append(t), cancel)
        except TranslateError as e:
            log.warning("%s 不带占位符重翻失败：%s", svc.name, self._scrub(str(e)))
            return None
        if not box:
            return None
        return SegmentResult(it.seg.idx, box[0], svc.name, NOTE_UNPROTECTED)

    def _attempt(self, svc, items: dict[int, _Item], direction: str,
                 on_result, cancel) -> set[int]:
        """用一个服务翻 items，返回已交付（含按原文兜底）的段落编号。
        服务整体失败时抛 TranslateError，done 是已交付的编号。"""
        done: set[int] = set()
        lost: list[int] = []

        def handle(idx: int, out: str) -> None:
            it = items.get(idx)
            if it is None or idx in done or idx in lost:
                return
            if self._finish(svc, it, out, direction, on_result):
                done.add(idx)
            else:
                lost.append(idx)

        jobs = [Job(i, it.prot.text) for i, it in items.items()]
        try:
            svc.translate_stream(jobs, direction, _merge_maps(items.values()), handle, cancel)
        except TranslateError as e:
            e.done = frozenset(done)
            raise
        for idx in lost:
            if cancel.is_set():  # 取消后不再重试，也不再发「占位符丢失」
                break
            it = items[idx]
            out, _ = self._single(svc, it, direction, cancel)
            if out is not None and self._finish(svc, it, out, direction, on_result):
                done.add(idx)
                continue
            if cancel.is_set():
                break
            if svc is self.tencent:
                res = self._unprotected(svc, it, direction, cancel)
                if cancel.is_set():
                    break
                if res is not None:
                    on_result(res)
                    done.add(idx)
                    continue
            on_result(SegmentResult(idx, it.seg.text, svc.name, NOTE_LOST))
            done.add(idx)
        return done

    def _per_segment(self, items: dict[int, _Item], direction: str, on_result, cancel) -> str | None:
        """vLLM 没按编号格式输出后，一段一个请求。遇到非 format 的失败就停（熔断），返回它的类型；否则返回 None。"""
        for idx in list(items):
            if cancel.is_set():
                return None
            try:
                for i in self._attempt(self.vllm, {idx: items[idx]}, direction, on_result, cancel):
                    items.pop(i, None)
            except TranslateError as e:
                if e.kind != "format":
                    if cancel.is_set():
                        return None
                    self._mark_vllm_failed(e.kind)
                    return e.kind
        return None

    # ---------- 分块并发 ----------
    def _split(self, items: dict[int, _Item]) -> list[dict[int, _Item]]:
        """按阅读顺序切块：累计原文字符数超过 chunk_chars 或段数到 chunk_max_segments 就另起一块；单段超长独占一块。"""
        chunks: list[dict[int, _Item]] = []
        cur: dict[int, _Item] = {}
        size = 0
        for idx, it in items.items():
            n = len(it.seg.text)
            if cur and (size + n > self.chunk_chars or len(cur) >= self.chunk_max_segments):
                chunks.append(cur)
                cur, size = {}, 0
            cur[idx] = it
            size += n
        if cur:
            chunks.append(cur)
        return chunks

    def _run_chunk(self, svc, sub: dict[int, _Item], direction: str, emit, cancel) -> str | None:
        """用 svc 翻一块。返回让这一块（及之后的块）该交给下一层的失败类型；
        None = 正常结束、已取消，或 format 错误（vLLM 已在本块内逐段重试；备用的 format 只影响本块）。"""
        try:
            self._attempt(svc, sub, direction, emit, cancel)
            return None
        except TranslateError as e:
            log.warning("%s 失败：%s", svc.name, self._scrub(str(e)))
            if cancel.is_set():  # 用户取消不算服务的错
                return None
            if e.kind == "format":
                if svc is self.vllm:
                    rest = {i: it for i, it in sub.items() if i not in e.done}
                    return self._per_segment(rest, direction, emit, cancel)
                return None
            if svc is self.vllm:
                self._mark_vllm_failed(e.kind)
            return e.kind

    def _run_layer(self, svc, items: dict[int, _Item], direction: str,
                   on_result: Callable[[SegmentResult], None], cancel: threading.Event) -> None:
        """用一层服务翻 items；交付了的段从 items 里删掉，剩下的留给下一层。"""
        chunks = [dict(items)] if svc is self.tencent else self._split(items)
        n_workers = min(self.max_concurrency, len(chunks))
        if n_workers <= 1:
            # 顺序：就在调用者线程里一块接一块（只有一块时与分块前完全相同）
            def emit(r: SegmentResult) -> None:
                items.pop(r.idx, None)
                on_result(r)

            for ch in chunks:
                if cancel.is_set():
                    return
                if self._run_chunk(svc, ch, direction, emit, cancel) is not None:
                    return  # 这一层失败：不再发新块，剩下的交给下一层
            return

        q: queue.Queue = queue.Queue()
        pending = iter(chunks)
        lock = threading.Lock()
        stop = threading.Event()  # 某块失败 / 调用者这边出错：不再发新块
        # 第一块先走一步：其余工作线程等第一块交出首段（或第一块结束、或至多 head_start 秒）再发请求。
        # 实测同时发 4 块时第一块的首段要和其余块抢算力，比不分块还慢 0.1～0.2 秒；先走一步后首段反而更快，总耗时只多零点几秒。
        head_go = threading.Event()

        def emit(r: SegmentResult) -> None:
            q.put(("res", r))
            head_go.set()

        def worker(k: int) -> None:
            try:
                if k:
                    head_go.wait(self.head_start)
                while not cancel.is_set() and not stop.is_set():
                    with lock:
                        ch = next(pending, None)
                    if ch is None:
                        return
                    failed = self._run_chunk(svc, ch, direction, emit, cancel) is not None
                    head_go.set()
                    if failed:
                        stop.set()
            except BaseException as e:  # 意外错误交回调用者线程抛出，不能让它干等
                stop.set()
                head_go.set()
                q.put(("crash", e))
            finally:
                q.put(("exit", None))

        for k in range(n_workers):
            threading.Thread(target=worker, args=(k,), daemon=True, name=f"shotrans-chunk-{svc.name}-{k}").start()
        alive = n_workers
        try:
            while alive:
                try:
                    tag, val = q.get(timeout=0.05)
                except queue.Empty:
                    if cancel.is_set():
                        return  # 在途请求由各自的 cancel 收尾；不再回调
                    continue
                if cancel.is_set():
                    return
                if tag == "res":
                    items.pop(val.idx, None)
                    on_result(val)
                elif tag == "crash":
                    raise val
                else:
                    alive -= 1
        finally:
            stop.set()

    # ---------- 单段重翻 ----------
    def retranslate_one(self, segment: Segment, direction: str, current_service: str) -> SegmentResult | None:
        """换一个服务重翻：按 vllm → 备用 → 腾讯 的顺序取第一个「不是当前服务、配置了、不在熔断期」的；没有就返回 None。"""
        svc = next((s for s in (self.vllm, self.fallback, self.tencent)
                    if self._usable(s) and s.name != current_service), None)
        if svc is None:
            return None
        it = self._make_item(segment, direction)
        out, kind = self._single(svc, it, direction, None)
        if out is None:
            if svc is self.vllm and kind in CONNECTIVITY_KINDS:
                self._mark_vllm_failed(kind)
            return None
        try:
            final, spans = restore_spans(out, it.prot.slots, it.prot.pills)
        except MissingPlaceholder:
            # 腾讯：改用不带占位符的原文翻（同 translate 的规则）；别的服务照旧放弃
            return self._unprotected(svc, it, direction, None) if svc is self.tencent else None
        self._cache_put((segment.text, direction, svc.name, it.sig), (final, spans))
        return SegmentResult(segment.idx, final, svc.name, pill_spans=spans)

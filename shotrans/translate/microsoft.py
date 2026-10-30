"""微软翻译（Edge 浏览器网页翻译用的接口）：免 key 的机器翻译，第三层（「免费翻译」层）。

这是非公开接口，可能随时失效；失效时一律抛 TranslateError，由路由交给下一层或显示原文。
2026-10-30 实测（旧的 translate/auth 取令牌已 404，改走不要令牌的 translatetext）：
- POST {URL}?from=en&to=zh-Hans&isEnterpriseClient=false，请求体是字符串数组，返回 [{"translations": [{"text": …}]}]，一条对一条；
- 占位符 ⟨n⟩ 会被当成引号、把别的词挪进去，所以发送前换成 {n}（见 to_wire）；⟦n⟧ 原样保留；< & 不转义；换行保留；
- 单次上限 1000 条或 5 万字符（超了 400），所以按 MAX_ITEMS / MAX_CHARS 分批。
- 「指定译法」：词条原文先换成 ⟦k⟧，回来换成译法；某段的 ⟦k⟧ 丢了，这一段不带替换再请求一次（没按译法翻，照常交出）。"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import httpx

from ..protect import term_pattern
from .base import Job, TranslateError

log = logging.getLogger(__name__)

EDGE_URL = "https://edge.microsoft.com/translate/translatetext"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0")
KEEPALIVE = 120.0  # 闲置连接保留秒数（httpx 默认 5 秒，截图间隔通常更长）
READ_PER_CHARS = 2000  # 读超时：每这么多字符多给 1 秒（实测 1.6 万字 0.5 秒，留足余量）
READ_CAP = 30.0        # 读超时上限（秒）
MAX_ITEMS = 1000       # 单次请求最多几段（接口上限 1000）
MAX_CHARS = 45000      # 单次请求最多几个字符（接口上限 5 万，留余量）
# 重试一次的错误：过期连接的表现，加上连接阶段很快就失败的（实测偶尔首次 TLS 握手被断：SSL UNEXPECTED_EOF，
# 马上重来就好）。超时不在其中（ConnectTimeout 不是 ConnectError 的子类），所以连不上时不会白等两遍
STALE_ERRORS = (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ConnectError)
LANGS = {"en2zh": ("en", "zh-Hans"), "zh2en": ("zh-Hans", "en")}

_TERM = re.compile(r"⟦\s*(\d+)\s*⟧")
_SLOT = re.compile(r"⟨(\d+)⟩")
_MARKS = re.compile(r"(⟨\d+⟩|⟦\d+⟧)")  # 切开后奇数下标是占位符 / 记号本身
_BRACE = re.compile(r"\{\s*(\d+)\s*\}")
_ALT = re.compile(r"__\s*P\s*(\d+)\s*__")


@dataclass(frozen=True)
class Wire:
    """一段发出去的文本，以及换回占位符需要的信息。"""
    text: str
    nums: frozenset[str]  # 换过的占位符编号（换回时只认这些）
    alt: bool             # True = 用的是 __Pn__ 形式


def to_wire(text: str) -> Wire:
    """⟨n⟩ → {n}：2026-10-30 实测微软会把 ⟨n⟩ 当成一对引号，把别的词挪进去（「⟨1的评估⟩」），12 句里坏 2 句；
    {n}、__Pn__、⟦n⟧ 都 12/12 原样保留。原文里本来就有 {数字}（如代码里的 {0}）时，这一段改用 __Pn__，
    免得译文回来分不清哪个 {n} 是我们的。（⟦n⟧ 留给「指定译法」的记号。）"""
    nums = frozenset(_SLOT.findall(text))
    if _BRACE.search(text):
        return Wire(_SLOT.sub(lambda m: f"__P{m.group(1)}__", text), nums, True)
    return Wire(_SLOT.sub(lambda m: "{" + m.group(1) + "}", text), nums, False)


def from_wire(text: str, w: Wire) -> str:
    pat = _ALT if w.alt else _BRACE
    return pat.sub(lambda m: f"⟨{m.group(1)}⟩" if m.group(1) in w.nums else m.group(0), text)


def apply_mappings(text: str, mappings: list[tuple[str, str]]) -> tuple[str, dict[str, str]]:
    """把段里命中的词条原文换成 ⟦k⟧（长词条先换，免得短词条抢走长词条里的一截），返回 (新文本, {编号: 译法})。
    匹配规则与术语表相同（term_pattern：全小写不分大小写、带大写按原样）。"""
    slots: dict[str, str] = {}
    for src, dst in sorted(mappings, key=lambda m: -len(m[0])):
        if not src.strip() or not dst.strip():
            continue
        pat = term_pattern(src.strip())
        # 只在占位符 ⟨n⟩ 和已换好的记号 ⟦k⟧ 之外替换：词条 "1"、"P1" 不能把它们里面的数字吃掉
        parts = _MARKS.split(text)
        if not any(pat.search(p) for p in parts[::2]):
            continue
        k = str(len(slots) + 1)
        slots[k] = dst.strip()
        text = "".join(pat.sub(f"⟦{k}⟧", p) if i % 2 == 0 else p for i, p in enumerate(parts))
    return text, slots


_CJK = "　-〿一-鿿＀-￯"  # 汉字、中文标点、全角符号
_GAP_BEFORE = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=⟦\s*\d+\s*⟧)")
_GAP_AFTER = re.compile(rf"(⟦\s*\d+\s*⟧)[ \t]+(?=[{_CJK}])")


def restore_mappings(text: str, slots: dict[str, str], to_chinese: bool = False) -> str | None:
    """⟦k⟧ 换回译法；有编号丢了（或冒出不认识的编号）返回 None。
    译成中文时，微软会在记号两边留空格（「快感缺失 是 重性抑郁障碍 的」）：挨着汉字、中文标点的那边去掉。"""
    found = set(_TERM.findall(text))
    if found != set(slots):
        return None
    if to_chinese:
        text = _GAP_AFTER.sub(r"\1", _GAP_BEFORE.sub("", text))
    return _TERM.sub(lambda m: slots[m.group(1)], text)


def batches(texts: list[str]) -> list[list[int]]:
    """按接口上限把段落编号（在 texts 里的下标）分成几批；单段超长独占一批（接口报错就按失败处理）。"""
    out: list[list[int]] = []
    cur: list[int] = []
    size = 0
    for i, s in enumerate(texts):
        if cur and (len(cur) >= MAX_ITEMS or size + len(s) > MAX_CHARS):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(s)
    if cur:
        out.append(cur)
    return out


class MicrosoftClient:
    name = "microsoft"

    def __init__(self, timeout_connect: float = 6.0, timeout_read: float = 8.0, url: str | None = None):
        self.timeout_connect = timeout_connect
        self.timeout_read = timeout_read
        self.url = url or EDGE_URL
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        # 预热和真请求互斥：预热进行中，真请求等它建好连接再发（复用这条连接）；有真请求在跑，预热直接跳过
        self._cv = threading.Condition()
        self._warming = False
        self._inflight = 0

    def _http(self) -> httpx.Client:
        """连接复用，闲置连接保留 KEEPALIVE 秒。不传自定义 transport：传了 httpx 就不再读环境/注册表里的系统代理。
        服务端先断了的旧连接由 _retry_stale 重试一次。"""
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(
                    timeout=httpx.Timeout(self.timeout_read, connect=self.timeout_connect),
                    limits=httpx.Limits(keepalive_expiry=KEEPALIVE),
                    headers={"User-Agent": USER_AGENT},
                )
            return self._client

    @staticmethod
    def _retry_stale(send: Callable[[], httpx.Response]) -> httpx.Response:
        """复用的旧连接可能已被服务端关掉、或握手偶尔被断：这类错误（没收到响应、读/写出错、连接阶段出错）重试一次；
        超时不重试。"""
        try:
            return send()
        except STALE_ERRORS as e:
            log.debug("微软翻译连接出错，重试一次：%r", e)
            return send()

    def read_timeout_for(self, texts: list[str]) -> float:
        """读超时随这批文字的长度放宽：基础 timeout_read，每 READ_PER_CHARS 个字符加 1 秒，最多 READ_CAP 秒。"""
        return min(READ_CAP, self.timeout_read + sum(len(t) for t in texts) / READ_PER_CHARS)

    def model_name(self) -> str:
        return "微软翻译"

    # ---------- 预热 ----------
    def warm(self) -> None:
        """提前把 TLS 连接建好（向站点根地址发一个 HEAD），首图不用再等握手。
        任何异常都吞掉（只记 debug 日志）；有真请求在跑或已在预热时直接返回。会阻塞，别在界面线程调。"""
        with self._cv:
            if self._warming or self._inflight:
                return
            self._warming = True
        try:
            parts = urlsplit(self.url)
            root = f"{parts.scheme}://{parts.netloc}/"
            self._retry_stale(lambda: self._http().head(root))
        except Exception as e:  # 预热失败无所谓，真请求会自己报错
            log.debug("微软翻译预热失败：%r", e)
        finally:
            with self._cv:
                self._warming = False
                self._cv.notify_all()

    def warm_async(self) -> threading.Thread:
        """在后台 daemon 线程里预热，立刻返回。"""
        t = threading.Thread(target=self.warm, daemon=True, name="shotrans-microsoft-warm")
        t.start()
        return t

    def _begin_request(self) -> None:
        with self._cv:
            # 等预热把连接建好（最长一次连接+读取的时间）；等不到也照发
            self._cv.wait_for(lambda: not self._warming, timeout=self.timeout_connect + self.timeout_read)
            self._inflight += 1

    def _end_request(self) -> None:
        with self._cv:
            self._inflight -= 1
            self._cv.notify_all()

    # ---------- 请求 ----------
    def _request(self, texts: list[str], direction: str) -> list[str]:
        """发一批，返回同样条数的译文。失败抛 TranslateError。"""
        src, dst = LANGS[direction]
        params = {"from": src, "to": dst, "isEnterpriseClient": "false"}
        timeout = httpx.Timeout(self.read_timeout_for(texts), connect=self.timeout_connect)
        self._begin_request()
        try:
            resp = self._retry_stale(lambda: self._http().post(self.url, params=params, json=texts, timeout=timeout))
        except httpx.TransportError as e:  # 连不上、超时、连接被断
            raise TranslateError("connect", repr(e)) from e
        except httpx.HTTPError as e:
            raise TranslateError("http", repr(e)) from e
        finally:
            self._end_request()
        if resp.status_code != 200:
            raise TranslateError("http", f"HTTP {resp.status_code}")
        try:
            obj = resp.json()
        except ValueError as e:
            raise TranslateError("format", "返回的不是 JSON") from e
        try:
            outs = [x["translations"][0]["text"] for x in obj]
        except (TypeError, KeyError, IndexError) as e:
            raise TranslateError("format", "返回的格式不认识") from e
        if not all(isinstance(x, str) for x in outs):
            raise TranslateError("format", "译文不是字符串")
        if len(outs) != len(texts):
            raise TranslateError("format", f"译文 {len(outs)} 条，原文 {len(texts)} 段")
        return outs

    def translate_stream(self, jobs: list[Job], direction: str, mappings: list[tuple[str, str]],
                         on_segment: Callable[[int, str], None],
                         cancel: threading.Event | None = None) -> None:
        if cancel is not None and cancel.is_set():
            return
        if not jobs:
            return
        # 先在原文（占位符还是 ⟨n⟩）上换词条，再转成发送格式；wires 是不带替换的版本（记号丢了时重发用）
        wires = [to_wire(j.text) for j in jobs]
        prepared = []
        for j in jobs:
            mapped, slots = apply_mappings(j.text, mappings)
            prepared.append((to_wire(mapped), slots))
        texts = [p[0].text for p in prepared]
        done: set[int] = set()
        empty: list[int] = []
        for batch in batches(texts):
            if cancel is not None and cancel.is_set():
                return
            try:
                outs = self._request([texts[i] for i in batch], direction)
            except TranslateError as e:
                e.done = frozenset(done)
                raise
            for i, out in zip(batch, outs):
                if cancel is not None and cancel.is_set():
                    return
                job, (wire, slots) = jobs[i], prepared[i]
                text = restore_mappings(out, slots, direction == "en2zh") if slots else out
                lead = _TERM.match(out.lstrip()) if slots and text is not None and direction == "zh2en" else None
                if lead and slots.get(lead.group(1), "").islower():
                    # 换回的全小写译法落在句首（「快感缺失是…」→ "anhedonia is…"）：句首大写；fMRI 这类带大写的不动
                    text = text.lstrip()
                    text = text[:1].upper() + text[1:]
                if text is None:  # 指定译法的记号丢了：这一段不带替换重翻（没按译法翻，总比没有好）
                    log.debug("微软翻译丢了术语记号，第 %s 段不带替换重翻", job.idx)
                    wire = wires[i]
                    try:
                        text = self._request([wire.text], direction)[0]
                    except TranslateError as e:
                        e.done = frozenset(done)
                        raise
                text = from_wire(text, wire).strip()
                if not text:
                    empty.append(job.idx)  # 空译文不交，结尾按 format 报，剩下的段落交给下一层
                    continue
                done.add(job.idx)
                on_segment(job.idx, text)
        if empty:
            raise TranslateError("format", f"段落 {empty} 译文为空", done)

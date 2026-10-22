"""腾讯交互翻译（TranSmart）：免 key 的机器翻译，第三层兜底。

这是网页版用的非官方接口，可能随时失效；失效时一律抛 TranslateError，由路由显示原文。
- 一次请求提交整张截图的全部段落，按返回顺序逐段交出（不是流式，整批一起回来）。
- 占位符：原样的 ⟨n⟩ 会被改坏（实测变成「102度」），{n} 能原样保留，所以发送前换成 {n}、收到后换回；
  原文本来就有 {数字} 的段改用 __Pn__（2026-10-10 实测同样原样保留）。
- 返回文本里 < 会变成 &lt;，收到后反转义。
- 不支持「指定译法」（术语表的 mappings 忽略）；「保持原样」靠占位符实现，照常生效。"""
from __future__ import annotations

import html
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .base import Job, TranslateError

log = logging.getLogger(__name__)

TRANSMART_URL = "https://transmart.qq.com/api/imt"
KEEPALIVE = 120.0  # 闲置连接保留秒数（httpx 默认 5 秒，截图间隔通常更长）
READ_PER_CHARS = 500  # 读超时：每这么多字符多给 1 秒（整页 4500 字符实测 3.7～11 秒）
READ_CAP = 30.0       # 读超时上限（秒）
# 每个进程生成一次，冒充一个浏览器客户端
CLIENT_KEY = f"browser-chrome-130.0.0-Windows_10-{uuid.uuid4()}"
STALE_ERRORS = (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)  # 过期连接的表现；超时不在其中
LANGS = {"en2zh": ("en", "zh"), "zh2en": ("zh", "en")}

_SLOT = re.compile(r"⟨(\d+)⟩")
_BRACE = re.compile(r"\{\s*(\d+)\s*\}")


_ALT = re.compile(r"__\s*P\s*(\d+)\s*__")


@dataclass(frozen=True)
class Wire:
    """一段发出去的文本，以及换回占位符需要的信息。"""
    text: str
    nums: frozenset[str]  # 换过的占位符编号（换回时只认这些）
    alt: bool             # True = 用的是 __Pn__ 形式


def to_wire(text: str) -> Wire:
    """⟨n⟩ → {n}。原文里本来就有 {数字}（如代码里的 {0}）时，这一段改用 __Pn__（实测 TranSmart 也原样保留），
    免得译文回来分不清哪个 {n} 是我们的。"""
    nums = frozenset(_SLOT.findall(text))
    if _BRACE.search(text):
        return Wire(_SLOT.sub(lambda m: f"__P{m.group(1)}__", text), nums, True)
    return Wire(_SLOT.sub(lambda m: "{" + m.group(1) + "}", text), nums, False)


def from_wire(text: str, w: Wire) -> str:
    pat = _ALT if w.alt else _BRACE
    text = pat.sub(lambda m: f"⟨{m.group(1)}⟩" if m.group(1) in w.nums else m.group(0), text)
    return html.unescape(text)


class TencentClient:
    name = "tencent"

    def __init__(self, timeout_connect: float = 2.0, timeout_read: float = 8.0, url: str | None = None):
        self.timeout_connect = timeout_connect
        self.timeout_read = timeout_read
        self.url = url or TRANSMART_URL
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        # 预热和真请求互斥：预热进行中，真请求等它建好连接再发（复用这条连接）；有真请求在跑，预热直接跳过
        self._cv = threading.Condition()
        self._warming = False
        self._inflight = 0

    def _http(self) -> httpx.Client:
        """连接复用：首次请求要建 TLS 连接（实测约 3.8 秒），之后约 1 秒。
        闲置连接保留 KEEPALIVE 秒。不传自定义 transport：传了 httpx 就不再读环境/注册表里的系统代理
        （本机 HTTP(S)_PROXY=127.0.0.1:7897，腾讯必须走它）。服务端先断了的旧连接由 _retry_stale 重试一次。"""
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(
                    timeout=httpx.Timeout(self.timeout_read, connect=self.timeout_connect),
                    limits=httpx.Limits(keepalive_expiry=KEEPALIVE),
                )
            return self._client

    @staticmethod
    def _retry_stale(send: Callable[[], httpx.Response]) -> httpx.Response:
        """复用的旧连接可能已被服务端关掉：这类断开（没收到响应、读/写出错）重试一次；超时不重试。"""
        try:
            return send()
        except STALE_ERRORS as e:
            log.debug("腾讯连接已过期，重试一次：%r", e)
            return send()

    def read_timeout_for(self, texts: list[str]) -> float:
        """读超时随这批文字的长度放宽：基础 timeout_read，每 READ_PER_CHARS 个字符加 1 秒，最多 READ_CAP 秒。"""
        return min(READ_CAP, self.timeout_read + sum(len(t) for t in texts) / READ_PER_CHARS)

    def _post(self, payload: dict, read_timeout: float) -> httpx.Response:
        """发一次请求；复用的旧连接恰好被服务端关掉（没收到任何响应就断开）时，透明地重发一次。"""
        headers = {"Content-Type": "application/json; charset=utf-8"}
        timeout = httpx.Timeout(read_timeout, connect=self.timeout_connect)
        return self._retry_stale(lambda: self._http().post(self.url, json=payload, headers=headers, timeout=timeout))

    def model_name(self) -> str:
        return "腾讯交互翻译"

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
            log.debug("腾讯预热失败：%r", e)
        finally:
            with self._cv:
                self._warming = False
                self._cv.notify_all()

    def warm_async(self) -> threading.Thread:
        """在后台 daemon 线程里预热，立刻返回。"""
        t = threading.Thread(target=self.warm, daemon=True, name="shotrans-tencent-warm")
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

    def translate_stream(self, jobs: list[Job], direction: str, mappings: list[tuple[str, str]],
                         on_segment: Callable[[int, str], None],
                         cancel: threading.Event | None = None) -> None:
        if cancel is not None and cancel.is_set():
            return
        if not jobs:
            return
        src, dst = LANGS[direction]
        wires = [to_wire(j.text) for j in jobs]
        payload = {
            "header": {"fn": "auto_translation", "client_key": CLIENT_KEY},
            "type": "plain",
            "model_category": "normal",
            "source": {"lang": src, "text_list": [w.text for w in wires]},
            "target": {"lang": dst},
        }
        self._begin_request()
        try:
            resp = self._post(payload, self.read_timeout_for([w.text for w in wires]))
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
        if not isinstance(obj, dict):
            raise TranslateError("format", "返回的 JSON 不是对象")
        header = obj.get("header")
        ret = header.get("ret_code") if isinstance(header, dict) else None
        if ret != "succ":
            raise TranslateError("format", f"ret_code = {ret!r}")
        outs = obj.get("auto_translation")
        if not isinstance(outs, list) or not all(isinstance(x, str) for x in outs):
            raise TranslateError("format", "auto_translation 缺失或不是字符串列表")
        if len(outs) != len(jobs):
            raise TranslateError("format", f"译文 {len(outs)} 条，原文 {len(jobs)} 段")
        done: set[int] = set()
        for job, w, out in zip(jobs, wires, outs):
            if cancel is not None and cancel.is_set():
                return
            text = from_wire(out, w).strip()
            if not text:
                continue  # 空译文不交，结尾按 format 报，剩下的段落交给下一层
            done.add(job.idx)
            on_segment(job.idx, text)
        missing = [j.idx for j in jobs if j.idx not in done]
        if missing:
            raise TranslateError("format", f"段落 {missing} 译文为空", done)

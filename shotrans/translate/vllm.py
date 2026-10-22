"""内网 vLLM：一次请求翻整张截图，流式接收，按 ⟦n⟧ 边收边交。

超时判定看「第一个字多久出来」和「多久没有新字」，不设总时长上限。"""
from __future__ import annotations

import copy
import ipaddress
import json
import queue
import re
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .base import LANG_NAMES, Job, TranslateError

_MARK = re.compile(r"⟦\s*(\d+)\s*⟧")
KEY_NOT_ASCII = "key 里有非 ASCII 字符，请求发不出去（请重新复制 key）"
AUTH_STATUS = frozenset({401, 403})  # key 不对 / 没填：报 auth，和「连不上」区分开
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # 运营商级 NAT / Tailscale 等，ip.is_private 不含它


def is_local_host(base_url: str) -> bool:
    """地址是 localhost，或内网/本机/链路本地的 IP 字面量。
    这类地址不该走系统代理（本机设了 HTTP_PROXY 时，内网 vLLM 会被送进代理，/models 返回 502）。"""
    host = (urlsplit(base_url).hostname or "").lower()
    if host.rstrip(".") == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip in _CGNAT


# Task 20：大模型曾把作者行「Gongbo Zhang, Wen Wang…」译成「龚博张，王文…」。中译英、英译中都适用。
# 后半句是实测补的：只有前半句时，论文标题「Attention Is All You Need」「Turning the TIDE: …」两次都被原样留下
# （当成了专名）；加上后 3 次都照常翻译，人名、代码、路径仍保持原文。
# Task 21：加上面两句后，模型又把普通的大写开头词也当专名留下（「Blood project」→「论文中的 Blood 项目」、
# 「Cascade journal」没译），所以再补一句：只有确实是专名的才保留。实测（各 3 次）：这句不能举例——
# 写成「（如 Blood、Journal）」反而 6/6 把 Blood 留着（被例词带偏）；不举例时 3/3 译成「血液项目」，
# 人名、产品名仍保持原文、论文标题仍翻译。
NAMES_RULE = ("4. 人名、机构名、产品/软件名、代码、命令、按键组合、文件路径、网址保持原文，不翻译、不音译。"
              "其余文字（包括论文标题、栏目名、按钮文字）都要翻译。"
              "只有确实是专名的才保持原文；句首或标题、标签里首字母大写的普通词照常翻译。")


def build_messages(jobs: list[Job], direction: str, mappings: list[tuple[str, str]],
                   domain_hint: str = "") -> list[dict]:
    src, dst = LANG_NAMES[direction]
    rules = [
        f"你是专业翻译。把用户给出的{src}逐段翻译成{dst}，忠实、通顺，保留原有格式。",
        "规则：",
        "1. 只输出译文，不解释，不加任何前言或说明。",
        "2. 每段以原编号开头，格式为 ⟦编号⟧，按原顺序输出，不要合并或拆分段落。",
        "3. ⟨数字⟩ 形式的占位符原样保留，不翻译、不改动、不删除。",
        NAMES_RULE,
    ]
    if domain_hint.strip():
        rules.append(f"领域：{domain_hint.strip()}")
    if mappings:
        rules.append("术语译法（必须遵守）：")
        rules += [f"- {s} → {d}" for s, d in mappings]
    user = "\n".join(f"⟦{j.idx}⟧ {j.text}" for j in jobs)
    return [{"role": "system", "content": "\n".join(rules)}, {"role": "user", "content": user}]


class SegmentStreamParser:
    """看到下一个 ⟦n⟧ 时，上一段才算完整。"""

    def __init__(self):
        self.buf = ""
        self.raw = ""

    def feed(self, chunk: str) -> list[tuple[int, str]]:
        self.buf += chunk
        self.raw += chunk
        marks = list(_MARK.finditer(self.buf))
        out = []
        if len(marks) >= 2:
            for a, b in zip(marks, marks[1:]):
                out.append((int(a.group(1)), self.buf[a.end():b.start()].strip()))
            self.buf = self.buf[marks[-1].start():]
        return out

    def finish(self) -> list[tuple[int, str]]:
        marks = list(_MARK.finditer(self.buf))
        if not marks:
            return []
        m = marks[-1]
        return [(int(m.group(1)), self.buf[m.end():].strip())]


# 「关闭思考」各家的字段不一样（2026-10-13 查证）：
# - vLLM / SGLang 部署的 Qwen3 等：chat_template_kwargs.enable_thinking（原有行为）
# - 阿里云百炼（help.aliyun.com/zh/model-studio/deep-thinking）、硅基流动（docs.siliconflow.cn chat-completions）：顶层 enable_thinking
# - DeepSeek 官方（api-docs.deepseek.com/guides/thinking_mode）：thinking.type = disabled（默认开着思考）
# - OpenAI：reasoning_effort = none（GPT-5.1 起的推理模型支持 "none"；更早的推理模型最低只到 "minimal"/"low"）
THINKING_OFF_FIELDS: dict[str, dict] = {
    "chat_template_kwargs": {"chat_template_kwargs": {"enable_thinking": False}},
    "enable_thinking": {"enable_thinking": False},
    "deepseek": {"thinking": {"type": "disabled"}},
    "openai_reasoning": {"reasoning_effort": "none"},
}


REASONING_TOKEN_BUDGET = 2000  # 推理模型的长度上限要给推理留足余量


def sampling_params(thinking_style: str, max_tokens: int | None = None) -> dict:
    """请求体里的 temperature 与长度上限；测试连接和真翻译都走这里，规则只写一处。

    OpenAI 推理模型（2026-10-16 查证，记录见 docs/预设核实-2026-10-14.md）：
    - max_tokens 已弃用、与推理模型不兼容，改用 max_completion_tokens；
    - reasoning_effort 不是 none 时必须去掉 temperature。用户可能取消勾选「关闭思考」（默认 medium），
      或换成不支持 none 的型号，所以这一类一律不发 temperature，不看 thinking_off。
    - max_completion_tokens 连推理 token 一起算：思考开着时 200 可能全被推理吃掉、正文为空（测试连接报
      bad_format），所以这一类有上限时一律用 REASONING_TOKEN_BUDGET，不看传进来的值。
    其余方式（自建 vLLM、DeepSeek、硅基流动、百炼、不认识的值）保持原样。"""
    if thinking_style == "openai_reasoning":
        return {} if max_tokens is None else {"max_completion_tokens": REASONING_TOKEN_BUDGET}
    out: dict = {"temperature": 0.2}
    if max_tokens is not None:
        out["max_tokens"] = max_tokens
    return out


def _extract_delta(data: str) -> str:
    """取一个 SSE 事件里的增量文字；任何畸形事件都当作没有内容。"""
    try:
        obj = json.loads(data)
    except ValueError:
        return ""
    if not isinstance(obj, dict):
        return ""
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""


class VllmClient:
    name = "vllm"

    def __init__(self, base_url: str, api_key: str, connect_timeout: float = 2.0,
                 first_token_timeout: float = 3.0, stall_timeout: float = 10.0, domain_hint: str = "",
                 name: str = "vllm", model: str = "", thinking_off: bool = True,
                 thinking_style: str = "chat_template_kwargs"):
        if thinking_style not in THINKING_OFF_FIELDS:
            raise ValueError(f"不认识的关闭思考方式：{thinking_style!r}")
        self.thinking_style = thinking_style
        self.name = name
        self.thinking_off = thinking_off
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.connect_timeout = connect_timeout
        self.first_token_timeout = first_token_timeout
        self.stall_timeout = stall_timeout
        self.domain_hint = domain_hint
        self._model: str | None = model or None
        # 内网/本机地址绕过环境变量和注册表里的代理；公网地址照旧（可能本来就要靠代理才连得上）
        self.trust_env = not is_local_host(self.base_url)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def model_name(self) -> str:
        if self._model is None:
            timeout = httpx.Timeout(self.first_token_timeout, connect=self.connect_timeout)
            try:
                r = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=timeout,
                              trust_env=self.trust_env)
            except httpx.TransportError as e:
                raise TranslateError("connect", str(e)) from e
            except UnicodeError as e:  # key 里有非 ASCII：异常原文带着整把 key，绝不外传，按 key 不对处理（会换下一层）
                raise TranslateError("auth", KEY_NOT_ASCII) from None
            if r.status_code in AUTH_STATUS:
                raise TranslateError("auth", f"/models 返回 {r.status_code}（key 无效或未填）")
            if r.status_code != 200:
                raise TranslateError("http", f"/models 返回 {r.status_code}")
            try:
                data = r.json().get("data") or []
                if not data:
                    raise TranslateError("http", "服务器上没有模型")
                model = data[0]["id"]
            except TranslateError:
                raise
            except (ValueError, AttributeError, KeyError, IndexError, TypeError) as e:
                raise TranslateError("http", f"/models 返回的内容无法识别：{e!r}") from e
            if not isinstance(model, str) or not model:
                raise TranslateError("http", "/models 返回的模型 id 不是字符串")
            self._model = model
        return self._model

    def translate_stream(self, jobs: list[Job], direction: str, mappings: list[tuple[str, str]],
                         on_segment: Callable[[int, str], None],
                         cancel: threading.Event | None = None) -> None:
        if cancel is not None and cancel.is_set():
            return
        payload = {
            "model": self.model_name(),
            "messages": build_messages(jobs, direction, mappings, self.domain_hint),
            "stream": True,
            **sampling_params(self.thinking_style),
        }
        if self.thinking_off:  # 只发这家认的那一个字段；别家的未知字段可能报 400
            payload.update(copy.deepcopy(THINKING_OFF_FIELDS[self.thinking_style]))
        q: queue.Queue = queue.Queue()
        holder: dict = {}

        # 兜底读超时：主线程的首字/断流判定之外，保证服务器收了请求却一直不回时 worker 也会退出
        safety = max(self.first_token_timeout, self.stall_timeout) + 1.0

        def worker() -> None:
            try:
                client = httpx.Client(timeout=httpx.Timeout(safety, connect=self.connect_timeout),
                                      trust_env=self.trust_env)
                holder["client"] = client
                with client.stream("POST", f"{self.base_url}/chat/completions", json=payload,
                                   headers=self._headers()) as resp:
                    holder["resp"] = resp
                    if resp.status_code != 200:
                        kind = "auth" if resp.status_code in AUTH_STATUS else "http"
                        q.put((kind, f"HTTP {resp.status_code}"))
                        return
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        delta = _extract_delta(data)
                        if delta:
                            q.put(("text", delta))
                q.put(("end", ""))
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                q.put(("connect", str(e)))
            except UnicodeError:  # 同 model_name：编码请求头失败，原文里有 key
                q.put(("auth", KEY_NOT_ASCII))
            except Exception as e:  # 被主线程关掉连接等。只报类型名：异常原文可能带出请求头（key）
                q.put(("http", type(e).__name__))

        threading.Thread(target=worker, daemon=True, name="shotrans-vllm-worker").start()
        parser = SegmentStreamParser()
        wanted = {j.idx for j in jobs}
        done: set[int] = set()
        got_text = False

        def deliver(pairs: list[tuple[int, str]]) -> None:
            for idx, text in pairs:
                if idx in wanted and idx not in done and text:
                    done.add(idx)
                    on_segment(idx, text)

        def cancelled() -> bool:
            return cancel is not None and cancel.is_set()

        last = time.monotonic()
        try:
            while True:
                if cancelled():
                    return
                timeout = self.stall_timeout if got_text else self.first_token_timeout
                remaining = last + timeout - time.monotonic()
                if remaining <= 0:
                    raise TranslateError("stall" if got_text else "first_token",
                                         f"{timeout} 秒没有新内容", done)
                try:
                    kind, val = q.get(timeout=min(0.1, remaining))
                except queue.Empty:
                    continue
                if cancelled():
                    return
                last = time.monotonic()
                if kind == "text":
                    got_text = True
                    deliver(parser.feed(val))
                elif kind == "end":
                    deliver(parser.finish())
                    if not done and len(jobs) == 1 and parser.raw.strip():
                        done.add(jobs[0].idx)
                        on_segment(jobs[0].idx, parser.raw.strip())
                    missing = wanted - done
                    if missing:
                        raise TranslateError("format", f"缺少段落 {sorted(missing)}", done)
                    return
                else:
                    raise TranslateError(kind, val, done)
        finally:
            for key in ("resp", "client"):
                obj = holder.get(key)
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass

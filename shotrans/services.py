"""翻译服务预设、测试连接、获取模型列表。不碰界面；网络调用可注入 httpx 传输层（测试用 MockTransport）。

预设内容 2026-10-14 按各家官方文档核实，记录见 docs/预设核实-2026-10-14.md。
测试连接真翻一句 "Hello, world."（非流式），按结果分类成人话原因；错误细节里绝不出现 key。"""
from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass, replace
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .config import ServiceConfig
from .i18n import N_, t
from .translate.base import Job
from .translate.vllm import THINKING_OFF_FIELDS, build_messages, is_local_host, sampling_params

TEST_TEXT = "Hello, world."
DETAIL_MAX = 200
CONN_KINDS = ("ok", "auth", "unreachable", "model_not_found", "timeout", "bad_format", "http")
_MARK = re.compile(r"^\s*⟦\s*1\s*⟧\s*")


@dataclass(frozen=True)
class Preset:
    id: str
    name_zh: str
    name_en: str
    base_url: str
    model: str
    signup_url: str
    cost_zh: str
    cost_en: str
    thinking_style: str
    thinking_off_default: bool

    def name(self, lang: str) -> str:
        return self.name_en if lang == "en" else self.name_zh

    def cost(self, lang: str) -> str:
        return self.cost_en if lang == "en" else self.cost_zh


PRESETS: tuple[Preset, ...] = (
    # 核实 2026-10-14：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
    # （思考默认开着，关法见 https://api-docs.deepseek.com/guides/thinking_mode）
    Preset("deepseek", "DeepSeek", "DeepSeek", "https://api.deepseek.com", "deepseek-flash",
           "https://platform.deepseek.com/api_keys",
           "按量计费，从充值或赠送余额扣；deepseek-flash 闲时约输入 1 元、输出 4 元每百万 token，"
           "工作日高峰时段翻倍（以官网价格为准）",
           "Pay as you go; deepseek-flash off-peak ≈ $0.15 in / $0.6 out per 1M tokens, "
           "double at weekday peak hours (see the official pricing page)",
           "deepseek", True),
    # 核实 2026-10-14：https://docs.siliconflow.cn/cn/api-reference/chat-completions/chat-completions
    # （模型在 https://cloud.siliconflow.cn/open/models 仍在列、未标弃用；非思考模型，不发关闭字段）
    Preset("siliconflow", "硅基流动", "SiliconFlow", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct",
           "https://cloud.siliconflow.cn/account/ak",
           "按量计费，部分模型免费（以官网模型广场与价格页为准）",
           "Pay as you go; some models are free (see the official model square and pricing page)",
           "enable_thinking", False),
    # 核实 2026-10-14：https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope
    # （旧域名「仍可正常使用」，新域名要业务空间 ID、没法做成预设；qwen-flash 默认不思考见 deep-thinking 页）
    Preset("bailian", "阿里云百炼", "Alibaba Cloud Bailian", "https://dashscope.aliyuncs.com/compatible-mode/v1",
           "qwen-flash", "https://help.aliyun.com/zh/model-studio/get-api-key",
           "按量计费；qwen-flash 约输入 0.15 元、输出 1.5 元每百万 token（北京地域、单次输入 ≤128K），"
           "新开通有限期免费额度；key 按地域绑定、不通用（新加坡地域地址用 dashscope-intl.aliyuncs.com）；以官网价格为准",
           "Pay as you go; qwen-flash ≈ ¥0.15 in / ¥1.5 out per 1M tokens (Beijing, input ≤128K), "
           "time-limited free quota on activation; keys are bound to a region (Singapore uses "
           "dashscope-intl.aliyuncs.com); see the official pricing page",
           "enable_thinking", False),
    # 核实 2026-10-14：https://developers.openai.com/api/docs/models/gpt-6-luna
    # （推理模型，reasoning_effort 支持 none、默认 medium；价格见 https://developers.openai.com/api/docs/pricing）
    Preset("openai", "OpenAI", "OpenAI", "https://api.openai.com/v1", "gpt-6-luna",
           "https://platform.openai.com/api-keys",
           "按量计费；gpt-6-luna 约输入 $0.10、输出 $0.50 每百万 token（以官网价格为准）",
           "Pay as you go; gpt-6-luna ≈ $0.10 in / $0.50 out per 1M tokens (see the official pricing page)",
           "openai_reasoning", True),
    Preset("custom", "自定义", "Custom", "", "", "", "", "", "chat_template_kwargs", True),
)


def preset(pid: str) -> Preset:
    return next((p for p in PRESETS if p.id == pid), PRESETS[-1])


def apply_preset(svc: ServiceConfig, pid: str) -> ServiceConfig:
    p = preset(pid)
    if p.id == "custom":
        return replace(svc, preset="custom")
    return replace(svc, preset=p.id, base_url=p.base_url, model=p.model,
                   thinking_off=p.thinking_off_default, thinking_style=p.thinking_style)


def normalize_base_url(url: str) -> str:
    u = url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if u.lower().endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
            break
    return u


@dataclass(frozen=True)
class ConnResult:
    kind: str
    seconds: float
    detail: str = ""
    sample: str = ""
    model: str = ""


@dataclass(frozen=True)
class ModelsResult:
    kind: str
    models: tuple[str, ...] = ()
    detail: str = ""


def _scrub(text: str, key: str) -> str:
    if key:
        text = text.replace(key, "***")
    return text[:DETAIL_MAX]


def _client(base_url: str, connect: float, read: float, transport) -> httpx.Client:
    kw = {"timeout": httpx.Timeout(read, connect=connect), "trust_env": not is_local_host(base_url)}
    if transport is not None:
        kw["transport"] = transport
    return httpx.Client(**kw)


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


def _error_message(resp: httpx.Response) -> str:
    try:
        obj = resp.json()
    except ValueError:
        return resp.text
    if isinstance(obj, dict):
        err = obj.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"]
        if isinstance(err, str):
            return err
        if isinstance(obj.get("message"), str):
            return obj["message"]
    return resp.text


_NO_MODEL_HINTS = ("not exist", "not found", "does not exist", "invalid model", "model_not_found")


def _error_code(resp: httpx.Response) -> str:
    try:
        obj = resp.json()
    except ValueError:
        return ""
    if not isinstance(obj, dict):
        return ""
    err = obj.get("error")
    code = err.get("code") if isinstance(err, dict) else obj.get("code")
    return code if isinstance(code, str) else ""


def classify_status(status: int, message: str, code: str = "") -> str:
    if status in (401, 403):
        return "auth"
    low = message.lower()
    if status == 400 and (code == "model_not_found" or any(h in low for h in _NO_MODEL_HINTS)):
        return "model_not_found"
    if status == 404 and ("model" in low or code == "model_not_found"):
        return "model_not_found"
    if status == 404:
        return "unreachable"  # 路径不对（常见：地址少了 /v1）
    return "http"


def _transport_kind(e: Exception) -> str:
    if isinstance(e, (httpx.ConnectTimeout, httpx.ConnectError)):
        return "unreachable"
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    return "unreachable"


NON_ASCII_KEY = N_("conn.detail.non_ascii_key")  # 文字键：用时再 t()，跟随当前语言


def _scheme_ok(base: str) -> bool:
    return base.lower().startswith(("http://", "https://")) and bool(urlsplit(base).hostname)


def list_models(base_url: str, api_key: str = "", *, connect_timeout: float = 6.0, read_timeout: float = 10.0,
                transport=None) -> ModelsResult:
    base, key = normalize_base_url(base_url), api_key.strip()
    if not _scheme_ok(base):
        return ModelsResult("unreachable", (), t("conn.detail.scheme"))
    if not key.isascii():
        return ModelsResult("auth", (), t(NON_ASCII_KEY))
    try:
        with _client(base, connect_timeout, read_timeout, transport) as c:
            r = c.get(f"{base}/models", headers=_headers(key))
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as e:
        return ModelsResult(_transport_kind(e), (), type(e).__name__)
    if r.status_code != 200:
        kind = "auth" if r.status_code in (401, 403) else "unreachable" if r.status_code == 404 else "http"
        return ModelsResult(kind, (), _scrub(f"HTTP {r.status_code}: {_error_message(r)}", key))
    try:
        data = r.json()["data"]
        ids = tuple(m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"])
    except (ValueError, KeyError, TypeError):
        return ModelsResult("bad_format", (), t("conn.detail.not_list"))
    if not ids:
        return ModelsResult("bad_format", (), t("conn.detail.no_models"))
    return ModelsResult("ok", ids)


def _content(resp: httpx.Response) -> str | None:
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    return _MARK.sub("", text).strip()


def test_connection(svc: ServiceConfig, *, connect_timeout: float = 6.0, read_timeout: float = 20.0,
                    transport=None, clock: Callable[[], float] = time.perf_counter) -> ConnResult:
    t0 = clock()
    base, key = normalize_base_url(svc.base_url), svc.api_key.strip()

    def done(kind: str, detail: str = "", sample: str = "", model: str = "") -> ConnResult:
        return ConnResult(kind, round(clock() - t0, 3), _scrub(detail, key), sample, model)

    if not _scheme_ok(base):
        return done("unreachable", t("conn.detail.scheme"))
    if not key.isascii():
        return done("auth", t(NON_ASCII_KEY))
    model = svc.model.strip()
    if not model:
        ms = list_models(base, key, connect_timeout=connect_timeout, read_timeout=read_timeout, transport=transport)
        if ms.kind != "ok":
            return done(ms.kind, ms.detail)
        model = ms.models[0]
    payload = {"model": model, "messages": build_messages([Job(1, TEST_TEXT)], "en2zh", []),
               "stream": False, **sampling_params(svc.thinking_style, 200)}
    if svc.thinking_off:
        payload.update(copy.deepcopy(THINKING_OFF_FIELDS.get(svc.thinking_style, {})))
    try:
        with _client(base, connect_timeout, read_timeout, transport) as c:
            r = c.post(f"{base}/chat/completions", json=payload, headers=_headers(key))
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError) as e:
        return done(_transport_kind(e), type(e).__name__, model=model)
    if r.status_code != 200:
        msg = _error_message(r)
        return done(classify_status(r.status_code, msg, _error_code(r)), f"HTTP {r.status_code}: {msg}", model=model)
    text = _content(r)
    if text is None:
        return done("bad_format", r.text, model=model)
    return done("ok", sample=text, model=model)


test_connection.__test__ = False  # 名字以 test_ 开头：别让 pytest 当测试收集

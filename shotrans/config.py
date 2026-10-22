"""配置：读 config.toml（新格式）或旧的 config.local.toml（[vllm]/[fallback]），写回 config.toml。

新格式的节：[general] [primary] [backup] [tencent] [hotkey] [translate] [timeouts] [history]。
api_key 加密存为 api_key_enc（DPAPI，见 secret.py）；手写的明文 api_key 也认，下次保存时转为加密并删去明文。
旧格式（有 [vllm]/[fallback]、没有 [primary]/[backup]）先由 legacy_to_v2 转成新格式再读，含义不变：
vLLM 没填 key = 不用主服务；超时沿用旧默认值（连接 2、首字 3、断流 10）。"""
from __future__ import annotations

import dataclasses
import math
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import secret
from .hotkey import parse_hotkey
from .i18n import t
from .tomlw import dump_toml
from .translate.vllm import THINKING_OFF_FIELDS


class ConfigError(Exception):
    pass


LEGACY_VLLM_URL = ""  # 旧格式 [vllm] 没写 base_url 时：不再假定某个内网地址，当作没配置主服务
LANGUAGES = ("zh", "en")
THINKING_STYLES = tuple(THINKING_OFF_FIELDS)
# 只对旧格式（[vllm]/[fallback]）文件：没写 [tencent] enabled 时按开着算——那个年代默认开着，
# 迁移后不能悄悄没了兜底（迁移会写成显式的 enabled = true）。新格式文件没写 = 关（与 Config() 一致），
# 免得新用户手写的配置在主服务失败时悄悄把文字发给腾讯。程序自己写的 config.toml 一向显式写出 enabled。
LEGACY_TENCENT_IF_UNSET = True
SECTIONS = ("general", "primary", "backup", "tencent", "hotkey", "translate", "timeouts", "history")
_LEGACY_SECTIONS = ("vllm", "fallback")
_SECRET_KEYS = frozenset({"api_key", "api_key_enc"})
HEADER = "截图翻译的配置（由程序写入；手改后在托盘点「重新加载配置」）。\nAPI key 已加密，换电脑或换用户需要重新填写。"


@dataclass(frozen=True)
class ServiceConfig:
    preset: str = "custom"
    base_url: str = ""
    api_key: str = ""           # 内存里是明文；写文件时加密
    model: str = ""             # 空 = 用服务端 /models 的第一个
    thinking_off: bool = False
    thinking_style: str = "chat_template_kwargs"
    key_plaintext: bool = False   # 文件里是明文 api_key（下次保存转加密）
    key_unreadable: bool = False  # api_key_enc 解不开（换了电脑/用户）：当作没填
    # 读入时文件里的原密文。key 解不开且没重新填（api_key 仍为空）时，保存会原样写回它，不把它清空；
    # 要真的删掉这把 key，用 dataclasses.replace(svc, key_enc="", key_unreadable=False)。不参与比较、不进 repr。
    key_enc: str = field(default="", compare=False, repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip())


@dataclass(frozen=True)
class Config:
    language: str = "zh"
    autostart: bool | None = None   # None = 配置里没写，不去动启动文件夹
    primary: ServiceConfig = field(default_factory=ServiceConfig)
    backup: ServiceConfig = field(default_factory=ServiceConfig)
    hotkey: str = "Ctrl+Alt+Q"
    domain_hint: str = ""
    chunk_chars: int = 600
    chunk_max_segments: int = 8
    max_concurrency: int = 4
    connect_timeout: float = 6.0
    first_token_timeout: float = 10.0
    stall_timeout: float = 20.0
    breaker_seconds: float = 60.0
    backup_connect_timeout: float = 6.0
    backup_first_token_timeout: float = 10.0
    backup_stall_timeout: float = 20.0
    history_limit: int = 200
    history_save_images: bool = True  # 历史记录连同截图（原图、译文图，PNG）一起存在 history_images\
    tencent_enabled: bool = False   # 默认不用腾讯兜底；旧格式文件没写这一项的例外见 LEGACY_TENCENT_IF_UNSET
    tencent_connect_timeout: float = 6.0


# (节, 键, 字段名, 类型)。默认值一律取 Config() 的，不在这里重复写
_SCHEMA = [
    ("general", "language", "language", str),
    ("hotkey", "capture", "hotkey", str),
    ("translate", "domain_hint", "domain_hint", str),
    ("translate", "chunk_chars", "chunk_chars", int),
    ("translate", "chunk_max_segments", "chunk_max_segments", int),
    ("translate", "max_concurrency", "max_concurrency", int),
    ("timeouts", "connect", "connect_timeout", float),
    ("timeouts", "first_token", "first_token_timeout", float),
    ("timeouts", "stall", "stall_timeout", float),
    ("timeouts", "breaker", "breaker_seconds", float),
    ("timeouts", "backup_connect", "backup_connect_timeout", float),
    ("timeouts", "backup_first_token", "backup_first_token_timeout", float),
    ("timeouts", "backup_stall", "backup_stall_timeout", float),
    ("history", "limit", "history_limit", int),
    ("history", "save_images", "history_save_images", bool),
    ("tencent", "enabled", "tencent_enabled", bool),
    ("tencent", "connect", "tencent_connect_timeout", float),
]
_SERVICE_SCHEMA = [("preset", str), ("base_url", str), ("model", str), ("thinking_off", bool), ("thinking_style", str)]
_MIN1 = [("[translate] chunk_chars", "chunk_chars"), ("[translate] chunk_max_segments", "chunk_max_segments"),
         ("[translate] max_concurrency", "max_concurrency"), ("[history] limit", "history_limit")]
_POSITIVE = [("[timeouts] connect", "connect_timeout"), ("[timeouts] first_token", "first_token_timeout"),
             ("[timeouts] stall", "stall_timeout"), ("[timeouts] breaker", "breaker_seconds"),
             ("[timeouts] backup_connect", "backup_connect_timeout"),
             ("[timeouts] backup_first_token", "backup_first_token_timeout"),
             ("[timeouts] backup_stall", "backup_stall_timeout"), ("[tencent] connect", "tencent_connect_timeout")]


@dataclass(frozen=True)
class FieldError:
    field: str   # 例 "[timeouts] stall"
    code: str    # type / language / hotkey / min1 / positive / url / thinking_style / key
    value: object
    detail: str = ""


def describe(e: FieldError) -> str:
    if e.code == "type":  # detail = 期望的类型名；value 只说类型，不回显（可能是 key）
        return t("config.err.field_type", field=e.field, type=e.detail, shown=type(e.value).__name__)
    if e.code == "hotkey":
        return t("config.err.hotkey", detail=e.detail)
    if e.code == "min1":
        return t("config.err.min1", field=e.field, value=repr(e.value))
    if e.code == "positive":
        return t("config.err.positive", field=e.field, value=repr(e.value))
    if e.code == "url":
        return t("config.err.url", field=e.field, value=repr(e.value))
    if e.code == "language":
        return t("config.err.language", value=repr(e.value))
    if e.code == "key":  # 只说原因，绝不回显 key
        return t("config.err.key", field=e.field)
    if e.code == "thinking_style":
        return t("config.err.thinking_style", field=e.field, choices=t("list.sep").join(THINKING_STYLES),
                 value=repr(e.value))
    return t("config.err.other", field=e.field, value=repr(e.value))


_FIELD_NAMES = {fname: f"[{section}] {key}" for section, key, fname, _ in _SCHEMA}
_FIELD_TYPES = {fname: typ for _, _, fname, typ in _SCHEMA}


def _type_ok(v, typ) -> bool:
    if isinstance(v, bool):  # bool 是 int 的子类：数值项不许用 true/false 冒充
        return typ is bool
    if typ is float:
        return isinstance(v, (int, float))
    return isinstance(v, typ)


def _type_errors(c: Config) -> list[FieldError]:
    """代码里直接构造的 Config（设置界面）类型可能不对：先查类型，免得后面的比较抛 TypeError。"""
    errs = [FieldError(_FIELD_NAMES[f], "type", getattr(c, f), typ.__name__)
            for f, typ in _FIELD_TYPES.items() if not _type_ok(getattr(c, f), typ)]
    if c.autostart is not None and not isinstance(c.autostart, bool):
        errs.append(FieldError("[general] autostart", "type", c.autostart, "bool"))
    for section in ("primary", "backup"):
        svc = getattr(c, section)
        if not isinstance(svc, ServiceConfig):
            errs.append(FieldError(f"[{section}]", "type", svc, "ServiceConfig"))
            continue
        for name, typ in _SERVICE_SCHEMA + [("api_key", str), ("key_enc", str)]:
            if not _type_ok(getattr(svc, name), typ):
                errs.append(FieldError(f"[{section}] {name}", "type", getattr(svc, name), typ.__name__))
    return errs


def api_key_ok(key: str) -> bool:
    """去掉首尾空白后只能是可打印的 ASCII（不含空格、零宽字符、中文等）。
    HTTP 头只能放 ASCII：带别的字符时 httpx 编码请求头会抛 UnicodeEncodeError，报错原文里就带着整把 key。"""
    return all(0x21 <= ord(ch) <= 0x7E for ch in key.strip())


def validate(c: Config) -> list[FieldError]:
    errs = _type_errors(c)
    if errs:
        return errs
    if c.language not in LANGUAGES:
        errs.append(FieldError("[general] language", "language", c.language))
    try:
        parse_hotkey(c.hotkey)
    except ValueError as e:
        errs.append(FieldError("[hotkey] capture", "hotkey", c.hotkey, str(e)))
    for name, attr in _MIN1:
        if getattr(c, attr) < 1:
            errs.append(FieldError(name, "min1", getattr(c, attr)))
    for name, attr in _POSITIVE:
        v = getattr(c, attr)
        if not math.isfinite(v) or v <= 0:
            errs.append(FieldError(name, "positive", v))
    for section, svc in (("primary", c.primary), ("backup", c.backup)):
        url = svc.base_url.strip()
        if url and not url.lower().startswith(("http://", "https://")):
            errs.append(FieldError(f"[{section}] base_url", "url", svc.base_url))
        if svc.thinking_style not in THINKING_STYLES:
            errs.append(FieldError(f"[{section}] thinking_style", "thinking_style", svc.thinking_style))
        if not api_key_ok(svc.api_key):
            errs.append(FieldError(f"[{section}] api_key", "key", "***"))  # value 不存 key
    return errs


def is_legacy(raw: dict) -> bool:
    return "primary" not in raw and "backup" not in raw and any(s in raw for s in _LEGACY_SECTIONS)


def _check_tables(raw: dict) -> None:
    """已知的节（新旧都算）必须是一节（dict）；写成 `vllm = 3` 之类的普通值就报 ConfigError。"""
    for section in SECTIONS + _LEGACY_SECTIONS:
        if section in raw and not isinstance(raw[section], dict):
            raise ConfigError(t("config.err.not_table", section=section, type=type(raw[section]).__name__))


def legacy_to_v2(raw: dict) -> dict:
    """旧格式 → 新格式的原始 dict（不改入参）。迁移（migrate.py）也用它。

    只接受旧格式（is_legacy 为真：有 [vllm]/[fallback]，没有 [primary]/[backup]），否则抛 ConfigError：
    - 已经是新格式，或新旧混写（同时有 [vllm] 和 [primary] 之类）：不转换、报错。load_config 读混写文件时
      以新格式为准（直接忽略 [vllm]/[fallback]），根本不会调到这里；直接调用者也不该拿新节去被旧节覆盖。
    - 已知的节写成了非表的值（`vllm = 3`、`timeouts = "x"`）：报错，不悄悄丢掉。"""
    _check_tables(raw)
    if not is_legacy(raw):
        raise ConfigError(t("config.err.not_legacy"))
    out = {k: dict(v) for k, v in raw.items() if k not in _LEGACY_SECTIONS and isinstance(v, dict)}
    out.setdefault("general", {"language": "zh"})
    vllm, fb = raw.get("vllm", {}), raw.get("fallback", {})
    key = vllm.get("api_key", "")
    out["primary"] = {"preset": "custom", "base_url": vllm.get("base_url", LEGACY_VLLM_URL) if key else "",
                      "api_key": key, "model": "", "thinking_off": True, "thinking_style": "chat_template_kwargs"}
    out["backup"] = {"preset": "custom", "base_url": fb.get("base_url", ""), "api_key": fb.get("api_key", ""),
                     "model": fb.get("model", ""), "thinking_off": False, "thinking_style": "chat_template_kwargs"}
    tm = out.setdefault("timeouts", {})
    tm.setdefault("connect", 2.0)
    tm.setdefault("first_token", 3.0)
    tm.setdefault("stall", 10.0)
    tm["backup_connect"] = tm["connect"]
    if "first_token" in fb:
        tm["backup_first_token"] = fb["first_token"]
    if "stall" in fb:
        tm["backup_stall"] = fb["stall"]
    return out


def _get(table: dict, section: str, key: str, typ, default):
    v = table.get(key, default)
    if typ is float and isinstance(v, int) and not isinstance(v, bool):
        v = float(v)
    # bool 是 int 的子类：数值项不许用 true/false 冒充；开关项（typ 就是 bool）只认 true/false
    if (typ is not bool and isinstance(v, bool)) or not isinstance(v, typ):
        # 密钥绝不能进报错（报错会弹窗、进日志）：只说类型，不回显内容
        shown = t("config.err.secret_shown", type=type(v).__name__) if key in _SECRET_KEYS else repr(v)
        raise ConfigError(t("config.err.type", section=section, key=key, type=typ.__name__, shown=shown))
    return v


def _service(table: dict, section: str) -> ServiceConfig:
    d = ServiceConfig()
    vals = {name: _get(table, section, name, typ, getattr(d, name)) for name, typ in _SERVICE_SCHEMA}
    enc = _get(table, section, "api_key_enc", str, "")
    plain = _get(table, section, "api_key", str, "")
    key, plaintext, unreadable = "", False, False
    if enc:
        try:
            key = secret.unprotect(enc)
        except secret.SecretError:
            unreadable = True
    elif plain:
        key, plaintext = plain, True
    return ServiceConfig(**vals, api_key=key, key_plaintext=plaintext, key_unreadable=unreadable, key_enc=enc)


def config_from_raw(raw: dict) -> Config:
    _check_tables(raw)
    legacy = is_legacy(raw)
    if legacy:
        raw = legacy_to_v2(raw)
    d = Config()
    values = {fname: _get(raw.get(section, {}), section, key, typ, getattr(d, fname))
              for section, key, fname, typ in _SCHEMA}
    auto = raw.get("general", {}).get("autostart")
    if auto is not None and not isinstance(auto, bool):
        raise ConfigError(t("config.err.type", section="general", key="autostart", type="bool", shown=repr(auto)))
    values["autostart"] = auto
    if legacy and "enabled" not in raw.get("tencent", {}):
        values["tencent_enabled"] = LEGACY_TENCENT_IF_UNSET
    values["primary"] = _service(raw.get("primary", {}), "primary")
    values["backup"] = _service(raw.get("backup", {}), "backup")
    c = Config(**values)
    errs = validate(c)
    if errs:
        raise ConfigError(describe(errs[0]))
    return c


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(t("config.err.missing", path=path))
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))  # 记事本另存可能带 BOM
    except UnicodeDecodeError as e:
        raise ConfigError(t("config.err.not_utf8", path=path)) from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(t("config.err.syntax", error=e)) from e
    return config_from_raw(raw)


def config_to_dict(c: Config) -> dict[str, dict]:
    def svc(s: ServiceConfig) -> dict:
        # 解不开、又没重新填的 key：原密文照写（换回原来的电脑/用户还能用），不清空
        keep = s.key_unreadable and not s.api_key and s.key_enc
        enc = s.key_enc if keep else secret.protect(s.api_key)
        return {"preset": s.preset, "base_url": s.base_url, "api_key_enc": enc,
                "model": s.model, "thinking_off": s.thinking_off, "thinking_style": s.thinking_style}

    return {
        "general": {"language": c.language, "autostart": c.autostart},
        "primary": svc(c.primary),
        "backup": svc(c.backup),
        "tencent": {"enabled": c.tencent_enabled, "connect": c.tencent_connect_timeout},
        "hotkey": {"capture": c.hotkey},
        "translate": {"domain_hint": c.domain_hint, "chunk_chars": c.chunk_chars,
                      "chunk_max_segments": c.chunk_max_segments, "max_concurrency": c.max_concurrency},
        "timeouts": {"connect": c.connect_timeout, "first_token": c.first_token_timeout,
                     "stall": c.stall_timeout, "breaker": c.breaker_seconds,
                     "backup_connect": c.backup_connect_timeout,
                     "backup_first_token": c.backup_first_token_timeout,
                     "backup_stall": c.backup_stall_timeout},
        "history": {"limit": c.history_limit, "save_images": c.history_save_images},
    }


def _comparable(c: Config) -> Config:
    def clean(s: ServiceConfig) -> ServiceConfig:
        return dataclasses.replace(s, key_plaintext=False, key_unreadable=False)
    return dataclasses.replace(c, primary=clean(c.primary), backup=clean(c.backup))


def write_config(path: Path, c: Config) -> None:
    """写临时文件 → 读回校验 → 旧文件复制为 .bak → 替换。任何一步失败抛 ConfigError，原文件不动。"""
    errs = validate(c)
    if errs:
        raise ConfigError(describe(errs[0]))
    try:
        text = dump_toml(config_to_dict(c), header=HEADER)
    except secret.SecretError as e:
        raise ConfigError(t("config.err.encrypt", error=e)) from e
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        back = load_config(tmp)
        if _comparable(back) != _comparable(c):
            raise ConfigError(t("config.err.readback"))
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)
    except ConfigError:
        _discard(tmp)
        raise
    except OSError as e:  # 磁盘满、没权限、文件被占用：同样不动原文件，统一报 ConfigError
        _discard(tmp)
        raise ConfigError(t("config.err.save", type=type(e).__name__, error=e)) from e


def _discard(tmp: Path) -> None:
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass  # 删不掉临时文件不该盖住真正的错误

"""自检：python -m shotrans.doctor，或托盘「自检…」窗口。逐项给出 ✅ / ❌。
检查项由 build_items() 给出，窗口和命令行共用，检查逻辑只写一份。"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import paths
from .config import ConfigError, load_config
from .hotkey import MOD_NOREPEAT, parse_hotkey
from .i18n import N_, t
from .render import FONT_EN, FONT_ZH
from .translate.factory import service_client

Check = tuple[str, Callable[[], tuple[bool, str]]]
FIX_LANG = "lang_settings"
LANG_SETTINGS_URL = "ms-settings:regionlanguage"
ITEM_LABEL_KEYS = {"ocr_en": N_("doctor.item.ocr_en"), "ocr_zh": N_("doctor.item.ocr_zh"),
                   "config": N_("doctor.item.config"), "primary": N_("doctor.item.primary"),
                   "backup": N_("doctor.item.backup"), "tencent": N_("doctor.item.tencent"),
                   "hotkey": N_("doctor.item.hotkey"), "fonts": N_("doctor.item.fonts")}


@dataclass(frozen=True)
class CheckItem:
    id: str
    label: str
    fn: Callable[[], tuple[bool, str]]
    fix: str | None
    depends_on: str | None = None  # 依赖的那一项（id）失败时，这一项不另给修复按钮：先修那一项


def item_label(item_id: str) -> str:
    key = ITEM_LABEL_KEYS.get(item_id)
    return t(key) if key else item_id


def run_one(fn) -> tuple[bool, str]:
    """跑一项检查：抛异常、返回的不是 (ok, msg) 二元组，都算失败并说明原因。"""
    try:
        res = fn()
    except Exception as e:
        return False, t("doctor.error", error=repr(e))
    if not isinstance(res, tuple) or len(res) != 2:
        return False, t("doctor.bad_result", value=repr(res)[:200])
    ok, msg = res
    return bool(ok), str(msg)


def run_checks(checks: list[Check]) -> tuple[list[str], bool]:
    lines, all_ok = [], True
    for name, fn in checks:
        ok, msg = run_one(fn)
        all_ok = all_ok and ok
        lines.append(t("doctor.line", mark="✅" if ok else "❌", name=name, msg=msg))
    return lines, all_ok


def check_ocr_lang(tag: str) -> tuple[bool, str]:
    from .ocr import available_languages

    if tag in available_languages():
        return True, t("doctor.ocr_ok")
    return False, t("doctor.ocr_missing", tag=tag)


def _translate_one(client) -> tuple[str, float, str]:
    from .translate.base import Job

    t0 = time.perf_counter()
    name = client.model_name()
    out: list[str] = []
    client.translate_stream([Job(1, "Hello, world.")], "en2zh", [], lambda i, txt: out.append(txt))
    return name, time.perf_counter() - t0, out[0]


def check_primary(cfg) -> tuple[bool, str]:
    if cfg is None:
        return False, t("doctor.cfg_missing")
    if cfg.primary.configured and cfg.primary.key_unreadable:
        return False, t("doctor.key_unreadable")
    client = service_client(cfg, "primary")
    if client is None:
        return True, t("doctor.primary_off")
    name, secs, text = _translate_one(client)
    return True, t("doctor.service_ok", model=name, secs=f"{secs:.1f}", text=text)


def check_backup(cfg) -> tuple[bool, str]:
    if cfg is None:
        return False, t("doctor.cfg_missing")
    if cfg.backup.configured and cfg.backup.key_unreadable:
        return False, t("doctor.key_unreadable")
    client = service_client(cfg, "backup")
    if client is None:
        return True, t("doctor.backup_off")
    name, secs, text = _translate_one(client)
    note = "" if cfg.backup.api_key else t("doctor.no_key_note")
    return True, t("doctor.service_ok", model=name, secs=f"{secs:.1f}", text=text) + note


def check_tencent(cfg) -> tuple[bool, str]:
    if cfg is None:
        return False, t("doctor.cfg_missing")
    if not cfg.tencent_enabled:
        return True, t("doctor.tencent_off")
    from .translate.base import TranslateError
    from .translate.tencent import TencentClient

    try:
        _, secs, text = _translate_one(TencentClient(cfg.tencent_connect_timeout))
    except TranslateError as e:
        return False, t("doctor.tencent_bad", kind=e.kind, detail=e.detail)
    return True, t("doctor.tencent_ok", secs=f"{secs:.1f}", text=text)


def no_service_configured(cfg) -> bool:
    return not cfg.primary.configured and not cfg.backup.configured and not cfg.tencent_enabled


def check_hotkey(cfg, own: str | None = None) -> tuple[bool, str]:
    spec = cfg.hotkey if cfg is not None else "Ctrl+Alt+Q"
    mods, vk = parse_hotkey(spec)
    if own is not None and parse_hotkey(own) == (mods, vk):
        return True, t("doctor.hotkey_own", hotkey=spec)  # 截图翻译自己注册着：不算被占用
    user32 = ctypes.windll.user32
    if user32.RegisterHotKey(None, 0xB0A2, mods | MOD_NOREPEAT, vk):
        user32.UnregisterHotKey(None, 0xB0A2)
        return True, t("doctor.hotkey_free", hotkey=spec)
    return False, t("doctor.hotkey_taken", hotkey=spec)


def check_fonts() -> tuple[bool, str]:
    missing = [p for p in (FONT_ZH, FONT_EN) if not Path(p).exists()]
    return (False, t("doctor.fonts_missing", fonts=missing)) if missing else (True, t("doctor.fonts_ok"))


def _config_file(home: Path, legacy: Path | None) -> Path:
    p = paths.config_path(home)
    if not p.exists() and legacy is not None and legacy.exists():
        return legacy  # 命令行 doctor 对旧的 config.local.toml 仍可用
    return p


def build_items(home: Path, legacy: Path | None = None, own_hotkey: str | None = None) -> list[CheckItem]:
    box: dict = {}

    def config_check() -> tuple[bool, str]:
        try:
            box["cfg"] = load_config(_config_file(home, legacy))
        except ConfigError as e:
            return False, str(e)
        if no_service_configured(box["cfg"]):
            return False, t("doctor.config_no_service")
        return True, t("doctor.config_ok")

    specs = [
        ("ocr_en", lambda: check_ocr_lang("en-US"), FIX_LANG, None),
        ("ocr_zh", lambda: check_ocr_lang("zh-Hans-CN"), FIX_LANG, None),
        ("config", config_check, "settings:service", None),
        ("primary", lambda: check_primary(box.get("cfg")), "settings:service", "config"),
        ("backup", lambda: check_backup(box.get("cfg")), "settings:service", "config"),
        ("tencent", lambda: check_tencent(box.get("cfg")), "settings:service", "config"),
        ("hotkey", lambda: check_hotkey(box.get("cfg"), own_hotkey), "settings:hotkey", None),  # 没配置时用默认键
        ("fonts", check_fonts, None, None),
    ]
    return [CheckItem(i, item_label(i), fn, fix, dep) for i, fn, fix, dep in specs]


def build_checks(home: Path, legacy: Path | None = None) -> list[Check]:
    return [(i.label, i.fn) for i in build_items(home, legacy)]


def mask_key(key: str) -> str:
    k = key.strip()
    if not k:
        return t("doctor.key_empty")
    prefix = "sk-" if k.startswith("sk-") else ""
    tail = k[-4:] if len(k) >= 12 else ""
    return f"{prefix}…{tail}"


def format_report(rows, cfg, version: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    out = [t("doctor.report_title"),
           t("doctor.report_meta", version=version, time=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))), ""]
    for label, ok, msg in rows:
        mark = "⏳" if ok is None else "✅" if ok else "❌"
        out.append(t("doctor.line", mark=mark, name=label, msg=msg))
    keys: list[str] = []
    if cfg is not None:
        out.append("")
        for role_key, svc in ((N_("role.primary"), cfg.primary), (N_("role.backup"), cfg.backup)):
            out.append(t("doctor.report_service", role=t(role_key), preset=svc.preset,
                         url=svc.base_url or t("doctor.url_empty"), model=svc.model or t("doctor.model_auto"),
                         key=mask_key(svc.api_key)))
            if len(svc.api_key.strip()) >= 6:
                keys.append(svc.api_key.strip())
    text = "\n".join(out)
    for k in keys:  # 检查结果里万一带出了 key（服务端回显、异常的 repr 转义写法），一并打码
        for form in {k, repr(k)[1:-1], ascii(k)[1:-1]}:
            text = text.replace(form, mask_key(k))
    return text


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK，打不出 ✅
    ap = argparse.ArgumentParser(description="截图翻译自检")
    ap.add_argument("--home", type=Path, default=paths.data_dir())
    args = ap.parse_args(argv)
    lines, ok = run_checks(build_checks(args.home, paths.legacy_config_path()))
    print("\n".join(lines))
    print(t("doctor.all_ok") if ok else t("doctor.has_problems"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

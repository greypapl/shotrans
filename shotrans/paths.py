"""数据放哪：默认 %APPDATA%\\截图翻译\\（config.toml、术语表.txt、history.json、history_images\\、logs\\、debug\\）；
环境变量 SHOTRANS_HOME 覆盖（测试、便携模式）。
程序自身的位置用 program_dir()（代码、开机自启的工作目录、随包的 licenses\\）。
旧版本把配置放在程序目录（config.local.toml），legacy_dir() 指向那里，SHOTRANS_LEGACY_DIR 可覆盖（测试用）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "截图翻译"
ENV_HOME = "SHOTRANS_HOME"
ENV_LEGACY = "SHOTRANS_LEGACY_DIR"


def data_dir() -> Path:
    env = os.environ.get(ENV_HOME, "").strip()
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA", "").strip()
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / APP_DIR_NAME


def ensure_data_dir() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _in(home: Path | None, name: str) -> Path:
    return (home if home is not None else data_dir()) / name


def config_path(home: Path | None = None) -> Path:
    return _in(home, "config.toml")


def glossary_path(home: Path | None = None) -> Path:
    return _in(home, "术语表.txt")


def history_path(home: Path | None = None) -> Path:
    return _in(home, "history.json")


def history_images_dir(home: Path | None = None) -> Path:
    """历史记录的截图（每条两张 PNG：原图、译文图），随记录一起删。"""
    return _in(home, "history_images")


def log_dir(home: Path | None = None) -> Path:
    return _in(home, "logs")


def debug_dir(home: Path | None = None) -> Path:
    return _in(home, "debug")


def program_dir() -> Path:
    """程序自身所在目录：打包版是 exe 所在目录，源码版是 截图翻译/。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def legacy_dir() -> Path:
    env = os.environ.get(ENV_LEGACY, "").strip()
    return Path(env) if env else program_dir()


def legacy_config_path() -> Path:
    return legacy_dir() / "config.local.toml"

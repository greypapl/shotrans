"""截图翻译：框选屏幕，译文原位覆盖。"""
from __future__ import annotations

import ctypes
import importlib.util
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)
__version__ = "1.0.0"


def _msvcp_candidates() -> list[Path]:
    """新版 VC++ 运行库的候选位置：打包版先找 _MEIPASS 下 PySide6 带的；再找 PySide6 包目录、Python 安装目录。"""
    out: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        out += [Path(meipass) / "PySide6" / "msvcp140.dll", Path(meipass) / "msvcp140.dll"]
    try:
        spec = importlib.util.find_spec("PySide6")  # 只找位置，不执行 PySide6 的 __init__
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.submodule_search_locations:
        out += [Path(p) / "msvcp140.dll" for p in spec.submodule_search_locations]
    out.append(Path(sys.base_prefix) / "msvcp140.dll")
    return out


def preload_msvcp140(candidates: list[Path] | None = None, loader=None) -> Path | None:
    """在任何 winrt 导入之前先加载新版 msvcp140.dll。

    根因（实测）：winrt 包自带 MSVCP140.dll 14.29，PySide6 需要 14.44；同名 DLL 进程里只认先加载的那份。
    先导入 winrt 再导入 Qt，Qt 就用上旧版，创建 QApplication 时 access violation。
    这里抢先把新版加载进来，之后谁再按名字要 msvcp140.dll 都拿到新版。
    失败只记日志，不影响启动（最坏情况退回原来的「先导入 Qt」要求）。"""
    if sys.platform != "win32":
        return None
    loader = loader or ctypes.WinDLL
    for p in candidates if candidates is not None else _msvcp_candidates():
        if not p.exists():
            continue
        try:
            loader(str(p))
        except OSError:
            log.warning("预加载 %s 失败", p, exc_info=True)
            continue
        return p
    log.warning("没有找到可预加载的新版 msvcp140.dll")
    return None


LOADED_MSVCP140 = preload_msvcp140()  # 自测（--selftest）报告用

"""开机自启：在当前用户的「启动」文件夹里放一个快捷方式，不碰注册表。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LINK_NAME = "截图翻译.lnk"
ENV_TRIAL = "SHOTRANS_TRIAL"  # 「用临时数据目录启动.bat」设的：试用模式
AUTOSTART_ARG = "--autostart"  # 开机自启快捷方式带的标记：这样启动不弹「已在后台运行」提示


def trial_mode() -> bool:
    """试用模式（环境变量 SHOTRANS_TRIAL 非空）下绝不建、删「启动」文件夹里的快捷方式：
    「启动」文件夹是系统级的，不跟着临时数据目录走。"""
    return bool(os.environ.get(ENV_TRIAL))


def startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _link(folder: Path | None) -> Path:
    return (folder or startup_dir()) / LINK_NAME


def is_enabled(folder: Path | None = None) -> bool:
    return _link(folder).exists()


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def enable(target: Path, args: str, workdir: Path, folder: Path | None = None) -> None:
    lnk = _link(folder)
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(lnk))});"
        f"$s.TargetPath={_ps_quote(str(target))};"
        f"$s.Arguments={_ps_quote(args)};"
        f"$s.WorkingDirectory={_ps_quote(str(workdir))};"
        "$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def read_link(folder: Path | None = None) -> tuple[str, str, str] | None:
    """读出现有快捷方式的（目标, 参数, 工作目录）；快捷方式不存在返回 None。读失败照常抛异常。"""
    lnk = _link(folder)
    if not lnk.exists():
        return None
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(lnk))});"
        "@($s.TargetPath,$s.Arguments,$s.WorkingDirectory) -join [char]10"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    text = out.stdout.decode("utf-8-sig").replace("\r", "").removesuffix("\n")
    target, args, workdir = (text + "\n\n").split("\n")[:3]
    return target, args, workdir


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def points_to(target: Path, args: str, workdir: Path, folder: Path | None = None) -> bool:
    """快捷方式存在，且目标、参数、工作目录都和给定的一致（路径按 Windows 规则不分大小写）。
    移动了程序文件夹、从源码版换成 exe 后，旧快捷方式还在但指向别处：这里返回 False。"""
    got = read_link(folder)
    if got is None:
        return False
    t, a, w = got
    return _same_path(t, target) and a == args and _same_path(w, workdir)


def launch_command() -> tuple[Path, str, Path]:
    """开机自启快捷方式的（目标, 参数, 工作目录）。打包版直接指向 exe；源码版用 pythonw -m shotrans。
    参数都带 --autostart：开机自启时不弹启动提示。旧版本建的快捷方式没有这个标记，points_to 比对不上，
    用户在设置里点「应用」/「保存」时就被重写。"""
    from . import paths
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return exe, AUTOSTART_ARG, exe.resolve().parent
    return Path(sys.executable).with_name("pythonw.exe"), f"-m shotrans {AUTOSTART_ARG}", paths.program_dir()


def disable(folder: Path | None = None) -> None:
    _link(folder).unlink(missing_ok=True)

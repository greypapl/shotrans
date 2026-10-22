import os
import subprocess
import sys
from pathlib import Path

from shotrans import autostart, paths
from shotrans.autostart import LINK_NAME, disable, enable, is_enabled


def test_enable_creates_link_and_disable_removes(tmp_path):
    assert not is_enabled(tmp_path)
    enable(Path(r"C:\Windows\System32\notepad.exe"), "-m shotrans", Path(r"C:\Windows"), tmp_path)
    assert (tmp_path / LINK_NAME).exists()
    assert is_enabled(tmp_path)
    disable(tmp_path)
    assert not is_enabled(tmp_path)


def test_disable_when_absent_is_fine(tmp_path):
    disable(tmp_path)
    assert not is_enabled(tmp_path)


def test_paths_with_quote_and_chinese(tmp_path):
    work = tmp_path / "带 '引号' 的目录"
    work.mkdir()
    enable(Path(r"C:\Windows\System32\notepad.exe"), "-m shotrans", work, tmp_path)
    assert is_enabled(tmp_path)


def read_link(lnk: Path) -> dict:
    """用 PowerShell 把快捷方式读回来（只读传入的路径，不碰真的启动文件夹）。"""
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK_PATH);"
        "@($s.TargetPath,$s.Arguments,$s.WorkingDirectory) -join [char]10"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True,
                         capture_output=True, env={**os.environ, "LNK_PATH": str(lnk)}).stdout.decode("utf-8")
    target, args, work = (out.replace("\r", "").removesuffix("\n") + "\n\n").split("\n")[:3]
    return {"target": target, "args": args, "work": work}


def test_link_contents_plain(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    enable(Path(r"C:\Windows\System32\notepad.exe"), "-m shotrans", work, tmp_path)
    got = read_link(tmp_path / LINK_NAME)
    assert got == {"target": r"C:\Windows\System32\notepad.exe", "args": "-m shotrans", "work": str(work)}


def test_link_contents_with_quote_and_chinese_workdir(tmp_path):
    work = tmp_path / "带 '引号' 的目录"
    work.mkdir()
    enable(Path(r"C:\Windows\System32\notepad.exe"), "-m shotrans", work, tmp_path)
    got = read_link(tmp_path / LINK_NAME)
    assert got["target"] == r"C:\Windows\System32\notepad.exe"
    assert got["args"] == "-m shotrans"
    assert got["work"] == str(work)


def test_launch_command_frozen_points_to_exe(monkeypatch, tmp_path):
    exe = tmp_path / "新建 文件夹" / "截图翻译.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert autostart.launch_command() == (exe, "--autostart", exe.parent.resolve())


def test_launch_command_source_uses_pythonw(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    target, args, workdir = autostart.launch_command()
    assert target == Path(sys.executable).with_name("pythonw.exe")
    assert args == "-m shotrans --autostart"  # 开机自启带标记：启动时不弹「已在后台运行」
    assert workdir == paths.program_dir()


# ---------- 读回快捷方式、判断是否指向本程序（真 COM，只在 tmp_path 里） ----------
NOTEPAD = Path(r"C:\Windows\System32\notepad.exe")


def test_read_link_missing_is_none(tmp_path):
    assert autostart.read_link(tmp_path) is None


def test_read_link_round_trip_chinese_and_quote(tmp_path):
    work = tmp_path / "新建 '文件夹'"
    work.mkdir()
    enable(NOTEPAD, "-m shotrans", work, tmp_path)
    assert autostart.read_link(tmp_path) == (str(NOTEPAD), "-m shotrans", str(work))


def test_points_to_same_command(tmp_path):
    enable(NOTEPAD, "-m shotrans", tmp_path, tmp_path)
    assert autostart.points_to(NOTEPAD, "-m shotrans", tmp_path, tmp_path) is True


def test_points_to_ignores_path_case(tmp_path):
    enable(NOTEPAD, "-m shotrans", tmp_path, tmp_path)
    upper_target = Path(str(NOTEPAD).upper())
    upper_work = Path(str(tmp_path).upper())
    assert autostart.points_to(upper_target, "-m shotrans", tmp_path, tmp_path) is True
    assert autostart.points_to(NOTEPAD, "-m shotrans", upper_work, tmp_path) is True


def test_points_to_missing_link_is_false(tmp_path):
    assert autostart.points_to(NOTEPAD, "-m shotrans", tmp_path, tmp_path) is False


def test_points_to_other_target_is_false(tmp_path):
    enable(NOTEPAD, "-m shotrans", tmp_path, tmp_path)
    assert autostart.points_to(Path(r"C:\Windows\System32\calc.exe"), "-m shotrans", tmp_path, tmp_path) is False


def test_points_to_other_args_is_false(tmp_path):
    enable(NOTEPAD, "-m shotrans", tmp_path, tmp_path)
    assert autostart.points_to(NOTEPAD, "", tmp_path, tmp_path) is False


def test_points_to_other_workdir_is_false(tmp_path):
    enable(NOTEPAD, "-m shotrans", tmp_path, tmp_path)
    assert autostart.points_to(NOTEPAD, "-m shotrans", tmp_path / "别处", tmp_path) is False


def test_trial_mode_follows_env(monkeypatch):
    monkeypatch.delenv("SHOTRANS_TRIAL", raising=False)
    assert autostart.trial_mode() is False
    monkeypatch.setenv("SHOTRANS_TRIAL", "")
    assert autostart.trial_mode() is False
    monkeypatch.setenv("SHOTRANS_TRIAL", "1")
    assert autostart.trial_mode() is True

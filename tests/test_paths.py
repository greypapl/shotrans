import os
import sys
from pathlib import Path

from shotrans import paths


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOTRANS_HOME", str(tmp_path / "便携"))
    assert paths.data_dir() == tmp_path / "便携"


def test_default_under_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("SHOTRANS_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert paths.data_dir() == tmp_path / "Roaming" / "截图翻译"


def test_blank_env_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOTRANS_HOME", "   ")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert paths.data_dir() == tmp_path / "Roaming" / "截图翻译"


def test_no_appdata_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("SHOTRANS_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.data_dir() == tmp_path / "AppData" / "Roaming" / "截图翻译"


def test_children(tmp_path):
    assert paths.config_path(tmp_path) == tmp_path / "config.toml"
    assert paths.glossary_path(tmp_path) == tmp_path / "术语表.txt"
    assert paths.history_path(tmp_path) == tmp_path / "history.json"
    assert paths.log_dir(tmp_path) == tmp_path / "logs"
    assert paths.debug_dir(tmp_path) == tmp_path / "debug"


def test_children_default_to_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOTRANS_HOME", str(tmp_path))
    assert paths.config_path() == tmp_path / "config.toml"
    assert paths.debug_dir() == tmp_path / "debug"


def test_data_dir_does_not_create(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOTRANS_HOME", str(tmp_path / "x"))
    paths.data_dir()
    assert not (tmp_path / "x").exists()


def test_ensure_creates(monkeypatch, tmp_path):
    monkeypatch.setenv("SHOTRANS_HOME", str(tmp_path / "a" / "截图翻译"))
    d = paths.ensure_data_dir()
    assert d == tmp_path / "a" / "截图翻译" and d.is_dir()


def test_program_dir_is_package_parent():
    assert (paths.program_dir() / "shotrans" / "paths.py").exists()


def test_legacy_dir_defaults_to_program_dir(monkeypatch):
    monkeypatch.delenv("SHOTRANS_LEGACY_DIR", raising=False)
    assert paths.legacy_dir() == paths.program_dir()
    assert paths.legacy_config_path() == paths.program_dir() / "config.local.toml"


def test_tests_never_touch_real_dirs():
    """conftest 的自动夹具在起作用：测试里数据目录、旧数据目录都不是真的。"""
    assert paths.data_dir() != Path(os.environ["APPDATA"]) / "截图翻译"
    assert paths.legacy_dir() != paths.program_dir()


def test_program_dir_frozen_is_exe_dir(monkeypatch, tmp_path):
    exe = tmp_path / "截图 翻译" / "截图翻译.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths.program_dir() == exe.parent.resolve()


def test_legacy_dir_frozen_is_exe_dir(monkeypatch, tmp_path):
    exe = tmp_path / "app" / "截图翻译.exe"
    monkeypatch.delenv("SHOTRANS_LEGACY_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths.legacy_config_path() == exe.parent.resolve() / "config.local.toml"
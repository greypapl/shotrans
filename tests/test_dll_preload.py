"""winrt 自带旧版 msvcp140.dll：shotrans 导入时必须抢先加载新版，否则「先 winrt 后 Qt」会崩。"""
import logging
import subprocess
import sys
from pathlib import Path

from shotrans import preload_msvcp140

ROOT = Path(__file__).resolve().parents[1]

WINRT_THEN_QT = (
    "import shotrans.ocr\n"
    "from winrt.windows.media.ocr import OcrEngine\n"
    "from PySide6.QtWidgets import QApplication\n"
    "app = QApplication([])\n"
    "print('ok')\n"
)


def test_winrt_imported_before_qt_does_not_crash():
    r = subprocess.run([sys.executable, "-c", WINRT_THEN_QT], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert r.stdout.strip() == "ok"


def test_preload_skips_missing_and_uses_first_existing(tmp_path):
    a, b = tmp_path / "a" / "msvcp140.dll", tmp_path / "b" / "msvcp140.dll"
    b.parent.mkdir()
    b.write_bytes(b"x")
    loaded = []
    assert preload_msvcp140([a, b], loader=loaded.append) == b
    assert loaded == [str(b)]


def test_preload_failure_only_logs_and_tries_next(tmp_path, caplog):
    a, b = tmp_path / "a.dll", tmp_path / "b.dll"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    loaded = []

    def loader(p):
        if p == str(a):
            raise OSError("坏的 DLL")
        loaded.append(p)

    with caplog.at_level(logging.WARNING):
        assert preload_msvcp140([a, b], loader=loader) == b
    assert loaded == [str(b)]
    assert any("预加载" in r.getMessage() for r in caplog.records)


def test_preload_nothing_found_returns_none_and_logs(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert preload_msvcp140([tmp_path / "none.dll"], loader=lambda p: None) is None
    assert any("没有找到" in r.getMessage() for r in caplog.records)


def test_candidates_frozen_look_in_meipass(monkeypatch, tmp_path):
    import shotrans
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    got = shotrans._msvcp_candidates()
    assert got[:2] == [tmp_path / "PySide6" / "msvcp140.dll", tmp_path / "msvcp140.dll"]


def test_loaded_path_is_recorded():
    import shotrans
    # 开发环境的 PySide6 目录里带着 msvcp140.dll，导入时一定能预加载成功（找不到才会是 None）
    assert shotrans.LOADED_MSVCP140 is not None
    assert shotrans.LOADED_MSVCP140.name.lower() == "msvcp140.dll"
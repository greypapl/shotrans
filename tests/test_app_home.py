"""托盘程序从数据目录读配置；没有 config.toml 时照样起来（等引导）；main 先做迁移。"""
import pytest
from PySide6.QtWidgets import QMessageBox, QWidget

from shotrans import app as app_mod
from shotrans import autostart
from shotrans.app import ShotTransApp, main
from shotrans.config import Config
from shotrans.hotkey import GlobalHotkey
from shotrans.migrate import MigrateResult
from shotrans.ui.notice import NoticePopup


class QuietRouter:
    glossary = None

    def vllm_down(self):
        return False


@pytest.fixture
def messages(monkeypatch):
    got = []
    monkeypatch.setattr(NoticePopup, "show_notice", lambda self, title, msg, *a, **k: got.append(msg))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    return got


@pytest.fixture
def make(qapp, messages):
    made = []

    def _make(home, **kw):
        # 没配置时启动会排队打开引导：用替身窗口，免得依赖还没写的窗口模块、或在测试里弹真窗口
        a = ShotTransApp(qapp, home, ocr_fn=lambda img: [], router=QuietRouter(),
                         window_factories={"wizard": lambda page: QWidget()},
                         ocr_languages=lambda: ["en-US", "zh-Hans-CN"], **kw)
        made.append(a)
        return a

    yield _make
    for a in made:
        a.shutdown()


def test_reads_config_toml_from_home(make, tmp_path):
    (tmp_path / "config.toml").write_text('[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n', encoding="utf-8")
    a = make(tmp_path)
    assert a.config.hotkey == "Ctrl+Alt+Shift+F10" and a.needs_setup is False
    assert a.home == tmp_path and a.log_file == tmp_path / "logs" / "shotrans.log"
    assert a.glossary_path == tmp_path / "术语表.txt" and a.glossary_path.exists()
    assert a.history.path == tmp_path / "history.json"


def test_without_config_starts_unconfigured(make, tmp_path):
    a = make(tmp_path / "新家")
    assert a.needs_setup is True and a.config == Config()
    assert not (tmp_path / "新家" / "config.toml").exists()
    assert (tmp_path / "新家" / "术语表.txt").exists()


def test_migrated_notice(make, tmp_path, messages):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    make(tmp_path, migration=MigrateResult("migrated"))
    assert len(messages) == 1 and "迁移" in messages[0] and str(tmp_path) in messages[0]


def test_migration_failed_notice(make, tmp_path, messages):
    make(tmp_path, migration=MigrateResult("failed", "配置文件格式错误：xx"))
    assert len(messages) == 1 and "迁移失败" in messages[0] and "配置文件格式错误：xx" in messages[0]


def test_main_migrates_then_passes_result(qapp, tmp_path, monkeypatch):
    legacy = tmp_path / "prog"
    legacy.mkdir()
    (legacy / "config.local.toml").write_text('[hotkey]\ncapture = "Ctrl+Alt+W"\n', encoding="utf-8")
    monkeypatch.setenv("SHOTRANS_LEGACY_DIR", str(legacy))
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    seen = []

    class Stub:
        def __init__(self, q, home, migration=None):
            seen.append((home, migration))

        def shutdown(self):
            pass

    monkeypatch.setattr(app_mod, "ShotTransApp", Stub)
    qapp.exec = lambda: 0
    try:
        assert main([], home=tmp_path / "home") == 0
    finally:
        del qapp.exec
    assert seen[0][0] == tmp_path / "home" and seen[0][1].status == "migrated"
    assert (tmp_path / "home" / "config.toml").exists()


def test_main_without_legacy_passes_none(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    seen = []

    class Stub:
        def __init__(self, q, home, migration=None):
            seen.append(migration)

        def shutdown(self):
            pass

    monkeypatch.setattr(app_mod, "ShotTransApp", Stub)
    qapp.exec = lambda: 0
    try:
        main([], home=tmp_path)
    finally:
        del qapp.exec
    assert seen == [None]


def test_main_logs_into_home(qapp, tmp_path, monkeypatch):
    dirs = []
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: dirs.append(d))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a: None))
    (tmp_path / "config.toml").write_text("[general\n", encoding="utf-8")  # 让它在建托盘前失败退出
    assert main([], home=tmp_path) == 1
    assert dirs == [tmp_path / "logs"]


def test_main_bad_config_toml_is_fatal(qapp, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda p, t, x: shown.append(x)))
    (tmp_path / "config.toml").write_text("[general\n", encoding="utf-8")
    assert main([], home=tmp_path) == 1
    assert len(shown) == 1 and "格式错误" in shown[0]


# ---------- 评审第 1 轮：气泡说明哪些没复制成功、旧文件在哪；查自启状态出错不拦启动 ----------
def test_migrated_with_failed_copies_notice(make, tmp_path, messages):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    make(tmp_path, migration=MigrateResult("migrated", "", ("history.json",), ("术语表.txt",), legacy="D:/旧程序"))
    assert len(messages) == 1 and "术语表.txt 没复制成功" in messages[0] and "D:/旧程序" in messages[0]


def test_migration_failed_notice_names_legacy(make, tmp_path, messages):
    make(tmp_path, migration=MigrateResult("failed", "配置文件格式错误：xx", legacy="D:/旧程序"))
    assert len(messages) == 1 and "旧文件在 D:/旧程序" in messages[0]


def test_main_autostart_error_falls_back_to_none(qapp, tmp_path, monkeypatch):
    from shotrans.config import load_config

    legacy = tmp_path / "prog"
    legacy.mkdir()
    (legacy / "config.local.toml").write_text('[hotkey]\ncapture = "Ctrl+Alt+W"\n', encoding="utf-8")
    monkeypatch.setenv("SHOTRANS_LEGACY_DIR", str(legacy))
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)

    def bad(folder=None):
        raise OSError("启动文件夹读不了")

    monkeypatch.setattr(autostart, "is_enabled", bad)
    fatal = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a: fatal.append(a)))  # 绝不弹真的模态框
    seen = []

    class Stub:
        def __init__(self, q, home, migration=None):
            seen.append(migration)

        def shutdown(self):
            pass

    monkeypatch.setattr(app_mod, "ShotTransApp", Stub)
    qapp.exec = lambda: 0
    try:
        assert main([], home=tmp_path / "home") == 0
    finally:
        del qapp.exec
    assert fatal == [] and seen[0].status == "migrated"
    assert load_config(tmp_path / "home" / "config.toml").autostart is None

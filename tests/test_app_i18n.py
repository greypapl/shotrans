"""托盘程序按配置的语言启动；迁移气泡、配置报错、带 {key} 占位符的文字跟着语言走。"""
from pathlib import Path

import pytest
from PySide6.QtWidgets import QSystemTrayIcon

from shotrans import autostart, i18n
from shotrans.app import ShotTransApp, migration_notice
from shotrans.config import ConfigError, load_config
from shotrans.hotkey import GlobalHotkey
from shotrans.i18n import t
from shotrans.migrate import MigrateResult
from shotrans.ui.notice import NoticePopup


class QuietRouter:
    glossary = None

    def vllm_down(self):
        return False


@pytest.fixture
def messages(monkeypatch):
    got = []
    monkeypatch.setattr(NoticePopup, "show_notice", lambda self, title, msg, *a, **k: got.append((title, msg)))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    return got


def test_app_starts_in_configured_language(qapp, tmp_path, messages):
    (tmp_path / "config.toml").write_text('[general]\nlanguage = "en"\n[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n',
                                          encoding="utf-8")
    a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router=QuietRouter())
    try:
        assert i18n.language() == "en"
        assert a.tray.toolTip() == "ShotTrans (Ctrl+Alt+Shift+F10)"
        assert [x.text() for x in a._menu.actions() if x.text()][-1] == "Quit"
    finally:
        a.shutdown()


def test_migrated_notice_english_with_failed_copy():
    i18n.set_language("en")
    text, icon = migration_notice(MigrateResult("migrated", "", (), ("history.json",), legacy="D:/old"), Path("H"))
    assert text == ("Your old settings were moved to H (the old file was left untouched). history.json could not "
                    "be copied. The old files are in D:/old; you can copy them to H by hand.")
    assert icon == QSystemTrayIcon.MessageIcon.Warning


def test_migrate_failed_notice_english_names_old_folder():
    i18n.set_language("en")
    text, _ = migration_notice(MigrateResult("failed", "R", legacy="D:/old"), Path("H"))
    assert text == ("Could not migrate the old settings: R. Please set up the service in the guide. "
                    "The old files are in D:/old, untouched.")


def test_config_error_follows_language(tmp_path):
    i18n.set_language("en")
    with pytest.raises(ConfigError, match=r"^Config file .* not found"):
        load_config(tmp_path / "nope.toml")


def test_placeholder_named_key_is_allowed():
    # t() 的第一个参数只能按位置传：文字里的 {key} 占位符不会和它撞名
    assert t("config.err.type", section="a", key="b", type="int", shown="'x'") == "配置项 [a] b 应该是 int，现在是 'x'"

"""启动最早的提示（已在运行、配置报错）按 config.toml 的 [general] language 显示；托盘「重新加载配置」的回执。"""
import uuid

import pytest
from PySide6.QtWidgets import QMessageBox

from shotrans import app as app_mod
from shotrans import autostart
from shotrans.app import ShotTransApp, main
from shotrans.config import Config
from shotrans.hotkey import GlobalHotkey
from shotrans.runtime import SingleInstance
from shotrans.ui.notice import NoticePopup


@pytest.fixture
def shown(monkeypatch):
    got = []
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda p, title, text: got.append(("critical", title, text))))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda p, title, text: got.append(("info", title, text))))
    monkeypatch.setattr(app_mod, "ShotTransApp", lambda *a, **k: pytest.fail("不该走到建托盘程序"))
    return got


def _second_launch(tmp_path):
    name = f"Local\\shotrans-test-{uuid.uuid4().hex}"
    holder = SingleInstance(name)
    assert holder.acquire()
    try:
        return main([], home=tmp_path, instance_name=name)
    finally:
        holder.release()


def test_already_running_in_english(qapp, tmp_path, shown):
    (tmp_path / "config.toml").write_text('[general]\nlanguage = "en"\n', encoding="utf-8")
    assert _second_launch(tmp_path) == 0
    assert shown == [("info", "ShotTrans",
                      "ShotTrans is already running. Press Ctrl+Alt+Q to capture and translate "
                      "(the blue tray icon at the bottom right, possibly hidden under ^).")]


def test_already_running_in_chinese_without_config(qapp, tmp_path, shown):
    assert _second_launch(tmp_path) == 0
    assert shown == [("info", "截图翻译", "截图翻译已在运行（右下角托盘里蓝色的「译」字图标，可能收在 ^ 里面）。")]


def test_already_running_chinese_when_config_says_zh(qapp, tmp_path, shown):
    (tmp_path / "config.toml").write_text('[general]\nlanguage = "zh"\n', encoding="utf-8")
    assert _second_launch(tmp_path) == 0
    assert shown[0][1] == "截图翻译"


@pytest.mark.parametrize("text", ['[general\nlanguage = "en"\n', '[general]\nlanguage = "fr"\n',
                                  'general = "en"\n', '[general]\nlanguage = 1\n'])
def test_unreadable_language_stays_chinese(qapp, tmp_path, shown, text):
    (tmp_path / "config.toml").write_text(text, encoding="utf-8")
    assert _second_launch(tmp_path) == 0
    assert shown[0][1] == "截图翻译"


def test_config_error_dialog_in_english(qapp, tmp_path, monkeypatch, shown):
    monkeypatch.setattr(app_mod, "ShotTransApp", ShotTransApp)  # 真的建：load_config 抛 ConfigError
    (tmp_path / "config.toml").write_text('[general]\nlanguage = "en"\n[hotkey]\ncapture = "Q"\n', encoding="utf-8")
    assert main([], home=tmp_path) == 1
    assert len(shown) == 1 and shown[0][:2] == ("critical", "ShotTrans")
    assert shown[0][2].startswith("Config item [hotkey] capture is invalid")


def test_config_error_dialog_in_chinese(qapp, tmp_path, monkeypatch, shown):
    monkeypatch.setattr(app_mod, "ShotTransApp", ShotTransApp)
    (tmp_path / "config.toml").write_text('[general]\nlanguage = "zh"\n[hotkey]\ncapture = "Q"\n', encoding="utf-8")
    assert main([], home=tmp_path) == 1
    assert len(shown) == 1 and shown[0][:2] == ("critical", "截图翻译")
    assert shown[0][2].startswith("配置项 [hotkey] capture 写得不对")


# ---------- 托盘「重新加载配置」的回执 ----------
class QuietRouter:
    glossary = None

    def vllm_down(self):
        return False


@pytest.fixture
def tray_app(qapp, tmp_path, monkeypatch):
    msgs = []
    monkeypatch.setattr(NoticePopup, "show_notice", lambda self, title, msg, *a, **k: msgs.append(msg))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    (tmp_path / "config.toml").write_text('[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n', encoding="utf-8")
    a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router=QuietRouter(),
                     router_factory=lambda c, g: QuietRouter(), window_factories={},
                     ocr_languages=lambda: ["en-US", "zh-Hans-CN"])
    a.msgs = msgs
    yield a
    a.shutdown()


def _reload_action(a):
    return next(x for x in a._menu.actions() if x.text() == "重新加载配置")


def test_menu_reload_success_confirms(tray_app):
    _reload_action(tray_app).trigger()
    assert tray_app.msgs == ["已重新加载配置。"]


def test_menu_reload_read_failure_no_confirmation(tray_app, tmp_path):
    (tmp_path / "config.toml").write_text("[hotkey\n", encoding="utf-8")
    _reload_action(tray_app).trigger()
    assert len(tray_app.msgs) == 1 and tray_app.msgs[0].startswith("重新加载配置失败")


def test_menu_reload_partial_failure_no_confirmation(tray_app, monkeypatch):
    def boom(c, g):
        raise RuntimeError("坏了")

    monkeypatch.setattr(tray_app, "_router_factory", boom)
    _reload_action(tray_app).trigger()
    assert tray_app.msgs == ["以下设置没有生效：翻译服务"]

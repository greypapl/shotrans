"""托盘程序用默认工厂真的能打开五种窗口（T8～T11 的窗口都在、构造签名对得上）。"""
import pytest

from shotrans import autostart
from shotrans.app import ShotTransApp
from shotrans.hotkey import GlobalHotkey
from shotrans.ui.notice import NoticePopup


class QuietRouter:
    glossary = None

    def vllm_down(self):
        return False


@pytest.mark.parametrize("kind,cls", [("settings", "SettingsWindow"), ("doctor", "DoctorWindow"),
                                      ("history", "HistoryWindow"), ("about", "AboutWindow"),
                                      ("wizard", "Wizard")])
def test_default_factories_open_real_windows(qapp, qtbot, tmp_path, monkeypatch, kind, cls):
    monkeypatch.setattr(NoticePopup, "show_notice", lambda *a, **k: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    (tmp_path / "config.toml").write_text('[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n[tencent]\nenabled = false\n',
                                          encoding="utf-8")
    a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router=QuietRouter(),
                     ocr_languages=lambda: ["en-US", "zh-Hans-CN"])
    try:
        w = a.open_window(kind)
        assert type(w).__name__ == cls and w.isVisible()
        assert a.open_window(kind) is w
    finally:
        a.shutdown()

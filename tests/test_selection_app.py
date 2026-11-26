from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPoint, Qt

from shotrans.app import ShotTransApp
from shotrans.config import Config, load_config
from shotrans.selection import ForegroundInfo
from shotrans.ui import selection_controller
from shotrans.ui.select_icon import SelectIcon
from tests.test_app_clip import R


def test_tray_updates_only_select_and_syncs_settings(qapp, tmp_path, monkeypatch):
    starts, stops = [], []
    monkeypatch.setattr(selection_controller.MouseHook, "install", lambda _: not starts.append(True))
    monkeypatch.setattr(selection_controller.MouseHook, "uninstall", lambda _: stops.append(True))
    app = ShotTransApp(qapp, tmp_path, router=R(), ocr_languages=lambda: ["en-US", "zh-Hans-CN"])
    try:
        settings = app.open_window("settings", "hotkey")
        settings.domain.setText("unsaved domain")
        def unexpected_apply(_):
            raise AssertionError("full apply")

        with monkeypatch.context() as scoped:
            scoped.setattr(app, "apply_config", unexpected_apply)
            assert starts == [True]
            app._actions["select"].trigger()
            app.exclude_selection_app("Test.exe")
            app.exclude_selection_app("TEST.EXE")
        assert app.needs_setup
        assert not settings.select_enabled.isChecked()
        assert settings.select_exclude.toPlainText() == "Test.exe"
        assert settings.domain.text() == "unsaved domain"
        assert stops
        saved = load_config(tmp_path / "config.toml")
        assert not saved.select_enabled
        assert saved.select_exclude == ["Test.exe"]
        app.reload_config()
        assert app.needs_setup
    finally:
        app.shutdown()


def test_select_icon_does_not_accept_focus_and_menu_routes_actions(qtbot):
    icon = SelectIcon(lambda _: None)
    qtbot.addWidget(icon)
    assert icon.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert icon.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    exclusions, switches = [], []
    icon.excluded.connect(exclusions.append)
    icon.disabled.connect(lambda: switches.append(False))
    icon.offer(QPoint(50, 50), "reader.exe")
    icon._open_menu()
    icon._menu.actions()[0].trigger()
    assert exclusions == ["reader.exe"]
    icon.offer(QPoint(50, 50), "reader.exe")
    icon._open_menu()
    icon._menu.actions()[1].trigger()
    assert switches == [False]


def test_icon_timer_restarts_after_hover(qtbot, monkeypatch):
    from shotrans.ui import select_icon

    monkeypatch.setattr(select_icon, "HIDE_MS", 80)
    icon = SelectIcon(lambda _: None)
    qtbot.addWidget(icon)
    icon.offer(QPoint(50, 50), "reader.exe")
    icon.enterEvent(None)
    qtbot.wait(120)
    assert icon.isVisible()
    icon.leaveEvent(None)
    qtbot.waitUntil(icon.isHidden, timeout=500)


@pytest.mark.parametrize("click_target", ["editor", "terminal", "other_window"])
def test_selection_revalidates_after_snapshot_before_shared_translation(qtbot, click_target):
    calls = []

    class App(QObject):
        needs_setup = False
        config = Config()
        capturer = SimpleNamespace(busy=False)

        def translate_text(self, text, cursor):
            calls.append(("translation", text, cursor))

        def exclude_selection_app(self, process):
            pass

    info = ForegroundInfo(41, 5, "code.exe", focus=6)
    current = info
    windows = SimpleNamespace(double_click_time=lambda: 500, foreground=lambda: current,
                              root_at=lambda *_: 41, logical_point=QPoint, no_activate=lambda _: None)

    class Probe:
        def cancel(self):
            pass

        def check(self, process, pid, done, *, hwnd):
            if calls == ["snapshot"]:
                calls.append("terminal check")
                done(click_target == "terminal")
            else:
                done(False)

    class Grabber:
        busy = False

        def prepare(self):
            calls.append("snapshot")
            return True

        def start(self, pid, can_copy, done):
            assert can_copy()
            calls.append("send")
            done("Selected words")

        def cancel_preparation(self):
            pass

        def shutdown(self):
            pass

    app = App()
    controller = selection_controller.SelectionController(app, windows=windows, probe=Probe(), grabber=Grabber())
    try:
        controller.set_enabled(True)
        controller._on_mouse("left_down", 45, 50, 1)
        controller._on_mouse("left_up", 65, 50, 30)
        icon = controller._icon
        assert icon is not None and icon.isVisible()
        assert not calls
        pos = icon.pos()
        if click_target == "other_window":
            current = ForegroundInfo(42, 5, "code.exe", focus=7)
        qtbot.mouseClick(icon, Qt.MouseButton.LeftButton)
        expected = {
            "editor": ["snapshot", "terminal check", "send", ("translation", "Selected words", pos)],
            "terminal": ["snapshot", "terminal check"],
            "other_window": [],
        }
        assert calls == expected[click_target]
    finally:
        controller.shutdown()

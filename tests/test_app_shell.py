"""新托盘菜单、可点的气泡、窗口单实例、未配置时打开引导。"""
import pytest
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QSystemTrayIcon, QWidget

from shotrans import autostart
from shotrans import app as app_mod
from shotrans.app import ShotTransApp
from shotrans.capture import Capturer
from shotrans.config import Config
from shotrans.hotkey import GlobalHotkey
from shotrans.models import Box, OcrLine
from shotrans.overlay import OverlayWindow
from shotrans.ui.notice import NoticePopup
from shotrans.ui.port import AppPort
from tests.ui_fakes import FakeApp

CFG = '[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n'
KINDS = ("settings", "doctor", "history", "about", "wizard")


class QuietRouter:
    glossary = None

    def vllm_down(self):
        return False

    def translate(self, segments, direction, on_result, cancel=None):
        pass


class Win(QWidget):
    def __init__(self, kind, page):
        super().__init__()
        self.kind, self.pages = kind, [page]

    def show_page(self, page):
        self.pages.append(page)


@pytest.fixture
def env(qapp, monkeypatch):
    got = {"messages": [], "opened": [], "captures": 0}
    monkeypatch.setattr(NoticePopup, "show_notice",
                        lambda self, title, msg, *a, **k: got["messages"].append(msg))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    monkeypatch.setattr(Capturer, "start", lambda self: got.__setitem__("captures", got["captures"] + 1))
    monkeypatch.setattr(OverlayWindow, "_close_if_inactive", lambda self: None)
    return got


@pytest.fixture
def make(qapp, env, tmp_path):
    made = []

    def factories():
        def f(kind):
            def build(page):
                env["opened"].append((kind, page))
                return Win(kind, page)
            return build
        return {k: f(k) for k in KINDS}

    def _make(config=CFG, langs=("en-US", "zh-Hans-CN"), **kw):
        if config is not None:
            (tmp_path / "config.toml").write_text(config, encoding="utf-8")
        a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [OcrLine("Hi", Box(1, 1, 20, 10))],
                         router=QuietRouter(), window_factories=factories(),
                         ocr_languages=lambda: list(langs), **kw)
        made.append(a)
        return a

    yield _make
    for a in made:
        a.shutdown()


def _texts(a):
    return [x.text() for x in a._menu.actions() if not x.isSeparator()]


def _action(a, text):
    return next(x for x in a._menu.actions() if x.text() == text)


def test_menu_texts_and_separators(make):
    a = make()
    assert _texts(a) == ["截图翻译（Ctrl+Alt+Shift+F10）", "历史记录…", "设置…", "自检…", "重新加载配置", "关于…", "退出"]
    assert [x.isSeparator() for x in a._menu.actions()] == [False, False, True, False, False, False, False, True, False]


@pytest.mark.parametrize("text,kind", [("历史记录…", "history"), ("设置…", "settings"), ("自检…", "doctor"),
                                       ("关于…", "about")])
def test_menu_opens_windows(make, env, text, kind):
    a = make()
    _action(a, text).trigger()
    assert env["opened"] == [(kind, None)]


def test_menu_reload_calls_reload(make, monkeypatch):
    a = make()
    calls = []
    monkeypatch.setattr(a, "reload_config", lambda: calls.append(1) or True)
    _action(a, "重新加载配置").trigger()
    assert calls == [1]


def test_window_opened_once(make, env):
    a = make()
    w1 = a.open_window("history")
    w2 = a.open_window("history")
    assert w1 is w2 and env["opened"] == [("history", None)]


def test_page_reaches_existing_settings(make):
    a = make()
    w = a.open_window("settings")
    a.open_window("settings", "hotkey")
    assert w.pages == [None, "hotkey"]


def test_configured_hotkey_starts_capture(make, env):
    a = make()
    a.hotkey.callback()
    assert env["captures"] == 1 and env["opened"] == []


def test_unconfigured_hotkey_opens_wizard(qtbot, make, env):
    a = make(config=None)
    qtbot.waitUntil(lambda: env["opened"] == [("wizard", None)], timeout=3000)  # 启动时自动打开一次
    a.windows.get("wizard").close()
    a.hotkey.callback()
    assert env["captures"] == 0 and env["opened"][-1] == ("wizard", None)


def test_unconfigured_tray_click_opens_wizard(qtbot, make, env):
    a = make(config=None)
    a.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert env["captures"] == 0 and ("wizard", None) in env["opened"]


def test_configured_startup_does_not_open_wizard(qtbot, make, env):
    make()
    qtbot.wait(50)
    assert env["opened"] == []


def test_wizard_closed_unfinished_notifies_and_click_reopens(qtbot, make, env):
    a = make(config=None)
    qtbot.waitUntil(lambda: bool(env["opened"]), timeout=3000)
    a.windows.get("wizard").close()
    env["messages"].clear()
    a.on_wizard_closed(False)
    assert env["messages"] == ["还没配置翻译服务，点这里打开引导。"]
    a.notice.clicked.emit()
    assert env["opened"][-1] == ("wizard", None)


def test_wizard_closed_after_save_no_notice(make, env):
    a = make(config=None)
    a.save_config(Config(hotkey="Ctrl+Alt+Shift+F10"))
    env["messages"].clear()
    a.on_wizard_closed(True)
    assert env["messages"] == []


def test_hotkey_taken_bubble_opens_hotkey_page(make, env, monkeypatch):
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: False)
    a = make()
    assert "占用" in env["messages"][0]
    a.notice.clicked.emit()
    assert env["opened"] == [("settings", "hotkey")]


def test_internal_error_bubble_opens_doctor(make, env):
    a = make()
    a.error_notice.emit()
    a.notice.clicked.emit()
    assert env["opened"] == [("doctor", None)]


def test_bubble_target_used_once(make, env):
    a = make()
    a.error_notice.emit()
    a.notice.clicked.emit()
    a.windows.get("doctor").close()  # 关掉再点：否则第二次只是激活已开的窗口，看不出目标有没有清掉（变异验证时补的）
    a.notice.clicked.emit()
    assert env["opened"] == [("doctor", None)]


def test_notice_without_target_click_does_nothing(make, env):
    a = make()
    a.notify("随便说一句")
    a.notice.clicked.emit()
    assert env["opened"] == []


def test_no_ocr_language_bubble_opens_doctor(make, env):
    a = make(langs=("ja",))
    assert env["messages"] == ["没有安装 OCR 语言包（英文、简体中文都没有），无法识别文字。点这里打开自检。"]
    a.notice.clicked.emit()
    assert env["opened"] == [("doctor", None)]


def test_one_ocr_language_no_bubble(make, env):
    make(langs=("en-US",))
    assert env["messages"] == []


def test_overlay_gets_open_window(make):
    a = make()
    a.on_captured(Image.new("RGB", (100, 50), "white"), QRect(300, 300, 100, 50))
    assert a.overlays[0]._open_window == a.open_window


def test_shutdown_closes_windows(make):
    a = make()
    w = a.open_window("about")
    a.shutdown()
    assert not w.isVisible()


def test_app_and_fake_implement_port(tmp_path):
    names = [n for n in dir(AppPort) if not n.startswith("_")] + list(AppPort.__annotations__)
    fake = FakeApp(tmp_path)
    for n in names:
        assert hasattr(fake, n), n
    for n in [n for n in dir(AppPort) if not n.startswith("_")]:
        assert hasattr(ShotTransApp, n), n


def test_default_factories_cover_all_kinds(qapp, tmp_path, env):
    a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router=QuietRouter(),
                     ocr_languages=lambda: ["en-US", "zh-Hans-CN"])
    try:
        assert set(a.windows.kinds()) == set(KINDS)
    finally:
        a.needs_setup = False  # 别让启动时排队的「打开引导」在后面的测试里去导入还没写的窗口
        a.shutdown()


# ---------- 其余启动气泡的点击目标（实施时补充） ----------
def test_unreadable_key_bubble_opens_service_page(make, env):
    a = make(config='[primary]\nbase_url = "http://x/v1"\napi_key_enc = "AAAAAAAA"\n' + CFG)
    assert len(env["messages"]) == 1 and "无法解密" in env["messages"][0]
    a.notice.clicked.emit()
    assert env["opened"] == [("settings", "service")]


def test_migration_failed_bubble_opens_wizard(make, env):
    from shotrans.migrate import MigrateResult
    a = make(migration=MigrateResult("failed", "坏了", legacy="D:/旧"))
    assert len(env["messages"]) == 1 and "迁移失败" in env["messages"][0]
    a.notice.clicked.emit()
    assert env["opened"] == [("wizard", None)]


def test_migrated_bubble_click_does_nothing(make, env):
    from shotrans.migrate import MigrateResult
    a = make(migration=MigrateResult("migrated"))
    assert len(env["messages"]) == 1 and "迁移" in env["messages"][0]
    a.notice.clicked.emit()
    assert env["opened"] == []


def test_queued_wizard_not_opened_after_shutdown(qtbot, make, env):
    a = make(config=None)  # 启动时排队了「打开引导」
    a.shutdown()           # 还没轮到就关了程序
    qtbot.wait(50)
    assert env["opened"] == []


def test_wizard_closed_during_shutdown_no_notice(qtbot, make, env):
    """退出程序时引导被一并关掉：不弹「还没配置翻译服务」。"""
    a = make(config=None)
    qtbot.waitUntil(lambda: bool(env["opened"]), timeout=3000)
    a.shutdown()
    env["messages"].clear()
    a.on_wizard_closed(False)
    assert env["messages"] == []

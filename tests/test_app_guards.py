"""托盘主程序的守卫专属测试：备用层接线、快捷键占用提示、自启开关、关闭清理、配置错误退出。"""
import logging

import pytest
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

from shotrans import app as app_mod
from shotrans import autostart
from shotrans.app import ShotTransApp, main, make_icon, setup_logging
from shotrans.capture import Capturer
from shotrans.hotkey import GlobalHotkey
from shotrans.models import Box, OcrLine
from shotrans.overlay import OverlayWindow
from shotrans.ui.notice import NoticePopup

BASE = """
[vllm]
base_url = "http://127.0.0.1:9/v1"
api_key = "k"
[hotkey]
capture = "Ctrl+Alt+Shift+F10"
"""

FALLBACK = """
[fallback]
base_url = "http://127.0.0.1:9/fb/v1"
api_key = "fk"
model = "fb-model"
first_token = 7
stall = 15
"""

IMG = Image.new("RGB", (100, 50), "white")


def grab(qtbot, a, rect):
    """截一次图。不等后台线程：浮窗的工作线程不碰任何 Qt 对象，窗口随时关都不会崩（见 test_overlay_stress）。"""
    a.on_captured(IMG, rect)


class QuietRouter:
    def __init__(self):
        self.glossary = None

    def vllm_down(self):
        return False

    def translate(self, segments, direction, on_result, cancel=None):
        pass

    def retranslate_one(self, seg, direction, current):
        return None


@pytest.fixture
def messages(monkeypatch):
    """记录提醒（提示小窗）的文字，不真的弹。"""
    got = []
    monkeypatch.setattr(NoticePopup, "show_notice", lambda self, title, msg, *a, **k: got.append((title, msg)))
    return got


@pytest.fixture
def autostart_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(autostart, "enable", lambda *a, **k: calls.append(("enable", a)))
    monkeypatch.setattr(autostart, "disable", lambda *a, **k: calls.append(("disable", a)))
    return calls


@pytest.fixture
def make_app(qapp, tmp_path, messages, autostart_calls, monkeypatch):
    # 「失焦就关」是浮窗自己的行为（overlay 的测试覆盖）；这里关掉，免得测试机上别的窗口抢焦点导致偶发
    monkeypatch.setattr(OverlayWindow, "_close_if_inactive", lambda self: None)
    made = []

    def _make(config=BASE, router="quiet", register=None):
        (tmp_path / "config.toml").write_text(config, encoding="utf-8")
        a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [OcrLine("Hi", Box(1, 1, 20, 10))],
                         router=QuietRouter() if router == "quiet" else router,
                         ocr_languages=lambda: ["en-US", "zh-Hans-CN"])
        made.append(a)
        return a

    yield _make
    for a in made:
        a.shutdown()


# ---------- 备用层接线（不联网：只构造客户端，不发请求） ----------
def test_router_built_with_fallback_when_configured(make_app):
    a = make_app(BASE + FALLBACK, router=None)
    r = a.router
    assert r.vllm.name == "vllm" and r.vllm.base_url == "http://127.0.0.1:9/v1"
    assert r.vllm.thinking_off is True
    assert r.fallback is not None
    assert r.fallback.name == "fallback"
    assert r.fallback.model_name() == "fb-model"
    assert r.fallback.base_url == "http://127.0.0.1:9/fb/v1"
    assert r.fallback.api_key == "fk"
    assert r.fallback.thinking_off is False
    assert r.fallback.first_token_timeout == 7.0 and r.fallback.stall_timeout == 15.0
    assert r.breaker_seconds == 60.0


def test_router_has_no_fallback_when_not_configured(make_app):
    a = make_app(BASE, router=None)
    assert a.router.fallback is None
    assert a.router.vllm.name == "vllm"


def test_built_router_gets_glossary_from_file(make_app, tmp_path):
    (tmp_path / "术语表.txt").write_text("PHQ-8\n", encoding="utf-8")
    a = make_app(BASE, router=None)
    assert a.router.glossary.keep == ("PHQ-8",)


def test_given_router_is_used_as_is(make_app):
    r = QuietRouter()
    assert make_app(BASE, router=r).router is r


# ---------- 快捷键 ----------
def test_hotkey_registration_failure_notifies(monkeypatch, make_app, messages, caplog):
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: False)
    with caplog.at_level(logging.WARNING):
        make_app()
    assert len(messages) == 1
    assert "Ctrl+Alt+Shift+F10" in messages[0][1] and "占用" in messages[0][1]
    assert any("注册失败" in r.message for r in caplog.records)


def test_hotkey_registered_ok_no_message(monkeypatch, make_app, messages):
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    make_app()
    assert messages == []


def test_hotkey_triggers_capture(monkeypatch, make_app):
    calls = []
    monkeypatch.setattr(Capturer, "start", lambda self: calls.append(1))
    a = make_app()
    a.hotkey.callback()
    assert calls == [1]


# ---------- 没填 vLLM key ----------
NO_KEY = """
[vllm]
base_url = "http://127.0.0.1:9/v1"
api_key = ""
[hotkey]
capture = "Ctrl+Alt+Shift+F10"
"""


# 改动说明（2026-10-10）：没填 vLLM key 现在是允许的配置，原先每次启动都弹「vLLM 未配置 key」的托盘气泡，
# 改为只记一行日志（自检照样报「未配置（不使用 vLLM）」）。下面两条原先钉的是气泡，改成钉「不弹、只记日志」。
def test_empty_vllm_key_logs_at_startup_without_popup(monkeypatch, make_app, messages, caplog):
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    with caplog.at_level(logging.INFO):
        make_app(NO_KEY)
    assert messages == []
    assert any("主服务" in r.getMessage() and "不使用" in r.getMessage() for r in caplog.records)


def test_empty_vllm_key_no_popup(monkeypatch, make_app, messages):
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    make_app(NO_KEY)
    assert not any("vLLM" in m[1] for m in messages)


def test_filled_vllm_key_no_key_warning(monkeypatch, make_app, messages):
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    make_app(BASE)
    assert not any("key" in m[1] for m in messages)


# ---------- 托盘 ----------
def _action(a, text_part):
    return next(x for x in a._menu.actions() if text_part in x.text())


def test_tray_left_click_starts_capture_other_reasons_do_not(monkeypatch, make_app):
    calls = []
    monkeypatch.setattr(Capturer, "start", lambda self: calls.append(1))
    a = make_app()
    a.tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)
    assert calls == []
    a.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert calls == [1]


def test_menu_capture_action_starts_capture(monkeypatch, make_app):
    calls = []
    monkeypatch.setattr(Capturer, "start", lambda self: calls.append(1))
    a = make_app()
    _action(a, "截图翻译（").trigger()
    assert calls == [1]


# ---------- 截图时术语表读取失败 ----------
def test_captured_with_gbk_glossary_keeps_old_notifies_and_still_opens(qtbot, make_app, messages, tmp_path, caplog):
    a = make_app()
    a.router.glossary = "旧术语表"
    (tmp_path / "术语表.txt").write_bytes("抑郁 => 忧郁\n".encode("gbk"))
    messages.clear()
    with caplog.at_level(logging.ERROR):
        grab(qtbot, a, QRect(300, 300, 100, 50))
    assert a.router.glossary == "旧术语表"
    assert len(a.overlays) == 1
    assert len(messages) == 1 and "术语表" in messages[0][1]
    assert any("术语表" in r.message and r.exc_info for r in caplog.records)


def test_startup_with_gbk_glossary_uses_empty_and_notifies(monkeypatch, make_app, messages, tmp_path, caplog):
    from shotrans.glossary import Glossary

    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    (tmp_path / "术语表.txt").write_bytes("抑郁 => 忧郁\n".encode("gbk"))
    with caplog.at_level(logging.ERROR):
        a = make_app(BASE, router=None)  # 不抛：启动不能被术语表拦住
    assert a.router.glossary == Glossary()
    assert len(messages) == 1 and "术语表" in messages[0][1] and "UTF-8" in messages[0][1]
    assert any("术语表" in r.getMessage() and r.exc_info for r in caplog.records)


def test_startup_with_good_glossary_no_glossary_warning(monkeypatch, make_app, messages, tmp_path):
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    (tmp_path / "术语表.txt").write_text("PHQ-8\n", encoding="utf-8")
    make_app(BASE, router=None)
    assert messages == []


# ---------- 浮窗列表 ----------
def test_closing_overlay_removes_it_from_list(qtbot, make_app):
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    w = a.overlays[0]
    w.close()
    assert a.overlays == []


def test_on_overlay_closed_removes_and_tolerates_unknown(qtbot, make_app):
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    keep = a.overlays[0]
    a._on_overlay_closed(object())  # 不在列表里：不报错、不动别的
    assert a.overlays == [keep]
    a._on_overlay_closed(keep)
    assert a.overlays == []
    keep.close()  # 已从列表摘掉，shutdown 够不着它，自己收尾


def test_unpinned_closed_pinned_kept_on_new_capture(qtbot, make_app):
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    pinned = a.overlays[0]
    pinned.pinned = True
    grab(qtbot, a, QRect(320, 320, 100, 50))
    loose = a.overlays[1]
    grab(qtbot, a, QRect(340, 340, 100, 50))
    assert pinned in a.overlays and loose not in a.overlays and len(a.overlays) == 2


def test_hide_before_grab_hides_unpinned_too(qtbot, make_app):
    """上一张没钉住的浮窗不能被冻进下一张截图。"""
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    w = a.overlays[0]
    assert not w.pinned and w.isVisible()
    a._hide_overlays()
    assert not w.isVisible()
    assert w in a.overlays  # 只是藏起来，没关


def test_cancel_restores_hidden_unpinned(qtbot, make_app):
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    w = a.overlays[0]
    a._hide_overlays()
    a.capturer._finish(None, None)  # 框选取消：after_grab 后发 cancelled
    assert w.isVisible()
    assert a._hidden == []


def test_cancel_does_not_show_overlay_that_was_already_hidden(qtbot, make_app):
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    w = a.overlays[0]
    w.hide()
    a._hide_overlays()
    a.capturer._finish(None, None)
    assert not w.isVisible()


def test_capture_after_hide_closes_unpinned(qtbot, make_app):
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    w = a.overlays[0]
    a._hide_overlays()
    a.capturer._after()  # 框成：先放回钉住的，再发 captured
    grab(qtbot, a, QRect(320, 320, 100, 50))
    assert w not in a.overlays and len(a.overlays) == 1
    assert a._hidden == []


def test_cancel_after_overlay_closed_does_not_touch_it(qtbot, make_app):
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    w = a.overlays[0]
    a._hide_overlays()
    w.close()  # 截屏期间被关掉（已从列表摘除）
    a._restore_after_cancel()  # 不能去 show 一个已关的窗口
    assert a.overlays == []
    assert not w.isVisible()  # 还没被 deleteLater 回收，能查询


def test_show_does_not_show_hidden_unpinned(qtbot, make_app):
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    w = a.overlays[0]
    w.hide()
    a._show_pinned()
    assert not w.isVisible()


def test_capturer_callbacks_wired_to_hide_show(qtbot, make_app):
    a = make_app()
    # 改动说明：截屏前的回调从 _hide_overlays 换成 _before_grab（先预热腾讯连接再藏浮窗），藏浮窗见下一条
    assert a.capturer._before == a._before_grab
    # 截屏后的回调从 _show_pinned 换成 _after_grab（放回钉住的浮窗，再弹截图期间推迟的提醒）
    assert a.capturer._after == a._after_grab


def test_after_grab_shows_pinned(qtbot, make_app, monkeypatch):
    a = make_app()
    calls = []
    monkeypatch.setattr(a, "_show_pinned", lambda: calls.append(1))
    a._after_grab()
    assert calls == [1]


def test_before_grab_hides_overlays(qtbot, make_app):
    a = make_app()
    grab(qtbot, a, QRect(300, 300, 100, 50))
    w = a.overlays[0]
    assert w.isVisible()
    a._before_grab()
    assert not w.isVisible()


# ---------- 关闭清理 ----------
def test_shutdown_unregisters_closes_everything(qtbot, make_app, qapp):
    a = make_app()
    removed = []
    real = qapp.removeNativeEventFilter
    qapp.removeNativeEventFilter = lambda f: (removed.append(f), real(f))[1]
    try:
        a.hotkey.registered = True  # 假装注册成功，验证 shutdown 会走 unregister
        calls = []
        a.hotkey.unregister = lambda: calls.append("unreg")
        grab(qtbot, a, QRect(300, 300, 100, 50))
        a.overlays[0].pinned = True
        grab(qtbot, a, QRect(320, 320, 100, 50))
        assert len(a.overlays) == 2
        a.shutdown()
    finally:
        del qapp.removeNativeEventFilter
    assert calls == ["unreg"]
    assert removed == [a.hotkey]
    assert a.overlays == []  # 钉住的也要关掉
    assert not a.tray.isVisible()


# ---------- 日志与图标 ----------
def test_setup_logging_writes_file(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    old_level = root.level
    try:
        setup_logging(tmp_path / "logs")
        added = [h for h in root.handlers if h not in before]
        logging.getLogger("shotrans.test").info("你好日志")
        for h in added:
            h.flush()
        text = (tmp_path / "logs" / "shotrans.log").read_text(encoding="utf-8")
        assert "你好日志" in text and "INFO" in text
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        root.setLevel(old_level)


def test_make_icon_not_null(qapp):
    icon = make_icon()
    assert not icon.isNull()
    assert not icon.pixmap(64, 64).isNull()


# ---------- main ----------
def test_main_config_error_shows_message_and_returns_1(qapp, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda parent, title, text: shown.append((title, text))))
    (tmp_path / "config.toml").write_text("[general\n", encoding="utf-8")
    assert main([], home=tmp_path) == 1
    assert len(shown) == 1
    assert shown[0][0] == "截图翻译" and "格式错误" in shown[0][1]


def test_main_unexpected_startup_error_logged_and_shown(qapp, tmp_path, monkeypatch, caplog):
    shown = []
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda parent, title, text: shown.append((title, text))))

    def boom(q, home, **kw):
        raise RuntimeError("意外炸了")

    monkeypatch.setattr(app_mod, "ShotTransApp", boom)
    with caplog.at_level(logging.ERROR):
        assert main([], home=tmp_path) == 1
    assert len(shown) == 1 and shown[0][0] == "截图翻译" and str(tmp_path / "logs" / "shotrans.log") in shown[0][1]
    assert "意外炸了" not in shown[0][1]  # 对用户只给通用说明
    assert any(r.exc_info and "意外炸了" in str(r.exc_info[1]) for r in caplog.records)


def test_main_config_error_is_logged(qapp, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a: None))
    (tmp_path / "config.toml").write_text("[general\n", encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        assert main([], home=tmp_path) == 1
    assert any("格式错误" in r.getMessage() for r in caplog.records)


def test_main_starts_event_loop_and_connects_shutdown(qapp, tmp_path, monkeypatch, messages, autostart_calls):
    (tmp_path / "config.toml").write_text(BASE, encoding="utf-8")
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    created = []

    class Stub:
        def __init__(self, q, home, **kw):
            created.append(self)
            self.home = home
            self.shut = 0

        def shutdown(self):
            self.shut += 1

    monkeypatch.setattr(app_mod, "ShotTransApp", Stub)
    execs = []
    qapp.exec = lambda: execs.append(1) or 7
    try:
        assert main([], home=tmp_path) == 7
        assert execs == [1] and created[0].home == tmp_path
        qapp.aboutToQuit.emit()
        assert created[0].shut == 1
    finally:
        del qapp.exec

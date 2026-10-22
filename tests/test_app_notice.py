"""托盘程序的提醒改走自己画的提示小窗（Windows 关了通知 / 勿扰时托盘气泡会被吞掉）：
notify 弹小窗、不再调托盘气泡；点小窗打开目标窗口；× 只关；新提醒替换旧的；后台线程的报错提醒落在界面线程。"""
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

from shotrans import autostart
from shotrans.app import ShotTransApp
from shotrans.hotkey import GlobalHotkey
from shotrans.ui.notice import NoticePopup

CFG = '[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n'
INFO = QSystemTrayIcon.MessageIcon.Information
WARN = QSystemTrayIcon.MessageIcon.Warning


class QuietRouter:
    glossary = None

    def vllm_down(self):
        return False


@pytest.fixture
def env(qapp, monkeypatch):
    got = {"balloons": [], "opened": [], "threads": []}
    monkeypatch.setattr(QSystemTrayIcon, "showMessage", lambda self, *a, **k: got["balloons"].append(a))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    real = NoticePopup.show_notice

    def spy(self, *a, **k):
        got["threads"].append(threading.get_ident())
        return real(self, *a, **k)

    monkeypatch.setattr(NoticePopup, "show_notice", spy)
    return got


@pytest.fixture
def make(qapp, env, tmp_path):
    made = []

    def _make():
        (tmp_path / "config.toml").write_text(CFG, encoding="utf-8")

        def factory(kind):
            def build(page):
                env["opened"].append((kind, page))
                return QWidget()
            return build

        a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router=QuietRouter(),
                         router_factory=lambda c, g: QuietRouter(),
                         window_factories={k: factory(k) for k in ("settings", "doctor", "history", "about", "wizard")},
                         ocr_languages=lambda: ["en-US", "zh-Hans-CN"])
        made.append(a)
        return a

    yield _make
    for a in made:
        a.shutdown()


def _popups():
    return [w for w in QApplication.topLevelWidgets() if isinstance(w, NoticePopup) and w.isVisible()]


def test_notify_shows_popup_not_tray_balloon(make, env):
    a = make()
    a.notify("快捷键被占用了", target="settings:hotkey")
    assert a.notice.isVisible()
    assert a.notice.title_label.text() == "截图翻译"
    assert a.notice.message == "快捷键被占用了"
    assert a.notice.kind == "warning"  # notify 默认是警告
    assert env["balloons"] == []  # 不再走托盘气泡（不重复提醒）


def test_info_icon_passes_through(make):
    a = make()
    a.notify("已重新加载配置。", icon=INFO)
    assert a.notice.kind == "info"


def test_click_popup_opens_target_and_closes(qtbot, make, env):
    a = make()
    a.notify("快捷键被占用了", target="settings:hotkey")
    qtbot.mouseClick(a.notice.text_label, Qt.MouseButton.LeftButton)
    assert env["opened"] == [("settings", "hotkey")]
    assert not a.notice.isVisible()


def test_close_button_does_not_open_target(qtbot, make, env):
    a = make()
    a.notify("快捷键被占用了", target="settings:hotkey")
    qtbot.mouseClick(a.notice.close_button, Qt.MouseButton.LeftButton)
    assert env["opened"] == [] and not a.notice.isVisible()


def test_popup_without_target_is_not_hand_cursor(make):
    a = make()
    a.notify("随便说一句")
    assert a.notice.cursor().shape() == Qt.CursorShape.ArrowCursor
    a.notify("点这里", target="doctor")
    assert a.notice.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_second_notice_replaces_first(qtbot, make, env):
    a = make()
    a.notify("第一条", target="settings:hotkey")
    a.notify("第二条", target="doctor", icon=INFO)
    assert len(_popups()) == 1
    assert a.notice.message == "第二条" and a.notice.kind == "info"
    qtbot.mouseClick(a.notice.text_label, Qt.MouseButton.LeftButton)
    assert env["opened"] == [("doctor", None)]  # 点的是最新那条的目标


def test_announce_start_shows_popup_once(make, env):
    a = make()
    assert not a.notice.isVisible()  # 构造时没有提醒
    a.announce_start()
    assert a.notice.isVisible() and a.notice.kind == "info" and "后台运行" in a.notice.message


def test_announce_start_skipped_after_startup_notice(make, env):
    a = make()
    a.notify("启动时的别的提醒", target="doctor")
    a.announce_start()
    assert a.notice.message == "启动时的别的提醒"


def test_error_notice_from_worker_thread_lands_on_ui_thread(qtbot, make, env):
    a = make()
    t = threading.Thread(target=a.error_notice.emit)
    t.start()
    t.join()
    qtbot.waitUntil(lambda: a.notice.isVisible(), timeout=3000)
    assert env["threads"] == [threading.main_thread().ident]
    assert "程序内部出错了" in a.notice.message


# ---------- 截图流程：小窗不能被冻进截图，也不能盖在框选层上（评审 I1、M1） ----------
@pytest.fixture
def no_grab(monkeypatch):
    """真走 Capturer.start / _finish，只是不真截屏、不建全屏框选窗口。"""
    from shotrans.capture import Capturer
    monkeypatch.setattr(Capturer, "_grab", lambda self: None)


def test_notice_hidden_before_grab(make, no_grab):
    a = make()
    a.announce_start()  # 「已在后台运行，按 … 截图翻译」：用户读完马上按快捷键是最常见的路径
    assert a.notice.isVisible()
    a.capturer.start()  # 真的 before_grab 钩子
    assert not a.notice.isVisible()


def test_hidden_notice_not_shown_again_after_capture(make, no_grab):
    a = make()
    a.notify("旧提醒", target="doctor")
    a.capturer.start()
    a.capturer._finish(None, None)  # 没框成，取消
    assert not a.notice.isVisible()  # 截之前的提醒不放回来：本来就是短暂的


def test_notice_during_selection_deferred_until_cancel(make, env, no_grab):
    a = make()
    a.capturer.start()
    a.error_notice.emit()  # 框选进行中来了提醒（实际多半是后台线程的报错）
    assert not a.notice.isVisible()  # 不能盖在全屏框选层上面
    a.capturer._finish(None, None)
    assert a.notice.isVisible() and "程序内部出错了" in a.notice.message
    a.notice.clicked.emit()
    assert env["opened"] == [("doctor", None)]  # 推迟后点击目标仍然对


def test_notice_during_selection_deferred_until_captured(make, no_grab, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPixmap
    monkeypatch.setattr(ShotTransApp, "on_captured", lambda self, image, rect: None)  # 不建真的浮窗
    a = make()
    a.capturer.start()
    a.notify("第一条", target="settings:hotkey")
    a.notify("第二条", target="doctor", icon=INFO)
    assert not a.notice.isVisible()

    class Win:  # 框选窗口的替身：_finish 只用 pixmap 和 geometry()
        pixmap = QPixmap(50, 50)

        @staticmethod
        def geometry():
            return QRect(0, 0, 50, 50)

    a.capturer._finish(Win(), QRect(0, 0, 10, 10))  # 框成了
    assert a.notice.isVisible() and a.notice.message == "第二条" and a.notice.kind == "info"  # 最新一条为准
    assert a._notice_target == "doctor"


def test_deferred_notice_shown_only_once(make, no_grab):
    a = make()
    a.capturer.start()
    a.notify("框选中来的提醒")
    a.capturer._finish(None, None)
    assert a.notice.isVisible()
    a.capturer.start()  # 下一次截图：先藏掉
    a.capturer._finish(None, None)
    assert not a.notice.isVisible()  # 已经弹过的推迟提醒不会再弹一次


def test_deferred_notice_still_counts_for_startup_rule(make, no_grab):
    a = make()
    a.capturer.start()
    a.notify("启动时的别的提醒", target="doctor")
    a.announce_start()  # 已经有提醒了（虽然推迟着）：不弹启动提示
    a.capturer._finish(None, None)
    assert a.notice.message == "启动时的别的提醒"


def test_shutdown_hides_popup(make):
    a = make()
    a.notify("x")
    a.shutdown()
    assert not a.notice.isVisible()

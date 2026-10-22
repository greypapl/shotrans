"""程序自己画的提示小窗（替代托盘气泡：Windows 关了通知 / 勿扰时托盘气泡会被系统直接吞掉）。"""
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QGuiApplication
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from shotrans import i18n
from shotrans.ui.notice import NoticePopup

INFO = QSystemTrayIcon.MessageIcon.Information
WARN = QSystemTrayIcon.MessageIcon.Warning
SHORT = 150  # 测试用的自动消失时间（毫秒）


@pytest.fixture
def make(qtbot):
    made = []

    def _make(timeout_ms=SHORT):
        p = NoticePopup(timeout_ms=timeout_ms)
        made.append(p)
        return p

    yield _make
    for p in made:
        p.close()


def test_shows_title_text_and_kind(make):
    p = make(timeout_ms=60_000)
    p.show_notice("截图翻译", "快捷键被占用了", WARN)
    assert p.isVisible()
    assert p.title_label.text() == "截图翻译"
    assert p.text_label.text() == "快捷键被占用了"
    assert p.kind == "warning"
    p.show_notice("截图翻译", "已重新加载配置。", INFO)
    assert p.kind == "info"


def test_critical_counts_as_warning(make):
    p = make(timeout_ms=60_000)
    p.show_notice("t", "x", QSystemTrayIcon.MessageIcon.Critical)
    assert p.kind == "warning"


def test_warning_looks_different_from_info(make):
    p = make(timeout_ms=60_000)
    p.show_notice("t", "x", INFO)
    info_style, info_badge = p.accent.styleSheet(), p.warn_badge.isVisible()
    p.show_notice("t", "x", WARN)
    assert p.accent.styleSheet() != info_style
    assert info_badge is False and p.warn_badge.isVisible() is True


def test_positioned_bottom_right_of_primary_available_geometry(make):
    p = make(timeout_ms=60_000)
    p.show_notice("截图翻译", "一句话", INFO)
    g = QGuiApplication.primaryScreen().availableGeometry()
    f = p.frameGeometry()
    assert g.contains(f), (g, f)  # 不压在任务栏上、不出屏幕
    assert g.right() - f.right() <= 48 and g.bottom() - f.bottom() <= 48  # 贴着右下角


def test_long_text_wraps_within_width(make):
    p = make(timeout_ms=60_000)
    p.show_notice("截图翻译", "很长的一句提醒，" * 40 + r" C:\Users\someone\AppData\Roaming\截图翻译\logs\shotrans.log", WARN)
    assert p.width() <= 420
    assert p.text_label.height() > p.title_label.height() * 2  # 折成了多行，不是一行横穿屏幕
    g = QGuiApplication.primaryScreen().availableGeometry()
    assert g.contains(p.frameGeometry())


def test_long_windows_path_wraps_instead_of_being_cut(make):
    # 反斜杠在 Unicode 断行规则里不是断点：不处理的话整条日志路径是一个「词」，右半截被小窗切掉看不见
    p = make(timeout_ms=60_000)
    p.show_notice("ShotTrans", "See " + "C:\\Users\\someone\\AppData\\Roaming\\ShotTrans\\logs\\" * 2 + "shotrans.log",
                  WARN)
    assert p.width() <= 420
    assert p.text_label.minimumSizeHint().width() <= p.text_label.width()  # 最长的不可断片段也放得下
    assert p.message == ("See " + "C:\\Users\\someone\\AppData\\Roaming\\ShotTrans\\logs\\" * 2 + "shotrans.log")


def test_pathological_unbreakable_text_keeps_popup_on_screen(make):
    p = make(timeout_ms=60_000)
    p.show_notice("ShotTrans", "x" * 400, WARN)
    assert p.width() <= 420
    assert QGuiApplication.primaryScreen().availableGeometry().contains(p.frameGeometry())


def test_auto_hides_after_timeout(qtbot, make):
    p = make()
    p.show_notice("t", "x", INFO)
    assert p.isVisible()
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)


def test_default_timeout_is_about_six_seconds(qtbot):
    p = NoticePopup()
    try:
        p.show_notice("t", "x", INFO)
        qtbot.wait(300)
        assert p.isVisible()
        assert 5000 <= p.timeout_ms <= 8000
    finally:
        p.close()


def _enter(p):
    QApplication.sendEvent(p, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))


def _leave(p):
    QApplication.sendEvent(p, QEvent(QEvent.Type.Leave))


def test_hover_pauses_and_leave_resumes(qtbot, make):
    p = make()
    p.show_notice("t", "x", INFO)
    _enter(p)
    qtbot.wait(SHORT * 3)
    assert p.isVisible()  # 鼠标停在上面：不消失
    _leave(p)
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)


def test_new_notice_while_hovered_stays_until_leave(qtbot, make):
    # 鼠标正停在小窗上时来了新一条：不能在光标底下自己消失，移开后才重新计时
    p = make()
    p.show_notice("t", "第一条", INFO)
    _enter(p)
    p.show_notice("t", "第二条", WARN)
    qtbot.wait(SHORT * 3)
    assert p.isVisible() and p.text_label.text() == "第二条"
    _leave(p)
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)


def test_new_notice_after_mouse_left_restarts_timer(qtbot, make):
    # 鼠标进来又走了，再来新一条：照常从头计时（不能还当成「悬停中」而沿用离开时那次计时）
    p = make(timeout_ms=400)
    p.show_notice("t", "第一条", INFO)
    _enter(p)
    _leave(p)
    qtbot.wait(250)
    p.show_notice("t", "第二条", INFO)
    qtbot.wait(250)  # 从离开算已 500ms：没重新计时的话已经藏了
    assert p.isVisible()
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)


def test_hover_state_cleared_when_hidden(qtbot, make):
    # 悬停时被点掉（藏起来）：下一条照常自动消失，不会因为残留的「悬停中」永远不走
    p = make()
    p.show_notice("t", "第一条", INFO)
    _enter(p)
    p.hide()
    p.show_notice("t", "第二条", INFO)
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)


def test_second_notice_replaces_text_and_restarts_timer(qtbot, make):
    p = make(timeout_ms=400)
    p.show_notice("t", "第一条", WARN)
    qtbot.wait(250)
    p.show_notice("t", "第二条", INFO)
    qtbot.wait(250)  # 从第一条算已过 500ms：计时器没重启的话早藏了
    assert p.isVisible() and p.text_label.text() == "第二条" and p.kind == "info"
    qtbot.waitUntil(lambda: not p.isVisible(), timeout=2000)


def test_click_body_emits_clicked_and_hides(qtbot, make):
    p = make(timeout_ms=60_000)
    got = []
    p.clicked.connect(lambda: got.append(1))
    p.show_notice("t", "点我", WARN)
    qtbot.mouseClick(p.text_label, Qt.MouseButton.LeftButton)  # 点在文字上：事件冒泡到小窗本身
    assert got == [1] and not p.isVisible()


def test_click_title_area_also_counts(qtbot, make):
    p = make(timeout_ms=60_000)
    got = []
    p.clicked.connect(lambda: got.append(1))
    p.show_notice("t", "点我", WARN)
    qtbot.mouseClick(p, Qt.MouseButton.LeftButton, pos=p.rect().center())
    assert got == [1] and not p.isVisible()


def test_press_then_drag_out_does_not_open(qtbot, make):
    p = make(timeout_ms=60_000)
    got = []
    p.clicked.connect(lambda: got.append(1))
    p.show_notice("t", "x", WARN)
    qtbot.mousePress(p, Qt.MouseButton.LeftButton, pos=p.rect().center())
    qtbot.mouseRelease(p, Qt.MouseButton.LeftButton, pos=p.rect().bottomRight() + QPoint(30, 30))
    assert got == [] and p.isVisible()


def test_no_screen_does_not_crash(make, monkeypatch):
    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: None))
    p = make(timeout_ms=60_000)
    p.show_notice("t", "x", WARN)
    assert p.isVisible() and p.text_label.text() == "x"


def test_right_click_does_not_open(qtbot, make):
    p = make(timeout_ms=60_000)
    got = []
    p.clicked.connect(lambda: got.append(1))
    p.show_notice("t", "x", WARN)
    qtbot.mouseClick(p.text_label, Qt.MouseButton.RightButton)
    assert got == [] and p.isVisible()


def test_close_button_hides_without_click(qtbot, make):
    p = make(timeout_ms=60_000)
    got = []
    p.clicked.connect(lambda: got.append(1))
    p.show_notice("t", "x", WARN)
    assert p.isVisible()
    qtbot.mouseClick(p.close_button, Qt.MouseButton.LeftButton)
    assert got == [] and not p.isVisible()


def test_close_button_tooltip_follows_language(make):
    p = make(timeout_ms=60_000)
    p.show_notice("t", "x", INFO)
    assert p.close_button.toolTip() == "关闭"
    i18n.set_language("en")
    p.show_notice("t", "x", INFO)
    assert p.close_button.toolTip() == "Close"


def test_cursor_hand_only_when_clickable(make):
    p = make(timeout_ms=60_000)
    p.show_notice("t", "x", INFO, clickable=True)
    assert p.cursor().shape() == Qt.CursorShape.PointingHandCursor
    p.show_notice("t", "x", INFO, clickable=False)
    assert p.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_does_not_take_focus_or_show_in_taskbar(make):
    p = make(timeout_ms=60_000)
    f = p.windowFlags()
    assert f & Qt.WindowType.Tool == Qt.WindowType.Tool  # 不进任务栏、Alt+Tab
    assert f & Qt.WindowType.FramelessWindowHint
    assert f & Qt.WindowType.WindowStaysOnTopHint
    assert f & Qt.WindowType.WindowDoesNotAcceptFocus
    assert p.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert p.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert p.close_button.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_shown_notice_is_not_active_window(qtbot, make):
    p = make(timeout_ms=60_000)
    p.show_notice("t", "x", WARN)
    qtbot.wait(100)
    assert p.isVisible() and not p.isActiveWindow()

"""进程级守卫：未捕获异常进日志 + 限频提醒；单实例。"""
import logging
import sys
import threading
import uuid

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from shotrans import app as app_mod
from shotrans import autostart
from shotrans.runtime import ErrorReporter, SingleInstance
from shotrans.ui.notice import NoticePopup

BASE = """
[vllm]
base_url = "http://127.0.0.1:9/v1"
api_key = "k"
[hotkey]
capture = "Ctrl+Alt+Shift+F10"
"""


@pytest.fixture
def reporter():
    """装上钩子，测试结束一定卸掉（不能污染别的测试）。"""
    got = []
    r = ErrorReporter(notify=lambda: got.append(1))
    r.got = got
    r.install()
    yield r
    r.uninstall()


# ---------- 未捕获异常 ----------
@pytest.mark.qt_no_exception_capture
def test_exception_in_qt_slot_is_logged(qtbot, reporter, caplog):
    def boom():
        raise RuntimeError("槽函数里炸了")

    with caplog.at_level(logging.ERROR):
        QTimer.singleShot(0, boom)
        qtbot.waitUntil(lambda: any(r.exc_info for r in caplog.records), timeout=2000)
    rec = next(r for r in caplog.records if r.exc_info)
    assert "未捕获的异常" in rec.getMessage()
    assert isinstance(rec.exc_info[1], RuntimeError) and "槽函数里炸了" in str(rec.exc_info[1])
    assert reporter.got == [1]


def test_exception_in_thread_is_logged(reporter, caplog):
    def boom():
        raise ValueError("线程里炸了")

    with caplog.at_level(logging.ERROR):
        t = threading.Thread(target=boom, name="测试线程")
        t.start()
        t.join()
    rec = next(r for r in caplog.records if r.exc_info)
    assert "测试线程" in rec.getMessage()
    assert isinstance(rec.exc_info[1], ValueError)
    assert reporter.got == [1]


def test_thread_system_exit_is_ignored(reporter, caplog):
    with caplog.at_level(logging.ERROR):
        t = threading.Thread(target=sys.exit)
        t.start()
        t.join()
    assert not caplog.records and reporter.got == []


def test_notify_rate_limited():
    got, now = [], [0.0]
    r = ErrorReporter(notify=lambda: got.append(now[0]), min_interval=30.0, clock=lambda: now[0])
    err = (RuntimeError, RuntimeError("x"), None)
    r.handle(*err)
    now[0] = 29.9
    r.handle(*err)
    assert got == [0.0]
    now[0] = 30.0
    r.handle(*err)
    assert got == [0.0, 30.0]


def test_notify_failure_is_logged_not_raised(caplog):
    def bad():
        raise OSError("托盘没了")

    r = ErrorReporter(notify=bad)
    with caplog.at_level(logging.ERROR):
        r.handle(RuntimeError, RuntimeError("x"), None)  # 不抛即通过
    assert any("提醒用户出错时又出错" in x.getMessage() for x in caplog.records)


def test_without_notify_only_logs(caplog):
    r = ErrorReporter()
    with caplog.at_level(logging.ERROR):
        r.handle(RuntimeError, RuntimeError("x"), None)
    assert any("未捕获的异常" in x.getMessage() for x in caplog.records)


def test_keyboard_interrupt_goes_to_default_hook(monkeypatch, caplog):
    seen = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: seen.append(a[0]))
    r = ErrorReporter(notify=lambda: seen.append("notify"))
    with caplog.at_level(logging.ERROR):
        r.sys_hook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert seen == [KeyboardInterrupt] and not caplog.records


def test_install_and_uninstall_restore_hooks():
    old_sys, old_thr = sys.excepthook, threading.excepthook
    r = ErrorReporter()
    r.install()
    try:
        assert sys.excepthook == r.sys_hook and threading.excepthook == r.thread_hook
    finally:
        r.uninstall()
    assert sys.excepthook is old_sys and threading.excepthook is old_thr


# ---------- 单实例 ----------
def _name():
    return f"Local\\shotrans-test-{uuid.uuid4().hex}"


def test_second_instance_refused_until_first_released():
    name = _name()
    first, second = SingleInstance(name), SingleInstance(name)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
        assert second.handle is None
    finally:
        first.release()
    third = SingleInstance(name)
    assert third.acquire() is True
    third.release()


def test_different_names_do_not_block_each_other():
    a, b = SingleInstance(_name()), SingleInstance(_name())
    try:
        assert a.acquire() and b.acquire()
    finally:
        a.release()
        b.release()


@pytest.fixture
def quiet_main(monkeypatch):
    shown = []
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda p, t, x: shown.append(("critical", x))))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda p, t, x: shown.append(("info", x))))
    return shown


def test_main_second_launch_exits_quietly(qapp, tmp_path, monkeypatch, quiet_main):
    name = _name()
    holder = SingleInstance(name)
    assert holder.acquire()
    created = []
    monkeypatch.setattr(app_mod, "ShotTransApp", lambda q, home, **kw: created.append(1))
    try:
        assert app_mod.main([], home=tmp_path, instance_name=name) == 0
    finally:
        holder.release()
    assert created == []  # 不能起第二个托盘程序
    assert quiet_main == [("info", quiet_main[0][1])] and "已在运行" in quiet_main[0][1]


def test_main_releases_instance_lock_on_exit(qapp, tmp_path, monkeypatch, quiet_main):
    name = _name()
    (tmp_path / "config.toml").write_text("[general\n", encoding="utf-8")
    assert app_mod.main([], home=tmp_path, instance_name=name) == 1  # 配置文件坏了：启动失败
    again = SingleInstance(name)
    assert again.acquire() is True  # 锁已放开，下次能正常启动
    again.release()


def test_main_logging_setup_failure_shows_message_and_returns_1(qapp, tmp_path, monkeypatch, quiet_main, caplog):
    def bad(d):
        raise PermissionError("logs 目录没权限")

    monkeypatch.setattr(app_mod, "setup_logging", bad)
    with caplog.at_level(logging.ERROR):
        assert app_mod.main([], home=tmp_path) == 1
    assert quiet_main == [("critical", f"启动失败，详情见 {tmp_path / 'logs' / 'shotrans.log'}")]
    assert any(r.exc_info and isinstance(r.exc_info[1], PermissionError) for r in caplog.records)


def test_main_installs_hooks_during_run_wires_notice_and_restores(qapp, tmp_path, monkeypatch, quiet_main):
    (tmp_path / "config.toml").write_text(BASE, encoding="utf-8")
    old_sys, old_thr = sys.excepthook, threading.excepthook
    emitted = []

    class Notice:
        def emit(self):
            emitted.append(1)

    class Stub:
        def __init__(self, q, home, **kw):
            self.error_notice = Notice()

        def shutdown(self):
            pass

    monkeypatch.setattr(app_mod, "ShotTransApp", Stub)
    during = []

    def fake_exec():
        during.append((sys.excepthook, threading.excepthook))
        sys.excepthook(RuntimeError, RuntimeError("运行中炸了"), None)
        return 0

    qapp.exec = fake_exec
    try:
        assert app_mod.main([], home=tmp_path) == 0
    finally:
        del qapp.exec
    hook_sys, hook_thr = during[0]
    assert hook_sys is not old_sys and hook_thr is not old_thr
    assert emitted == [1]  # 运行期的异常会提醒用户
    assert sys.excepthook is old_sys and threading.excepthook is old_thr


def test_show_fatal_without_qapplication_uses_system_message_box(monkeypatch):
    calls = []
    monkeypatch.setattr(QApplication, "instance", staticmethod(lambda: None))
    # 万一走错分支也不能真弹出模态框把测试卡住
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a: calls.append(("qt",) + a)))

    class User32:
        def MessageBoxW(self, *a):
            calls.append(a)

    class Windll:
        user32 = User32()

    monkeypatch.setattr(app_mod.ctypes, "windll", Windll())
    app_mod._show_fatal("起不来")
    assert len(calls) == 1 and calls[0][1] == "起不来" and calls[0][2] == "截图翻译"


def test_app_error_notice_shows_notice_popup(qapp, tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(NoticePopup, "show_notice", lambda self, title, msg, *a, **k: got.append(msg))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(app_mod.GlobalHotkey, "register", lambda self: True)
    (tmp_path / "config.toml").write_text(BASE, encoding="utf-8")
    a = app_mod.ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router=object())
    try:
        a.error_notice.emit()
        assert got == [f"程序内部出错了，详情见 {tmp_path / 'logs' / 'shotrans.log'}。点这里打开自检。"]
    finally:
        a.shutdown()

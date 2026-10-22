"""自检窗口：打开就在后台跑、逐行更新、失败行带修复按钮、复制报告打码、关窗后晚到的结果被丢弃。"""
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from shotrans import i18n
from shotrans.config import Config, ServiceConfig
from shotrans.doctor import CheckItem
from shotrans.ui.doctor_window import DoctorWindow
from tests.ui_fakes import FakeApp


@pytest.fixture
def app(tmp_path):
    return FakeApp(tmp_path, Config(primary=ServiceConfig(base_url="https://api.example.com/v1",
                                                          api_key="sk-abcdef123456")))


@pytest.fixture
def make(qtbot, app, track):
    urls = []

    def _make(items):
        w = DoctorWindow(app, items_factory=lambda: list(items), open_url=urls.append)
        track(w)
        w.show()
        return w

    _make.urls = urls
    return _make


def _boom():
    raise RuntimeError("炸了")


ITEMS = [CheckItem("ocr_en", "OCR 英文", lambda: (False, "没有安装"), "lang_settings"),
         CheckItem("primary", "主服务", lambda: (True, "好"), "settings:service"),
         CheckItem("hotkey", "快捷键", lambda: (False, "被占用"), "settings:hotkey"),
         CheckItem("fonts", "字体", _boom, None)]


def _done(w):
    return all(w.row_state(i) != "pending" for i in range(w.table.rowCount()))


def test_runs_on_open_and_fills_rows(qtbot, make):
    w = make(ITEMS)
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    assert [w.row_state(i) for i in range(4)] == ["fail", "ok", "fail", "fail"]
    assert w.table.item(3, 2).text().startswith("出错：")


def test_fix_buttons_only_on_failures_with_fix(qtbot, make):
    w = make(ITEMS)
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    assert w.fix_button(0) is not None and w.fix_button(2) is not None
    assert w.fix_button(1) is None and w.fix_button(3) is None  # 成功的、没有修复动作的都没按钮


def test_fix_actions(qtbot, make, app):
    w = make(ITEMS)
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    w.fix_button(0).click()
    w.fix_button(2).click()
    assert make.urls == ["ms-settings:regionlanguage"]
    assert app.opened == [("settings", "hotkey")]


def test_service_fix_opens_settings_service(qtbot, make, app):
    w = make([CheckItem("primary", "主服务", lambda: (False, "key 无效"), "settings:service")])
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    w.fix_button(0).click()
    assert app.opened == [("settings", "service")]


def test_rows_pending_before_results(qtbot, make):
    gate = threading.Event()
    w = make([CheckItem("fonts", "字体", lambda: gate.wait(5) and (True, "好"), None)])
    assert w.row_state(0) == "pending" and w.table.item(0, 0).text() == "⏳"
    gate.set()
    qtbot.waitUntil(lambda: w.row_state(0) == "ok", timeout=3000)


def test_rerun_runs_again(qtbot, make):
    calls = []
    w = make([CheckItem("fonts", "字体", lambda: calls.append(1) or (True, "好"), None)])
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    w.btn_rerun.click()
    qtbot.waitUntil(lambda: len(calls) == 2 and _done(w), timeout=3000)


def test_stale_run_results_ignored(qtbot, make):
    gate = threading.Event()
    w = make([CheckItem("fonts", "字体", lambda: gate.wait(5) and (False, "旧的"), None)])
    first = w._thread
    w._items_factory = lambda: [CheckItem("fonts", "字体", lambda: (True, "新的"), None)]
    w.run_checks()
    qtbot.waitUntil(lambda: w.row_state(0) == "ok", timeout=3000)
    gate.set()
    first.join(5)
    qtbot.wait(100)
    assert w.row_state(0) == "ok" and w.table.item(0, 2).text() == "新的"


def test_old_run_message_ignored(qtbot, make):
    w = make([CheckItem("fonts", "字体", lambda: (True, "新的"), None)])
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    w._inbox.put(("row", w._run_id - 1, 0, False, "旧的"))  # 上一轮迟到的结果
    qtbot.wait(100)
    assert w.row_state(0) == "ok" and w.table.item(0, 2).text() == "新的"


def test_close_while_running_drops_late_results(qtbot, app):
    gate = threading.Event()
    calls = []

    def slow():
        gate.wait(5)
        calls.append(1)
        return True, "好"

    w = DoctorWindow(app, items_factory=lambda: [CheckItem("fonts", "字体", slow, None)], open_url=lambda u: None)
    w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    w.show()
    th = w._thread
    w.close()
    gate.set()
    th.join(5)
    qtbot.wait(100)  # 定时器已停：晚到的结果没人取，也不能有异常
    assert calls == [1] and not th.is_alive()


def test_copy_report_masks_key(qtbot, make):
    w = make(ITEMS)
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    w.copy_report()
    text = QGuiApplication.clipboard().text()
    assert "sk-abcdef123456" not in text and "sk-…3456" in text and "https://api.example.com/v1" in text
    assert "❌ OCR 英文：没有安装" in text


def test_language_switch(qtbot, make):
    w = make(ITEMS)
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    i18n.set_language("en")
    assert w.windowTitle() == "Diagnostics" and w.btn_rerun.text() == "Run again"
    assert w.table.item(1, 1).text() == "Primary service"


def test_dependent_rows_get_no_fix_button_when_dependency_failed(qtbot, make):
    """配置文件读不到：只有配置文件那行带「打开设置」，依赖它的服务行不再各带一个一样的按钮。"""
    w = make([CheckItem("config", "配置文件", lambda: (False, "找不到"), "settings:service"),
              CheckItem("primary", "主服务", lambda: (False, "配置没读到"), "settings:service", "config")])
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    assert w.fix_button(0) is not None and w.fix_button(1) is None


def test_dependent_row_keeps_fix_button_when_dependency_ok(qtbot, make):
    w = make([CheckItem("config", "配置文件", lambda: (True, "读取成功"), "settings:service"),
              CheckItem("primary", "主服务", lambda: (False, "key 无效"), "settings:service", "config")])
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    assert w.fix_button(1) is not None


def test_bad_result_row_is_failure(qtbot, make):
    w = make([CheckItem("fonts", "字体", lambda: None, None)])
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    assert w.row_state(0) == "fail" and w.table.item(0, 2).text().startswith("出错：")


def test_close_stops_pump(qtbot, make):
    """关窗即停定时器：之后队列里再来的本轮结果不会被取走、画到已关的窗口上。"""
    gate = threading.Event()
    w = make([CheckItem("fonts", "字体", lambda: gate.wait(5) and (True, "好"), None)])
    w.close()
    w._inbox.put(("row", w._run_id, 0, True, "关窗后才到"))
    qtbot.wait(100)
    gate.set()
    w._thread.join(5)
    assert w.row_state(0) == "pending"


def _two_step(gate, later_calls):
    def first():
        gate.wait(5)
        return True, "第一项"

    def second():
        later_calls.append(1)
        return True, "第二项"

    return [CheckItem("ocr_en", "OCR 英文", first, None), CheckItem("fonts", "字体", second, None)]


def test_close_cancels_remaining_checks(qtbot, make):
    """关窗置取消标志：正在跑的那项跑完后，后面的项不再检查（不白白连网、试注册热键）。"""
    gate, later = threading.Event(), []
    w = make(_two_step(gate, later))
    th = w._thread
    w.close()
    gate.set()
    th.join(5)
    assert not th.is_alive() and later == []


def test_worker_checks_cancel_before_each_item():
    """工作线程开始一项之前先看取消标志：已取消就一项都不跑。"""
    import queue

    from shotrans.ui.doctor_window import _check_worker

    cancel, inbox, calls = threading.Event(), queue.Queue(), []
    cancel.set()
    _check_worker([lambda: calls.append(1) or (True, "好")], 1, cancel, inbox)
    assert calls == [] and inbox.empty()


def test_worker_drops_result_finished_after_cancel():
    """一项跑到一半被取消：跑完后不再把结果交出去（连结束标记也不交）。"""
    import queue

    from shotrans.ui.doctor_window import _check_worker

    cancel, inbox = threading.Event(), queue.Queue()

    def cancelled_midway():
        cancel.set()
        return True, "好"

    _check_worker([cancelled_midway], 1, cancel, inbox)
    assert inbox.empty()


def test_rerun_cancels_previous_run(qtbot, make):
    """重新检查时上一轮停下：上一轮后面的项不再跑。"""
    gate, later = threading.Event(), []
    w = make(_two_step(gate, later))
    first = w._thread
    w._items_factory = lambda: [CheckItem("fonts", "字体", lambda: (True, "新的"), None)]
    w.run_checks()
    gate.set()
    first.join(5)
    assert not first.is_alive() and later == []


def test_default_items_ignore_legacy_config(qtbot, app, track, monkeypatch):
    """界面上的自检只查数据目录里的配置：不退回去读程序目录的旧 config.local.toml（那只给命令行 doctor 用）。"""
    from shotrans import doctor, paths
    legacy = app.home / "prog" / "config.local.toml"
    legacy.parent.mkdir()
    legacy.write_text('[vllm]\napi_key = "k"\n', encoding="utf-8")
    monkeypatch.setattr(paths, "legacy_config_path", lambda: legacy)
    calls = []

    def fake_build(home, legacy_path=None, own_hotkey=None):
        calls.append((home, legacy_path))
        return [CheckItem("fonts", "字体", lambda: (True, "好"), None)]

    monkeypatch.setattr(doctor, "build_items", fake_build)
    w = DoctorWindow(app, open_url=lambda url: None)
    track(w)
    w.show()
    qtbot.waitUntil(lambda: _done(w), timeout=3000)
    assert calls == [(app.home, None)]

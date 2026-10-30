"""引导的翻译服务页：「大模型服务」在上、推荐、默认选中（预设默认智谱，免费），可勾「连不上时用微软兜底」；
「免费：微软翻译」在下：不用 key，测通就能完成；完成后主服务不动、只开免费翻译。测试函数都是注入的假货，不连网。"""
import threading

import pytest

from shotrans import i18n
from shotrans.config import Config, ServiceConfig
from shotrans.services import ConnResult, ModelsResult
from shotrans.ui.wizard import Wizard
from tests.ui_fakes import FakeApp


@pytest.fixture
def make(qtbot, tmp_path, track):
    def _make(free=(True, "单句 0.3 秒，译文：你好，世界。"), config=None):
        app = FakeApp(tmp_path)
        if config is not None:
            app.config = config
        calls = []

        def free_tester():
            calls.append(threading.current_thread())
            if isinstance(free, Exception):
                raise free
            return free

        w = Wizard(app, languages_fn=lambda: ["en-US", "zh-Hans-CN"],
                   tester=lambda svc: ConnResult("ok", 1.0, "", "好", "m"),
                   lister=lambda u, k: ModelsResult("ok", ("m",)), open_url=lambda u: None, warn=lambda s: None,
                   free_tester=free_tester)
        track(w)
        w.show()
        w.next()
        w.next()
        assert w.page() == "service"
        w.free_calls = calls
        return w, app
    return _make


def _free_test(qtbot, w):
    w.mode_free.setChecked(True)
    w.free_test.btn_test.click()
    qtbot.waitUntil(lambda: w.free_test.btn_test.isEnabled(), timeout=3000)


def test_llm_is_recommended_default_and_on_top(make):
    w, _ = make()
    assert w.mode_llm.isChecked() and w.llm_box.isVisible() and not w.free_box.isVisible()
    assert "推荐" in w.mode_llm.text() and "不想注册" in w.mode_free.text()
    lay = w.mode_llm.parentWidget().layout()
    assert lay.indexOf(w.mode_llm) < lay.indexOf(w.mode_free)  # 大模型在上
    assert not w.btn_next.isEnabled()  # 没测之前不能下一步


def test_free_mode_shows_free_box_and_needs_its_own_test(make):
    w, _ = make()
    w.mode_free.setChecked(True)
    assert w.free_box.isVisible() and not w.llm_box.isVisible() and not w.btn_next.isEnabled()


def test_free_test_runs_off_ui_thread_then_enables_next(qtbot, make):
    w, _ = make()
    _free_test(qtbot, w)
    assert w.free_calls[0] is not threading.main_thread()
    assert w.btn_next.isEnabled() and "你好，世界" in w.free_test.result.text()


def test_free_test_failure_keeps_next_disabled_and_shows_reason(qtbot, make):
    w, _ = make(free=(False, "不可用（connect：x）。可以改用智谱"))
    _free_test(qtbot, w)
    assert not w.btn_next.isEnabled() and "智谱" in w.free_test.result.text()


def test_free_tester_exception_is_failure_not_hang(qtbot, make):
    w, _ = make(free=RuntimeError("boom"))
    _free_test(qtbot, w)
    assert not w.btn_next.isEnabled() and "boom" in w.free_test.result.text()


def test_finish_free_keeps_primary_empty_and_turns_free_on(qtbot, make):
    w, app = make()
    _free_test(qtbot, w)
    w.next()
    assert w.finish() is True
    saved = app.saved[0]
    assert saved.free_enabled is True and not saved.primary.configured


def test_each_mode_tested_separately(qtbot, make):
    w, _ = make()
    _free_test(qtbot, w)
    w.mode_llm.setChecked(True)
    assert w.llm_box.isVisible() and not w.free_box.isVisible()
    assert not w.btn_next.isEnabled()  # 大模型那边还没测
    w.mode_free.setChecked(True)
    assert w.btn_next.isEnabled()  # 切回来不用重测


def test_llm_default_preset_is_zhipu(make):
    w, _ = make()
    w.mode_llm.setChecked(True)
    assert w.form.preset.currentData() == "zhipu"


def _llm_pass(qtbot, w):
    w.mode_llm.setChecked(True)
    w.form.preset.setCurrentIndex(w.form.preset.findData("zhipu"))
    w.form.api_key.setText("k-zhipu")
    w.form.start_test()
    qtbot.waitUntil(lambda: w.form.test_ok, timeout=3000)
    w.next()


def test_llm_backup_unchecked_by_default_saves_free_off(qtbot, make):
    w, app = make()
    _llm_pass(qtbot, w)
    assert w.finish() is True
    assert app.saved[0].free_enabled is False and app.saved[0].primary.base_url == "https://open.bigmodel.cn/api/paas/v4"


def test_llm_backup_checked_saves_free_on(qtbot, make):
    w, app = make()
    w.mode_llm.setChecked(True)
    w.free_backup.setChecked(True)
    _llm_pass(qtbot, w)
    assert w.finish() is True
    assert app.saved[0].free_enabled is True and app.saved[0].primary.configured


def test_existing_primary_opens_on_llm_side(make):
    cfg = Config(primary=ServiceConfig(preset="custom", base_url="http://10.0.0.1:8000/v1"), free_enabled=True)
    w, _ = make(config=cfg)
    assert w.mode_llm.isChecked() and w.free_backup.isChecked()


def test_finish_refused_when_free_not_tested(make):
    w, app = make()
    w.mode_free.setChecked(True)
    w.stack.setCurrentIndex(3)  # 硬跳到最后一页也不行
    assert w.finish() is False and app.saved == []


def test_english_texts(make):
    w, _ = make()
    i18n.set_language("en")
    assert "Microsoft" in w.mode_free.text() and w.free_test.btn_test.text() == "Test connection"
    assert "Zhipu" in w.service_note.text() and "Ollama" in w.service_note.text()
    assert "recommended" in w.mode_llm.text()

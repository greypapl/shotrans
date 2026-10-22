import pytest

from shotrans import i18n
from shotrans.config import Config
from shotrans.services import ConnResult, ModelsResult
from shotrans.ui.wizard import Wizard
from tests.ui_fakes import FakeApp


@pytest.fixture
def make(qtbot, tmp_path, track):
    def _make(langs=("en-US", "zh-Hans-CN"), result=ConnResult("ok", 1.0, "", "好", "m")):
        app = FakeApp(tmp_path)
        state = {"langs": list(langs)}
        opened, warned, tested = [], [], []

        def tester(svc):
            tested.append(svc)
            return result

        w = Wizard(app, languages_fn=lambda: list(state["langs"]), tester=tester,
                   lister=lambda u, k: ModelsResult("ok", ("m",)), open_url=opened.append, warn=warned.append)
        track(w)
        w.show()
        w.state, w.opened, w.warned, w.tested = state, opened, warned, tested
        return w, app
    return _make


def _to_service(w):
    w.next()  # welcome → env
    w.next()  # env → service
    assert w.page() == "service"


def _pass_test(qtbot, w):
    w.form.preset.setCurrentIndex(w.form.preset.findData("deepseek"))
    w.form.api_key.setText("sk-wiz")
    w.form.start_test()
    qtbot.waitUntil(lambda: w.form.test_ok, timeout=3000)


def test_starts_on_welcome(make):
    w, _ = make()
    assert w.page() == "welcome" and not w.btn_back.isEnabled() and w.btn_next.isEnabled()


def test_language_choice_switches_ui(make):
    w, _ = make()
    w.language.setCurrentIndex(w.language.findData("en"))
    assert i18n.language() == "en" and w.windowTitle() == "ShotTrans setup" and w.btn_next.text() == "Next"


def test_env_both_installed(make):
    w, _ = make()
    w.next()
    assert w.page() == "env" and w.can_next() and w.chk_continue.isHidden()
    assert w.lbl_en.text().startswith("✅") and w.lbl_zh.text().startswith("✅")


def test_env_one_missing_still_can_continue(make):
    w, _ = make(langs=("en-US",))
    w.next()
    assert w.can_next() and w.lbl_zh.text().startswith("❌") and w.chk_continue.isHidden()


def test_env_both_missing_needs_continue_anyway(make):
    w, _ = make(langs=())
    w.next()
    assert not w.can_next() and not w.btn_next.isEnabled() and not w.chk_continue.isHidden()
    w.chk_continue.setChecked(True)
    assert w.can_next() and w.btn_next.isEnabled()


def test_recheck(make):
    w, _ = make(langs=())
    w.next()
    w.state["langs"] = ["zh-Hans-CN"]
    w.btn_recheck.click()
    assert w.can_next() and w.lbl_zh.text().startswith("✅")


def test_open_language_settings(make):
    w, _ = make(langs=())
    w.next()
    w.btn_lang_settings.click()
    assert w.opened == ["ms-settings:regionlanguage"]


def test_next_disabled_until_test_passes(qtbot, make):
    w, _ = make()
    _to_service(w)
    assert not w.btn_next.isEnabled()
    _pass_test(qtbot, w)
    assert w.btn_next.isEnabled()


def test_failed_test_keeps_next_disabled(qtbot, make):
    w, _ = make(result=ConnResult("auth", 0.3, "HTTP 401"))
    _to_service(w)
    w.form.preset.setCurrentIndex(w.form.preset.findData("deepseek"))
    w.form.start_test()
    qtbot.waitUntil(lambda: w.form.result.text().startswith("❌"), timeout=3000)
    assert not w.btn_next.isEnabled()


def test_edit_after_pass_disables_next(qtbot, make):
    w, _ = make()
    _to_service(w)
    _pass_test(qtbot, w)
    w.form.api_key.setText("另一个")
    assert not w.btn_next.isEnabled()


def test_edit_url_after_pass_disables_next(qtbot, make):
    w, _ = make()
    _to_service(w)
    _pass_test(qtbot, w)
    w.form.base_url.setText("https://example.com/v1")
    assert not w.btn_next.isEnabled()


def test_edit_model_after_pass_disables_next(qtbot, make):
    w, _ = make()
    _to_service(w)
    _pass_test(qtbot, w)
    w.form.model.setEditText("另一个模型")
    assert not w.btn_next.isEnabled()


def test_change_preset_after_pass_disables_next(qtbot, make):
    w, _ = make()
    _to_service(w)
    _pass_test(qtbot, w)
    w.form.preset.setCurrentIndex(w.form.preset.findData("siliconflow"))
    assert not w.btn_next.isEnabled()


def test_finish_saves_config_and_reports_completed(qtbot, make):
    w, app = make()
    _to_service(w)
    _pass_test(qtbot, w)
    w.next()
    assert w.page() == "finish" and w.hotkey_edit.spec() == "Ctrl+Alt+Q"
    w.autostart.setChecked(True)
    assert w.finish() is True
    svc = w.tested[-1]  # 测试连接时真正测过的那一份，而不是保存时再从控件读一遍
    assert svc.preset == "deepseek" and svc.api_key == "sk-wiz"
    assert app.saved == [Config(language="zh", autostart=True, primary=svc, hotkey="Ctrl+Alt+Q")]
    assert app.wizard_closed == [True] and not w.isVisible()


def test_finish_keeps_other_settings(qtbot, tmp_path, track):
    """引导只改语言、开机自启、主服务、快捷键；程序已有的其它设置（这里是腾讯开、历史 50 条，都不是默认值）原样保留。"""
    app = FakeApp(tmp_path, Config(tencent_enabled=True, history_limit=50))
    w = Wizard(app, languages_fn=lambda: ["en-US"], tester=lambda svc: ConnResult("ok", 1.0, "", "好", "m"),
               lister=lambda u, k: ModelsResult("ok", ("m",)), open_url=lambda u: None, warn=lambda s: None)
    track(w)
    w.show()
    _to_service(w)
    _pass_test(qtbot, w)
    w.next()
    assert w.finish() is True
    assert app.saved[0].tencent_enabled is True and app.saved[0].history_limit == 50


def test_fresh_wizard_saves_tencent_off(qtbot, make):
    """全新安装（程序配置 = Config()）：引导写出的配置不开腾讯兜底。"""
    w, app = make()
    _to_service(w)
    _pass_test(qtbot, w)
    w.next()
    assert w.finish() is True
    assert app.saved[0].tencent_enabled is False


def test_try_now_starts_capture(qtbot, make):
    w, app = make()
    _to_service(w)
    _pass_test(qtbot, w)
    w.next()
    w.btn_try.click()
    qtbot.waitUntil(lambda: app.captures == 1, timeout=3000)
    assert len(app.saved) == 1


def _taken_wizard(tmp_path, track):
    app = FakeApp(tmp_path)
    app.taken_hotkeys = {"Ctrl+Alt+Q"}
    w = Wizard(app, languages_fn=lambda: ["en-US"], tester=lambda svc: ConnResult("ok", 1.0, "", "好", "m"),
               lister=lambda u, k: ModelsResult("ok", ("m",)), open_url=lambda u: None, warn=lambda s: None)
    track(w)
    w.show()
    return w, app


def test_finish_blocked_by_taken_hotkey(qtbot, tmp_path, track):
    w, app = _taken_wizard(tmp_path, track)
    assert w.hotkey_edit.taken
    _to_service(w)
    _pass_test(qtbot, w)
    w.next()
    assert w.page() == "finish" and w.hotkey_edit.taken
    assert w.finish() is False and app.saved == []


def test_try_disabled_when_hotkey_taken(qtbot, tmp_path, track):
    w, app = _taken_wizard(tmp_path, track)
    _to_service(w)
    _pass_test(qtbot, w)
    w.next()
    assert w.page() == "finish" and not w.btn_try.isEnabled() and not w.btn_finish.isEnabled()


def test_hotkey_rechecked_on_entering_finish(qtbot, tmp_path, track):
    w, app = _taken_wizard(tmp_path, track)
    assert w.hotkey_edit.taken
    app.taken_hotkeys = set()  # 引导打开后，占着这个键的程序退出了
    _to_service(w)
    _pass_test(qtbot, w)
    w.next()
    assert not w.hotkey_edit.taken and w.btn_finish.isEnabled() and w.btn_try.isEnabled()


def test_finish_on_welcome_does_nothing(make):
    w, app = make()
    assert w.finish() is False and app.saved == [] and w.isVisible() and app.wizard_closed == []


def test_finish_before_finish_page_does_nothing(qtbot, make):
    """测试连接已通过、但还停在服务页：不能跳过完成页直接保存（只靠「页面不对」这一条拦）。"""
    w, app = make()
    _to_service(w)
    _pass_test(qtbot, w)
    assert w.finish() is False and app.saved == []


def test_finish_after_service_edited_does_nothing(qtbot, make):
    """已到完成页、之后服务又被改动（测试结果作废）：不能保存（只靠「测试已通过」这一条拦）。"""
    w, app = make()
    _to_service(w)
    _pass_test(qtbot, w)
    w.next()
    w.form.api_key.setText("改过的")
    assert w.page() == "finish" and w.finish() is False and app.saved == []


def test_close_midway_reports_unfinished(make):
    w, app = make()
    w.close()
    assert app.wizard_closed == [False]


def test_save_error_warns_and_stays(qtbot, make):
    w, app = make()
    _to_service(w)
    _pass_test(qtbot, w)
    w.next()
    app.save_error = "磁盘满了"
    assert w.finish() is False and w.warned == ["保存失败：磁盘满了"] and w.isVisible()


def test_back_button(make):
    w, _ = make()
    w.next()
    w.btn_back.click()
    assert w.page() == "welcome"


# ---------- 开机自启框：初值取真实状态；试用模式禁用并说明 ----------
def _wizard_with_state(tmp_path, track, state):
    w = Wizard(FakeApp(tmp_path), languages_fn=lambda: ["en-US"],
               tester=lambda svc: ConnResult("ok", 1.0, "", "好", "m"),
               lister=lambda u, k: ModelsResult("ok", ("m",)), open_url=lambda u: None, warn=lambda s: None,
               autostart_state=lambda: state)
    track(w)
    return w


def test_autostart_initially_checked_when_link_exists(tmp_path, track):
    """已开着开机自启的人走引导（比如数据目录被删了）：不勾 = 完成时删掉快捷方式，所以初值要跟真实状态。"""
    assert _wizard_with_state(tmp_path, track, True).autostart.isChecked()


def test_autostart_initially_unchecked_when_no_link(tmp_path, track):
    assert not _wizard_with_state(tmp_path, track, False).autostart.isChecked()


def test_autostart_default_state_reads_real_link(tmp_path, track):
    """没传 autostart_state 时用 autostart.is_enabled（conftest 把「启动」文件夹换成了临时目录）。"""
    from shotrans import autostart
    autostart.enable(*autostart.launch_command())
    w = Wizard(FakeApp(tmp_path), languages_fn=lambda: ["en-US"], open_url=lambda u: None, warn=lambda s: None)
    track(w)
    assert w.autostart.isChecked()


def test_trial_mode_disables_autostart_with_note(tmp_path, track, monkeypatch):
    monkeypatch.setenv("SHOTRANS_TRIAL", "1")
    w = _wizard_with_state(tmp_path, track, True)
    assert not w.autostart.isEnabled()
    assert not w.autostart_trial.isHidden() and w.autostart_trial.text() == "试用模式（用临时数据目录启动）不会改动开机自启。"


def test_normal_mode_autostart_enabled_no_note(tmp_path, track):
    w = _wizard_with_state(tmp_path, track, False)
    assert w.autostart.isEnabled() and w.autostart_trial.isHidden()


def test_trial_note_follows_language(tmp_path, track, monkeypatch):
    monkeypatch.setenv("SHOTRANS_TRIAL", "1")
    w = _wizard_with_state(tmp_path, track, False)
    i18n.set_language("en")
    assert w.autostart_trial.text() == "Trial mode (temporary data folder) doesn't change start-with-Windows."

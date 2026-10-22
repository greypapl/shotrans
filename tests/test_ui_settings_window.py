from dataclasses import replace

import pytest
from PySide6.QtCore import Qt

from shotrans import i18n
from shotrans.config import Config, ServiceConfig
from shotrans.services import ConnResult, ModelsResult
from shotrans.ui.settings_window import SettingsWindow
from tests.ui_fakes import FakeApp

SAMPLE = "# 术语表\ndepression => 抑郁\n"
PRIMARY = ServiceConfig(preset="deepseek", base_url="https://api.deepseek.com", api_key="sk-x",
                        model="deepseek-flash", thinking_off=True, thinking_style="deepseek")
CFG = Config(primary=PRIMARY, hotkey="Ctrl+Alt+Shift+F10")


@pytest.fixture
def make(qtbot, tmp_path, track):
    def _make(config=CFG, glossary=SAMPLE, page=None):
        (tmp_path / "术语表.txt").write_text(glossary, encoding="utf-8")
        app = FakeApp(tmp_path, config)
        warned, opened = [], []
        w = SettingsWindow(app, page=page, tester=lambda svc: ConnResult("ok", 1.0, "", "好", "m"),
                           lister=lambda u, k: ModelsResult("ok", ("m",)), confirm=lambda issues: True,
                           warn=warned.append, open_path=opened.append, open_url=opened.append,
                           autostart_state=lambda: False)
        track(w)
        w.show()
        w.warned, w.opened = warned, opened
        return w, app
    return _make


def test_five_tabs(make):
    w, _ = make()
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ["翻译服务", "快捷键", "术语表", "常规", "高级"]


def test_opens_on_requested_page(make):
    w, _ = make(page="hotkey")
    assert w.tabs.currentIndex() == 1
    w.show_page("advanced")
    assert w.tabs.currentIndex() == 4
    w.show_page("nope")
    assert w.tabs.currentIndex() == 0


def test_loads_current_config(make):
    w, _ = make()
    assert w.primary.base_url.text() == "https://api.deepseek.com" and w.primary.api_key.text() == "sk-x"
    assert w.backup.is_empty() and not w.tencent.isChecked()  # CFG 没开腾讯（全新默认是关）
    assert w.hotkey_edit.spec() == "Ctrl+Alt+Shift+F10"
    assert w.language.currentData() == "zh" and w.history_limit.value() == 200


def test_tencent_checkbox_follows_config(make):
    w, _ = make(config=replace(CFG, tencent_enabled=True))
    assert w.tencent.isChecked()


def test_tencent_checkbox_off_for_fresh_config(make):
    w, app = make(config=Config(primary=PRIMARY))  # 默认值 + 一个主服务（保存要求至少有一层可用）
    assert not w.tencent.isChecked()
    assert w.apply() is True and app.saved[-1].tencent_enabled is False


def test_unchanged_apply_saves_current_with_real_autostart_state(make):
    w, app = make()
    assert w.apply() is True
    assert app.saved == [replace(CFG, autostart=False)]
    assert w.isVisible()


def test_ok_saves_and_closes(make):
    w, app = make()
    w.domain.setText("医学论文")
    w.btn_ok.click()
    assert app.saved[-1].domain_hint == "医学论文" and not w.isVisible()


def test_cancel_discards(make):
    w, app = make()
    w.domain.setText("医学论文")
    w.btn_cancel.click()
    assert app.saved == [] and not w.isVisible()


def test_invalid_url_blocks_and_marks_red(make):
    w, app = make()
    w.show_page("general")
    w.primary.preset.setCurrentIndex(w.primary.preset.findData("custom"))
    w.primary.base_url.setText("api.deepseek.com")
    assert w.apply() is False and app.saved == []
    assert w.tabs.currentIndex() == 0 and "#ef4444" in w.primary.base_url.styleSheet()
    assert w.error_label.text() == "有填错的地方（已标红），请改好再保存"


def test_primary_required(make):
    w, app = make()
    w.primary.preset.setCurrentIndex(w.primary.preset.findData("custom"))
    w.primary.base_url.setText("")
    assert w.apply() is False and app.saved == []


def test_backup_empty_saved_as_default(make):
    w, app = make()
    assert w.apply() is True and app.saved[-1].backup == ServiceConfig()


def test_backup_filled_is_saved(make):
    w, app = make()
    w.backup.preset.setCurrentIndex(w.backup.preset.findData("bailian"))
    w.backup.api_key.setText("sk-b")
    w.apply()
    b = app.saved[-1].backup
    assert (b.preset, b.base_url, b.api_key, b.model) == (
        "bailian", "https://dashscope.aliyuncs.com/compatible-mode/v1", "sk-b", "qwen-flash")


def test_taken_hotkey_blocks_and_switches_page(qtbot, make):
    w, app = make()
    app.taken_hotkeys = {"Ctrl+Alt+W"}
    w.show_page("hotkey")
    qtbot.keyClick(w.hotkey_edit.edit, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    w.show_page("service")
    assert w.apply() is False and w.tabs.currentIndex() == 1 and app.saved == []


def test_new_hotkey_saved(qtbot, make):
    w, app = make()
    qtbot.keyClick(w.hotkey_edit.edit, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    assert w.apply() is True and app.saved[-1].hotkey == "Ctrl+Alt+W"


def test_hotkey_default_button(make):
    w, _ = make()
    w.btn_hotkey_default.click()
    assert w.hotkey_edit.spec() == "Ctrl+Alt+Q"


def test_glossary_blocking_issue_blocks(make, tmp_path):
    w, app = make()
    i = w.glossary.add_row()
    w.glossary.table.item(i, 1).setText("只有译法")
    assert w.apply() is False and w.tabs.currentIndex() == 2 and app.saved == []
    assert (tmp_path / "术语表.txt").read_text(encoding="utf-8") == SAMPLE


def test_glossary_saved_with_config(make, tmp_path):
    w, app = make()
    w.glossary.table.item(1, 1).setText("抑郁症")
    assert w.apply() is True
    assert (tmp_path / "术语表.txt").read_text(encoding="utf-8") == "# 术语表\ndepression => 抑郁症\n"
    assert len(app.saved) == 1


def test_general_values_collected(make):
    w, app = make()
    w.language.setCurrentIndex(w.language.findData("en"))
    w.autostart.setChecked(True)
    w.history_limit.setValue(77)
    w.tencent.setChecked(False)
    w.apply()
    c = app.saved[-1]
    assert (c.language, c.autostart, c.history_limit, c.tencent_enabled) == ("en", True, 77, False)


def test_advanced_values_collected(make):
    w, app = make()
    w.adv["stall_timeout"].setValue(33.5)
    w.adv["max_concurrency"].setValue(2)
    w.apply()
    assert app.saved[-1].stall_timeout == 33.5 and app.saved[-1].max_concurrency == 2


def test_advanced_reset_defaults(make):
    w, _ = make(config=replace(CFG, chunk_chars=100, stall_timeout=99.0, breaker_seconds=5.0))
    w.btn_adv_default.click()
    assert {k: s.value() for k, s in w.adv.items()} == {
        "chunk_chars": 600, "chunk_max_segments": 8, "max_concurrency": 4,
        "connect_timeout": 6.0, "first_token_timeout": 10.0, "stall_timeout": 20.0,
        "backup_connect_timeout": 6.0, "backup_first_token_timeout": 10.0, "backup_stall_timeout": 20.0,
        "breaker_seconds": 60.0, "tencent_connect_timeout": 6.0}


def test_advanced_has_tips(make):
    w, _ = make()
    assert all(w.adv_tips[k].text() for k in w.adv)


def test_open_data_folder(make):
    w, app = make()
    w.btn_open_data.click()
    assert w.opened == [app.home]


def test_save_error_warns_and_stays_open(make):
    w, app = make()
    app.save_error = "磁盘满了"
    w.btn_ok.click()
    assert w.warned == ["保存失败：磁盘满了"] and w.isVisible()


def test_partly_applied_shown(make):
    w, app = make()
    app.apply_failures = ["快捷键 Ctrl+Alt+W（被占用，已恢复原快捷键）"]
    assert w.apply() is True
    assert w.error_label.text() == "已保存，但以下设置没有生效：快捷键 Ctrl+Alt+W（被占用，已恢复原快捷键）"


def test_language_switch_retranslates(make):
    w, _ = make()
    i18n.set_language("en")
    assert w.tabs.tabText(0) == "Translation service" and w.windowTitle() == "Settings"
    assert w.btn_ok.text() == "OK"


def test_close_shuts_down_forms(make):
    w, _ = make()
    w.close()
    assert not w.primary._pump.isActive() and not w.backup._pump.isActive()


# ---------- 以下为本任务在计划用例之外补的：术语表写盘失败、先存术语表后配置失败、数据目录被删、解不开的 key ----------
def test_glossary_write_error_warns_and_skips_config(make, monkeypatch):
    from shotrans.ui import glossary_page

    def boom(doc, path):
        raise PermissionError("拒绝访问")

    w, app = make()
    w.glossary.table.item(1, 1).setText("抑郁症")
    monkeypatch.setattr(glossary_page, "save_doc", boom)
    assert w.apply() is False and app.saved == [] and w.tabs.currentIndex() == 2
    assert w.warned == ["术语表保存失败：拒绝访问"]


def test_config_failure_after_glossary_saved_says_glossary_kept(make, tmp_path):
    w, app = make()
    w.glossary.table.item(1, 1).setText("抑郁症")
    app.save_error = "磁盘满了"
    assert w.apply() is False and w.isVisible()
    assert w.warned == ["设置保存失败：磁盘满了（术语表已经保存）"]
    assert (tmp_path / "术语表.txt").read_text(encoding="utf-8") == "# 术语表\ndepression => 抑郁症\n"


def test_glossary_save_recreates_missing_data_dir(qtbot, tmp_path, track):
    import shutil

    home = tmp_path / "数据"
    app = FakeApp(home, CFG)
    w = SettingsWindow(app, tester=lambda svc: ConnResult("ok", 1.0, "", "好", "m"),
                       lister=lambda u, k: ModelsResult("ok", ("m",)), confirm=lambda issues: True,
                       warn=lambda s: None, open_path=lambda p: None, open_url=lambda u: None,
                       autostart_state=lambda: False)
    track(w)
    shutil.rmtree(home)
    i = w.glossary.add_row()
    w.glossary.table.item(i, 0).setText("anxiety")
    w.glossary.table.item(i, 1).setText("焦虑")
    assert w.apply() is True
    assert "anxiety => 焦虑" in (home / "术语表.txt").read_text(encoding="utf-8")


def test_unreadable_primary_key_kept_when_untouched(make):
    cfg = replace(CFG, primary=replace(PRIMARY, api_key="", key_unreadable=True, key_enc="CIPHER"))
    w, app = make(config=cfg)
    assert w.apply() is True
    p = app.saved[-1].primary
    assert (p.api_key, p.key_unreadable, p.key_enc) == ("", True, "CIPHER")

def test_glossary_blocking_issue_reported_with_other_errors(make):
    """设置窗口自己先查术语表阻止项（和别的错误一起标红提示），不是等到 glossary.save() 才悄悄拦下。"""
    w, app = make()
    i = w.glossary.add_row()
    w.glossary.table.item(i, 1).setText("只有译法")
    assert w.apply() is False and app.saved == []
    assert w.error_label.text() == "有填错的地方（已标红），请改好再保存"

# ---------- 评审修复（Minor 5）：启动时就被占的快捷键，用户没动就不挡保存 ----------
def _window_with_taken(tmp_path, track, taken):
    (tmp_path / "术语表.txt").write_text(SAMPLE, encoding="utf-8")
    app = FakeApp(tmp_path, CFG)
    app.taken_hotkeys = set(taken)
    w = SettingsWindow(app, tester=lambda svc: ConnResult("ok", 1.0, "", "好", "m"),
                       lister=lambda u, k: ModelsResult("ok", ("m",)), confirm=lambda issues: True,
                       warn=lambda s: None, open_path=lambda p: None, open_url=lambda u: None,
                       autostart_state=lambda: False)
    track(w)
    return w, app


def test_untouched_taken_hotkey_does_not_block_other_settings(tmp_path, track):
    w, app = _window_with_taken(tmp_path, track, {"Ctrl+Alt+Shift+F10"})
    assert w.hotkey_edit.taken
    w.domain.setText("医学论文")
    assert w.apply() is True
    assert app.saved[-1].hotkey == "Ctrl+Alt+Shift+F10" and app.saved[-1].domain_hint == "医学论文"


def test_changed_to_taken_hotkey_still_blocks_when_loaded_was_taken(qtbot, tmp_path, track):
    w, app = _window_with_taken(tmp_path, track, {"Ctrl+Alt+Shift+F10", "Ctrl+Alt+W"})
    qtbot.keyClick(w.hotkey_edit.edit, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    assert w.apply() is False and app.saved == [] and w.tabs.currentIndex() == 1

def test_saved_hotkey_becomes_new_baseline(qtbot, tmp_path, track):
    w, app = _window_with_taken(tmp_path, track, set())
    qtbot.keyClick(w.hotkey_edit.edit, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    assert w.apply() is True
    app.taken_hotkeys = {"Ctrl+Alt+W"}
    w.hotkey_edit.set_spec("Ctrl+Alt+W")  # 刚存进配置的键，之后被别的软件占了：用户没再改，不挡
    assert w.hotkey_edit.taken and w.apply() is True

def test_invalid_hotkey_blocks_via_config_validate_on_hotkey_page(tmp_path, track):
    w, app = _window_with_taken(tmp_path, track, set())
    w.hotkey_edit.set_spec("Hyper+Q")
    assert w.apply() is False and app.saved == [] and w.tabs.currentIndex() == 1

def test_bad_key_blocks_apply_on_service_page(make):
    w, app = make(page="hotkey")
    w.primary.api_key.setText("sk-中文SECRET1234")
    assert w.apply() is False and app.saved == [] and w.tabs.currentIndex() == 0
    assert "#ef4444" in w.primary.api_key.styleSheet()


def test_key_field_errors_map_to_service_page():
    from shotrans.ui.settings_window import _page_of
    assert _page_of("[primary] api_key") == "service" and _page_of("[backup] api_key") == "service"


# ---------- 试用模式：开机自启框禁用并说明 ----------
def test_trial_mode_disables_autostart_with_note(make, monkeypatch):
    monkeypatch.setenv("SHOTRANS_TRIAL", "1")
    w, _ = make()
    assert not w.autostart.isEnabled()
    assert not w.autostart_trial.isHidden() and w.autostart_trial.text() == "试用模式（用临时数据目录启动）不会改动开机自启。"


def test_normal_mode_autostart_enabled_no_note(make):
    w, _ = make()
    assert w.autostart.isEnabled() and w.autostart_trial.isHidden()


def test_trial_note_follows_language(make, monkeypatch):
    monkeypatch.setenv("SHOTRANS_TRIAL", "1")
    w, _ = make()
    i18n.set_language("en")
    assert w.autostart_trial.text() == "Trial mode (temporary data folder) doesn't change start-with-Windows."


def test_unchanged_apply_with_autostart_on_still_saves(make):
    """框原本就勾着、什么都没改，点「应用」也照样 save_config（快捷方式指向的检查在 apply_config 里做）。"""
    w, app = make(config=replace(CFG, autostart=True))
    assert w.apply() is True
    assert app.saved == [replace(CFG, autostart=True)]

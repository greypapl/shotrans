"""设置窗口「快捷键」页的第二个录入框（复制后翻译）、录入框的「空着 = 不用」、术语表页并入别处加的词条。"""
from dataclasses import replace

import pytest

from shotrans.config import Config, ServiceConfig
from shotrans.services import ConnResult, ModelsResult
from shotrans.ui.glossary_page import GlossaryPage
from shotrans.ui.hotkey_edit import HotkeyEdit
from shotrans.ui.settings_window import SettingsWindow
from tests.ui_fakes import FakeApp

PRIMARY = ServiceConfig(preset="deepseek", base_url="https://api.deepseek.com", api_key="sk-x",
                        model="deepseek-flash", thinking_off=True, thinking_style="deepseek")
CFG = Config(primary=PRIMARY, hotkey="Ctrl+Alt+Shift+F10", clip_hotkey="Ctrl+Alt+Shift+F9")


@pytest.fixture
def make(qtbot, tmp_path, track):
    def _make(config=CFG):
        (tmp_path / "术语表.txt").write_text("depression => 抑郁\n", encoding="utf-8")
        app = FakeApp(tmp_path, config)
        w = SettingsWindow(app, tester=lambda svc: ConnResult("ok", 1.0, "", "好", "m"),
                           lister=lambda u, k: ModelsResult("ok", ("m",)), confirm=lambda issues: True,
                           warn=lambda text: None, open_path=lambda p: None, open_url=lambda u: None,
                           autostart_state=lambda: False)
        track(w)
        w.show()
        return w, app
    return _make


def test_loads_and_saves_clip_hotkey(make):
    w, app = make()
    assert w.clip_edit.spec() == "Ctrl+Alt+Shift+F9"
    w.clip_edit.set_spec("Ctrl+Alt+Shift+F8")
    assert w.apply() and app.saved[-1].clip_hotkey == "Ctrl+Alt+Shift+F8"


def test_off_and_default_buttons(make):
    w, app = make()
    w.btn_clip_off.click()
    assert w.clip_edit.spec() == "" and w.clip_edit.status.text() == "不用这个功能"
    assert w.apply() and app.saved[-1].clip_hotkey == ""
    w.btn_clip_default.click()
    assert w.clip_edit.spec() == "Ctrl+Alt+C"


def test_taken_new_clip_blocks_save(make):
    w, app = make()
    app.taken_hotkeys.add("Ctrl+Alt+Shift+F7")
    w.show_page("general")
    w.clip_edit.set_spec("Ctrl+Alt+Shift+F7")
    assert not w.apply() and app.saved == [] and w.tabs.currentIndex() == 1


def test_taken_unchanged_clip_still_saves(make):
    cfg = replace(CFG, clip_hotkey="Ctrl+Alt+Shift+F7")
    w, app = make(cfg)
    app.taken_hotkeys.add("Ctrl+Alt+Shift+F7")
    w.clip_edit.set_spec("Ctrl+Alt+Shift+F7")  # 启动时就被占、用户没动：照常保存（应用时再试注册）
    assert w.apply()


def test_same_as_capture_blocked(make):
    w, app = make()
    w.clip_edit.set_spec("Ctrl+Alt+Shift+F10")
    assert not w.apply() and app.saved == [] and w.tabs.currentIndex() == 1


def test_hotkey_edit_empty_not_allowed_by_default(qtbot):
    e = HotkeyEdit(lambda s: True)
    qtbot.addWidget(e)
    e.set_spec("")
    assert e.invalid and not e.is_valid()
    e2 = HotkeyEdit(lambda s: True, allow_empty=True)
    qtbot.addWidget(e2)
    e2.set_spec("")
    assert not e2.invalid and e2.is_valid()


def test_glossary_page_merge_entry(qtbot, tmp_path):
    p = tmp_path / "g.txt"
    p.write_text("# 注释\ndepression => 抑郁\n", encoding="utf-8")
    page = GlossaryPage(p, confirm=lambda issues: True)
    qtbot.addWidget(page)
    page.doc.rows[1].dst = "抑郁症"  # 用户在设置里改了、还没保存
    page._dirty = True
    page.merge_entry("anhedonia", "快感缺失")
    assert page.is_dirty() and page.table.rowCount() == 3
    assert page.save()
    assert p.read_text(encoding="utf-8") == "# 注释\ndepression => 抑郁症\nanhedonia => 快感缺失\n"

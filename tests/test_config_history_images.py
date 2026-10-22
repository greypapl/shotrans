"""[history] save_images：默认开；读、写、校验、设置窗口的勾选框、托盘程序把它交给 History。"""
import pytest

from shotrans.config import Config, ConfigError, ServiceConfig, config_from_raw, load_config, validate, write_config
from shotrans.services import ConnResult, ModelsResult
from shotrans.ui.notice import NoticePopup
from shotrans.ui.settings_window import SettingsWindow
from tests.ui_fakes import FakeApp


def test_default_true():
    assert Config().history_save_images is True
    assert config_from_raw({"primary": {}}).history_save_images is True


def test_read_false():
    assert config_from_raw({"history": {"save_images": False}}).history_save_images is False


def test_read_rejects_non_bool():
    with pytest.raises(ConfigError, match="save_images"):
        config_from_raw({"history": {"save_images": 1}})


def test_validate_rejects_non_bool():
    errs = validate(Config(history_save_images="yes"))
    assert [(e.field, e.code) for e in errs] == [("[history] save_images", "type")]


@pytest.mark.parametrize("value", [True, False])
def test_round_trip(tmp_path, value):
    p = tmp_path / "config.toml"
    write_config(p, Config(history_save_images=value, history_limit=9))
    text = p.read_text(encoding="utf-8")
    assert f"save_images = {'true' if value else 'false'}" in text
    c = load_config(p)
    assert c.history_save_images is value and c.history_limit == 9


def test_legacy_file_defaults_true():
    assert config_from_raw({"vllm": {"api_key": ""}}).history_save_images is True


# ---------- 设置窗口 ----------
@pytest.fixture
def make(qtbot, tmp_path, track):
    def _make(config=Config(primary=ServiceConfig(base_url="https://api.deepseek.com", api_key="sk-x"))):
        (tmp_path / "术语表.txt").write_text("", encoding="utf-8")
        app = FakeApp(tmp_path, config)
        w = SettingsWindow(app, tester=lambda svc: ConnResult("ok", 1.0, "", "好", "m"),
                           lister=lambda u, k: ModelsResult("ok", ("m",)), confirm=lambda issues: True,
                           warn=lambda text: None, open_path=lambda p: None, open_url=lambda u: None,
                           autostart_state=lambda: False)
        track(w)
        w.show()
        return w, app
    return _make


def test_checkbox_loads_config(make):
    w, _ = make()
    assert w.history_images.isChecked() is True and w.history_images.text() == "保存截图图片"
    w, _ = make(Config(history_save_images=False))
    assert w.history_images.isChecked() is False


def test_checkbox_on_general_page(make):
    w, _ = make()
    assert w.tabs.widget(3).isAncestorOf(w.history_images)


def test_tip_mentions_folder(make):
    w, _ = make()
    assert "history_images" in w.history_images.toolTip()


# ---------- 托盘程序 ----------
@pytest.fixture
def tray_app(qapp, tmp_path, monkeypatch):
    from shotrans.app import ShotTransApp
    from shotrans.hotkey import GlobalHotkey
    monkeypatch.setattr(NoticePopup, "show_notice", lambda *a, **k: None)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    monkeypatch.setattr(GlobalHotkey, "unregister", lambda self: None)
    made = []

    def _make(text):
        (tmp_path / "config.toml").write_text(text, encoding="utf-8")

        class R:
            glossary = None

        a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router_factory=lambda c, g: R(),
                         window_factories={}, ocr_languages=lambda: ["en-US"])
        made.append(a)
        return a

    yield _make
    for a in made:
        a.shutdown()


def test_app_history_uses_images_dir_and_switch(tray_app, tmp_path):
    a = tray_app('[history]\nsave_images = false\n')
    assert a.history.images_dir == tmp_path / "history_images"
    assert a.history.save_images is False


def test_app_apply_config_updates_switch(tray_app):
    a = tray_app('[history]\nsave_images = false\n')
    a.apply_config(Config())
    assert a.history.save_images is True
    a.apply_config(Config(history_save_images=False))
    assert a.history.save_images is False


def test_checkbox_saves_field(make):
    w, app = make()
    w.history_images.setChecked(False)
    assert w.apply() is True
    assert app.saved[-1].history_save_images is False
    w.history_images.setChecked(True)
    assert w.apply() is True
    assert app.saved[-1].history_save_images is True

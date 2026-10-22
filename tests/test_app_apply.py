"""apply_config：重建路由（旧浮窗用旧路由）、换快捷键失败回滚、托盘文字、开机自启、语言；save/reload。"""
import sys

import pytest
from PIL import Image
from PySide6.QtCore import QRect

from shotrans import app as app_mod
from shotrans import autostart, i18n, paths
from shotrans.app import ShotTransApp
from shotrans.autostart import disable as REAL_DISABLE  # 导入时还没被 env 夹具打桩
from shotrans.autostart import enable as REAL_ENABLE
from shotrans.autostart import is_enabled as REAL_IS_ENABLED
from shotrans.config import Config, ConfigError, load_config
from shotrans.hotkey import GlobalHotkey
from shotrans.models import Box, OcrLine
from shotrans.overlay import OverlayWindow
from shotrans.ui.notice import NoticePopup

BASE = Config(hotkey="Ctrl+Alt+Shift+F10")
F11_VK = 0x7A


class FakeRouter:
    glossary = None

    def __init__(self, cfg):
        self.cfg = cfg

    def vllm_down(self):
        return False

    def translate(self, segments, direction, on_result, cancel=None):
        pass


@pytest.fixture
def env(qapp, monkeypatch):
    got = {"messages": [], "auto": [], "auto_on": False, "auto_ok": True, "reg": [], "unreg": []}
    monkeypatch.setattr(NoticePopup, "show_notice", lambda self, t, msg, *a, **k: got["messages"].append(msg))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: got["auto_on"])
    # 快捷方式在、且指向本程序（auto_ok = False：在，但指向别处，如移动过文件夹）
    monkeypatch.setattr(autostart, "points_to", lambda *a, **k: got["auto_on"] and got["auto_ok"])
    monkeypatch.setattr(autostart, "enable", lambda *a, **k: got["auto"].append(("enable", a)))
    monkeypatch.setattr(autostart, "disable", lambda *a, **k: got["auto"].append(("disable", a)))

    def reg(self):
        got["reg"].append(self.vk)
        self.registered = self.vk != F11_VK  # F11 当作被占用
        return self.registered

    def unreg(self):
        got["unreg"].append(self.vk)
        self.registered = False

    monkeypatch.setattr(GlobalHotkey, "register", reg)
    monkeypatch.setattr(GlobalHotkey, "unregister", unreg)
    monkeypatch.setattr(OverlayWindow, "_close_if_inactive", lambda self: None)
    return got


@pytest.fixture
def make(qapp, env, tmp_path):
    made = []

    def _make(factory=lambda c, g: FakeRouter(c)):
        (tmp_path / "config.toml").write_text('[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n', encoding="utf-8")
        a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [OcrLine("Hi", Box(1, 1, 20, 10))],
                         router_factory=factory, window_factories={}, ocr_languages=lambda: ["en-US", "zh-Hans-CN"])
        made.append(a)
        return a

    yield _make
    for a in made:
        a.shutdown()


def test_router_rebuilt_old_overlay_keeps_old(make):
    a = make()
    old = a.router
    a.on_captured(Image.new("RGB", (100, 50), "white"), QRect(300, 300, 100, 50))
    w = a.overlays[0]
    w.pinned = True
    new_cfg = Config(hotkey="Ctrl+Alt+Shift+F10", history_limit=7)
    assert a.apply_config(new_cfg) == []
    assert a.router is not old and a.router.cfg == new_cfg
    assert w._router is old  # 进行中的会话不受影响
    a.on_captured(Image.new("RGB", (100, 50), "white"), QRect(320, 320, 100, 50))
    assert a.overlays[-1]._router is a.router
    assert w in a.overlays and w._router is old  # 新截图也不能把钉住的旧浮窗换成新路由（变异验证时补的）
    assert a.history.limit == 7


def test_hotkey_swap_success(make, env, qapp):
    a = make()
    old = a.hotkey
    assert a.apply_config(Config(hotkey="Ctrl+Alt+Shift+F12")) == []
    assert a.hotkey is not old and a.hotkey.vk == 0x7B and a.hotkey.registered
    assert a.config.hotkey == "Ctrl+Alt+Shift+F12"
    assert env["unreg"] == [0x79]  # 先注销旧键（F10）


def test_hotkey_swap_failure_rolls_back(make, env):
    a = make()
    old = a.hotkey
    failed = a.apply_config(Config(hotkey="Ctrl+Alt+Shift+F11", language="zh"))
    assert failed == ["快捷键 Ctrl+Alt+Shift+F11（被占用，已恢复原快捷键）"]
    assert a.hotkey is old and old.registered is True  # 旧键重新注册上了
    assert a.config.hotkey == "Ctrl+Alt+Shift+F10"
    assert env["messages"][-1] == "以下设置没有生效：快捷键 Ctrl+Alt+Shift+F11（被占用，已恢复原快捷键）"


def test_same_hotkey_not_reregistered(make, env):
    a = make()
    before = list(env["reg"])
    a.apply_config(Config(hotkey="Ctrl+Alt+Shift+F10", history_limit=9))
    assert env["reg"] == before and env["unreg"] == []


def test_tray_texts_follow_new_hotkey(make):
    a = make()
    a.apply_config(Config(hotkey="Ctrl+Alt+Shift+F12"))
    assert a.tray.toolTip() == "截图翻译（Ctrl+Alt+Shift+F12）"
    assert a._menu.actions()[0].text() == "截图翻译（Ctrl+Alt+Shift+F12）"


def test_autostart_enabled(make, env):
    a = make()
    a.apply_config(Config(hotkey=BASE.hotkey, autostart=True))
    kind, args = env["auto"][0]
    assert kind == "enable" and args[0].name == "pythonw.exe" and args[1] == "-m shotrans --autostart"


def test_apply_autostart_uses_program_dir(make, env):
    a = make()
    a.apply_config(Config(hotkey=BASE.hotkey, autostart=True))
    assert env["auto"][0][1][2] == paths.program_dir()


def test_autostart_disabled(make, env):
    env["auto_on"] = True
    a = make()
    a.apply_config(Config(hotkey=BASE.hotkey, autostart=False))
    assert env["auto"] == [("disable", ())]


def test_autostart_already_matching_untouched(make, env):
    env["auto_on"] = True
    a = make()
    a.apply_config(Config(hotkey=BASE.hotkey, autostart=True))
    assert env["auto"] == []


def test_autostart_none_untouched(make, env):
    a = make()
    a.apply_config(Config(hotkey=BASE.hotkey, autostart=None))
    assert env["auto"] == []


def test_autostart_failure_reported_others_still_applied(make, env, monkeypatch):
    def boom(*a, **k):
        raise OSError("拒绝访问")

    monkeypatch.setattr(autostart, "enable", boom)
    a = make()
    failed = a.apply_config(Config(hotkey="Ctrl+Alt+Shift+F12", autostart=True, language="en"))
    assert failed == ["start with Windows"]  # 第 5 步换成英文后才汇总提示
    assert a.config.hotkey == "Ctrl+Alt+Shift+F12" and i18n.language() == "en"


def test_router_failure_reported_others_still_applied(make):
    calls = []

    def factory(c, g):
        calls.append(c)
        if len(calls) > 1:
            raise RuntimeError("坏了")
        return FakeRouter(c)

    a = make(factory)
    old = a.router
    failed = a.apply_config(Config(hotkey="Ctrl+Alt+Shift+F12"))
    assert failed == ["翻译服务"] and a.router is old and a.config.hotkey == "Ctrl+Alt+Shift+F12"


def test_language_switch_retranslates_tray(make):
    a = make()
    a.apply_config(Config(hotkey=BASE.hotkey, language="en"))
    texts = [x.text() for x in a._menu.actions() if not x.isSeparator()]
    assert texts == ["Translate a screenshot (Ctrl+Alt+Shift+F10)", "History…", "Settings…", "Diagnostics…",
                     "Reload config", "About…", "Quit"]


def test_save_config_writes_and_applies(make, tmp_path):
    a = make()
    a.needs_setup = True
    new = Config(hotkey="Ctrl+Alt+Shift+F12", history_limit=33)
    assert a.save_config(new) == []
    assert load_config(tmp_path / "config.toml") == new
    assert a.needs_setup is False and a.config.history_limit == 33


def test_save_config_error_propagates_and_changes_nothing(make, tmp_path):
    a = make()
    before = (tmp_path / "config.toml").read_text(encoding="utf-8")
    with pytest.raises(ConfigError):
        a.save_config(Config(hotkey="Q"))
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == before
    assert a.config.hotkey == "Ctrl+Alt+Shift+F10"


def test_reload_config_success(make, tmp_path):
    a = make()
    (tmp_path / "config.toml").write_text('[hotkey]\ncapture = "Ctrl+Alt+Shift+F12"\n', encoding="utf-8")
    assert a.reload_config() is True and a.config.hotkey == "Ctrl+Alt+Shift+F12"


def test_reload_config_failure_keeps_current(make, env, tmp_path):
    a = make()
    (tmp_path / "config.toml").write_text("[hotkey\n", encoding="utf-8")
    assert a.reload_config() is False
    assert a.config.hotkey == "Ctrl+Alt+Shift+F10"
    assert env["messages"][-1].startswith("重新加载配置失败，仍使用当前配置：配置文件格式错误")


def test_hotkey_free_own_hotkey_without_probe(make, monkeypatch):
    a = make()
    monkeypatch.setattr(app_mod, "try_register", lambda spec: pytest.fail("自己的快捷键不该再试注册"))
    assert a.hotkey_free("ctrl+alt+shift+f10") is True


def test_hotkey_free_other_uses_probe(make, monkeypatch):
    a = make()
    monkeypatch.setattr(app_mod, "try_register", lambda spec: spec != "Ctrl+Alt+Shift+F12")
    assert a.hotkey_free("Ctrl+Alt+Shift+F12") is False
    assert a.hotkey_free("Ctrl+Alt+Shift+F9") is True


def test_hotkey_free_invalid_spec_false(make):
    assert make().hotkey_free("Hyper+Q") is False


def test_own_hotkey(make):
    a = make()
    assert a.own_hotkey() == "Ctrl+Alt+Shift+F10"
    a.hotkey.registered = False
    assert a.own_hotkey() is None


def test_apply_autostart_frozen_targets_exe(make, env, monkeypatch, tmp_path):
    exe = tmp_path / "dist" / "截图翻译.exe"
    a = make()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    a.apply_config(Config(hotkey=BASE.hotkey, autostart=True))
    kind, args = env["auto"][0]
    assert kind == "enable" and args == (exe, "--autostart", exe.parent.resolve())


# ---------- 快捷方式指向别处时重写；试用模式不动「启动」文件夹 ----------
def test_autostart_link_points_elsewhere_is_rewritten(make, env):
    """移动了程序文件夹 / 从源码版换成 exe：快捷方式还在但指向别处，开着自启再保存一次就重写。"""
    env["auto_on"], env["auto_ok"] = True, False
    a = make()
    a.apply_config(Config(hotkey=BASE.hotkey, autostart=True))
    kind, args = env["auto"][0]
    assert kind == "enable" and args[0].name == "pythonw.exe" and args[1] == "-m shotrans --autostart"


def test_trial_mode_keeps_existing_link_when_off(make, env, monkeypatch):
    monkeypatch.setenv("SHOTRANS_TRIAL", "1")
    env["auto_on"] = True
    a = make()
    failed = a.apply_config(Config(hotkey=BASE.hotkey, autostart=False))
    assert env["auto"] == [] and failed == []


def test_trial_mode_creates_no_link_when_on(make, env, monkeypatch):
    monkeypatch.setenv("SHOTRANS_TRIAL", "1")
    a = make()
    failed = a.apply_config(Config(hotkey=BASE.hotkey, autostart=True))
    assert env["auto"] == [] and failed == []


def test_trial_wizard_unticked_keeps_real_link(make, env, monkeypatch):
    """终审 I1 的场景：真快捷方式已在（这里在测试自己的「启动」临时目录里），试用模式下引导以不勾完成 → 还在。"""
    monkeypatch.setattr(autostart, "is_enabled", REAL_IS_ENABLED)
    monkeypatch.setattr(autostart, "disable", REAL_DISABLE)
    REAL_ENABLE(*autostart.launch_command())
    lnk = autostart.startup_dir() / autostart.LINK_NAME
    assert lnk.exists()
    monkeypatch.setenv("SHOTRANS_TRIAL", "1")
    a = make()
    assert a.save_config(Config(hotkey=BASE.hotkey, autostart=False)) == []
    assert lnk.exists()

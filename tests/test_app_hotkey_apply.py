"""apply_config 的快捷键一步（评审第 1 轮）：同一组合重新注册、写错的快捷键不碰旧键、新旧都失败、
气泡指向快捷键页；reload_config 接住 OSError。每条用例自己决定 register 的结果。"""
import pytest
from PySide6.QtWidgets import QWidget

from shotrans import app as app_mod
from shotrans import autostart, i18n
from shotrans.app import ShotTransApp
from shotrans.config import Config
from shotrans.hotkey import GlobalHotkey
from shotrans.ui.notice import NoticePopup

F10 = "Ctrl+Alt+Shift+F10"


class R:
    glossary = None

    def vllm_down(self):
        return False


@pytest.fixture
def env(qapp, monkeypatch):
    got = {"messages": [], "auto": [], "reg": [], "unreg": [], "opened": [], "answers": []}
    monkeypatch.setattr(NoticePopup, "show_notice", lambda self, t, msg, *a, **k: got["messages"].append(msg))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(autostart, "enable", lambda *a, **k: got["auto"].append("enable"))
    monkeypatch.setattr(autostart, "disable", lambda *a, **k: got["auto"].append("disable"))

    def reg(self):  # 依次取 answers 里的结果；取完了一律成功
        got["reg"].append(self.vk)
        self.registered = got["answers"].pop(0) if got["answers"] else True
        return self.registered

    def unreg(self):
        got["unreg"].append(self.vk)
        self.registered = False

    monkeypatch.setattr(GlobalHotkey, "register", reg)
    monkeypatch.setattr(GlobalHotkey, "unregister", unreg)
    return got


@pytest.fixture
def make(qapp, env, tmp_path):
    made = []

    def _make(answers=()):
        env["answers"][:] = list(answers)
        (tmp_path / "config.toml").write_text(f'[hotkey]\ncapture = "{F10}"\n', encoding="utf-8")

        def win(kind):
            def build(page):
                env["opened"].append((kind, page))
                return QWidget()
            return build

        a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router_factory=lambda c, g: R(),
                         window_factories={k: win(k) for k in ("settings", "doctor", "wizard")},
                         ocr_languages=lambda: ["en-US", "zh-Hans-CN"])
        made.append(a)
        env["messages"].clear()
        return a

    yield _make
    for a in made:
        a.shutdown()


def test_same_combo_reregistered_after_startup_failure(make, env):
    a = make(answers=[False])  # 启动时被占用
    assert a.own_hotkey() is None
    failed = a.apply_config(Config(hotkey=F10))  # 用户关掉了占用的程序，原样保存
    assert failed == [] and a.hotkey.registered is True and a.own_hotkey() == F10
    assert env["reg"] == [0x79, 0x79]


def test_same_combo_other_case_not_reregistered(make, env):
    a = make()
    failed = a.apply_config(Config(hotkey="ctrl+alt+shift+f10"))
    assert failed == [] and env["reg"] == [0x79] and env["unreg"] == []
    assert a.hotkey.registered is True


def test_invalid_hotkey_keeps_old_and_other_steps_run(make, env):
    a = make()
    old = a.hotkey
    failed = a.apply_config(Config(hotkey="Q", autostart=True, language="en", history_limit=5))
    assert failed == ["hotkey Q (taken; the old hotkey was restored)"]
    assert a.hotkey is old and old.registered is True and env["unreg"] == []
    assert a.config.hotkey == F10 and a.history.limit == 5
    assert env["auto"] == ["enable"] and i18n.language() == "en"
    assert a._menu.actions()[0].text() == f"Translate a screenshot ({F10})"


def test_new_and_old_both_fail_reports_no_hotkey(make, env):
    a = make()
    env["answers"][:] = [False, False]  # 新键失败，旧键也没能重新注册
    failed = a.apply_config(Config(hotkey="Ctrl+Alt+Shift+F12"))
    assert failed == ["快捷键 Ctrl+Alt+Shift+F12（注册失败，原快捷键也没能恢复，目前没有可用的快捷键）"]
    assert a.own_hotkey() is None
    a.notice.clicked.emit()
    assert env["opened"] == [("settings", "hotkey")]


def test_restored_failure_bubble_opens_hotkey_page(make, env):
    a = make()
    env["answers"][:] = [False]  # 新键失败，旧键恢复成功
    assert a.apply_config(Config(hotkey="Ctrl+Alt+Shift+F12")) == ["快捷键 Ctrl+Alt+Shift+F12（被占用，已恢复原快捷键）"]
    a.notice.clicked.emit()
    assert env["opened"] == [("settings", "hotkey")]


def test_non_hotkey_failure_bubble_has_no_target(make, env):
    a = make()

    def boom(c, g):
        raise RuntimeError("坏了")

    a._router_factory = boom
    assert a.apply_config(Config(hotkey=F10)) == ["翻译服务"]
    a.notice.clicked.emit()
    assert env["opened"] == []


def test_reload_oserror_keeps_current(make, env, monkeypatch):
    a = make()

    def denied(path):
        raise PermissionError("拒绝访问")

    monkeypatch.setattr(app_mod, "load_config", denied)
    assert a.reload_config() is False
    assert a.config.hotkey == F10
    assert env["messages"] == ["重新加载配置失败，仍使用当前配置：拒绝访问"]

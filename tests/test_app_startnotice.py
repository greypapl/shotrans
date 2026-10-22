"""手动启动时的「已在后台运行」气泡；开机自启（--autostart）不弹；首次运行不弹；
启动时已有别的提醒就不弹（托盘气泡会互相顶掉，不能把重要提醒挤掉）；「已在运行」提示带上快捷键。"""
import uuid

import pytest
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon, QWidget

from shotrans import app as app_mod
from shotrans import autostart
from shotrans.app import ShotTransApp, main
from shotrans.config import Config
from shotrans.hotkey import GlobalHotkey
from shotrans.migrate import MigrateResult
from shotrans.runtime import SingleInstance
from shotrans.ui.notice import NoticePopup

CFG = '[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n'
STARTED_ZH = "截图翻译已在后台运行，按 Ctrl+Alt+Shift+F10 截图翻译；图标在右下角托盘里，可能收在 ^ 里面。"


class QuietRouter:
    glossary = None

    def vllm_down(self):
        return False


@pytest.fixture
def env(qapp, monkeypatch):
    got = {"msgs": [], "opened": [], "reg_ok": True}
    monkeypatch.setattr(NoticePopup, "show_notice",
                        lambda self, title, msg, icon=None, *a, **k: got["msgs"].append((title, msg, icon)))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)

    def reg(self):
        self.registered = got["reg_ok"]
        return self.registered

    monkeypatch.setattr(GlobalHotkey, "register", reg)
    return got


@pytest.fixture
def make(qapp, env, tmp_path):
    made = []

    def _make(config=CFG, langs=("en-US", "zh-Hans-CN"), router="quiet", **kw):
        if config is not None:
            (tmp_path / "config.toml").write_text(config, encoding="utf-8")

        def history(page):
            env["opened"].append(("history", page))
            return QWidget()

        a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router=QuietRouter() if router == "quiet" else router,
                         router_factory=lambda c, g: QuietRouter(),
                         window_factories={"history": history, "wizard": lambda page: QWidget(),
                                           "settings": lambda page: QWidget(), "doctor": lambda page: QWidget()},
                         ocr_languages=lambda: list(langs), **kw)
        made.append(a)
        return a

    yield _make
    for a in made:
        a.shutdown()


def test_manual_start_shows_notice_with_real_hotkey(make, env):
    a = make()
    assert env["msgs"] == []  # 构造时不弹：只有 main 判断不是开机自启才调用
    a.announce_start()
    assert env["msgs"] == [("截图翻译", STARTED_ZH, QSystemTrayIcon.MessageIcon.Information)]
    assert a._notice_target == "history"


def test_notice_in_english(make, env):
    a = make('[general]\nlanguage = "en"\n' + CFG)
    a.announce_start()
    assert env["msgs"][-1][1] == ("ShotTrans is running in the background. Press Ctrl+Alt+Shift+F10 to capture and "
                                  "translate; the icon is in the system tray, possibly hidden under ^.")


def test_clicking_notice_opens_history(make, env):
    a = make()
    a.announce_start()
    a.notice.clicked.emit()
    assert env["opened"] == [("history", None)]


def test_first_run_no_startup_notice(make, env):
    a = make(config=None)
    assert a.needs_setup is True
    a.announce_start()
    assert env["msgs"] == []


def test_hotkey_taken_wins_no_second_balloon(make, env):
    env["reg_ok"] = False
    a = make()
    a.announce_start()
    assert [m[1] for m in env["msgs"]] == ["快捷键 Ctrl+Alt+Shift+F10 被别的软件占用了，点这里换一个。"]
    assert a._notice_target == "settings:hotkey"  # 点气泡仍然去改快捷键，没被启动提示改成历史记录


def test_ocr_missing_warning_not_clobbered(make, env):
    a = make(langs=["ja"])
    a.announce_start()
    assert len(env["msgs"]) == 1 and env["msgs"][0][1].startswith("没有安装 OCR 语言包")
    assert a._notice_target == "doctor"


def test_migration_notice_not_clobbered(make, env, tmp_path):
    a = make(migration=MigrateResult("migrated"))
    a.announce_start()
    assert len(env["msgs"]) == 1 and "迁移" in env["msgs"][0][1]


def test_glossary_failure_not_clobbered(make, env, monkeypatch, tmp_path):
    def bad(path):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "坏了")

    monkeypatch.setattr(app_mod, "load_glossary", bad)
    a = make(router=None)
    a.announce_start()
    assert len(env["msgs"]) == 1 and env["msgs"][0][1].startswith("术语表读取失败")


# ---------- 旧快捷方式（没有 --autostart）在保存设置时被重写（真 COM，只在测试自己的「启动」临时目录） ----------
def test_old_link_without_marker_rewritten_on_apply(make, env):
    target, args, workdir = autostart.launch_command()
    autostart.enable(target, "-m shotrans", workdir)  # 旧版本建的快捷方式
    a = make()
    assert a.apply_config(Config(hotkey=a.config.hotkey, autostart=True)) == []
    assert autostart.read_link() == (str(target), "-m shotrans --autostart", str(workdir))


# ---------- main：--autostart 不弹、手动启动弹；--autostart 不交给 Qt ----------
@pytest.fixture
def stub_main(qapp, monkeypatch):
    seen = {"announced": 0, "argv": []}
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a: pytest.fail(f"不该报错：{a}")))

    class Stub:
        def __init__(self, q, home, migration=None):
            pass

        def announce_start(self):
            seen["announced"] += 1

        def shutdown(self):
            pass

    monkeypatch.setattr(app_mod, "ShotTransApp", Stub)
    qapp.exec = lambda: 0
    yield seen
    del qapp.exec


def test_main_manual_start_announces(stub_main, tmp_path):
    assert main(["prog"], home=tmp_path) == 0
    assert stub_main["announced"] == 1


def test_main_autostart_does_not_announce(stub_main, tmp_path):
    assert main(["prog", "--autostart"], home=tmp_path) == 0
    assert stub_main["announced"] == 0


def test_main_autostart_arg_not_passed_to_qt(stub_main, tmp_path, monkeypatch, qapp):
    class FakeQApplication:
        @staticmethod
        def instance():
            return None

        def __new__(cls, argv):
            stub_main["argv"].append(list(argv))
            return qapp

    monkeypatch.setattr(app_mod, "QApplication", FakeQApplication)
    assert main(["prog", "--autostart", "-x"], home=tmp_path) == 0
    assert stub_main["argv"] == [["prog", "-x"]] and stub_main["announced"] == 0


def test_main_selftest_still_dispatched_with_autostart(monkeypatch):
    from shotrans import selftest
    got = []
    monkeypatch.setattr(selftest, "selftest_main", lambda argv: got.append(argv) or 5)
    assert main(["prog", "--autostart", "--selftest", "r.json"]) == 5
    assert got == [["prog", "--autostart", "--selftest", "r.json"]]


# ---------- 「已在运行」提示带快捷键（读不到就不带） ----------
@pytest.fixture
def shown(monkeypatch):
    got = []
    monkeypatch.setattr(app_mod, "setup_logging", lambda d: None)
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda p, title, text: got.append(text)))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a: pytest.fail("不该报错")))
    monkeypatch.setattr(app_mod, "ShotTransApp", lambda *a, **k: pytest.fail("不该走到建托盘程序"))
    return got


def _second_launch(home):
    name = f"Local\\shotrans-test-{uuid.uuid4().hex}"
    holder = SingleInstance(name)
    assert holder.acquire()
    try:
        return main([], home=home, instance_name=name)
    finally:
        holder.release()


RUNNING_HK = "截图翻译已在运行，按 {} 截图翻译（右下角托盘里蓝色的「译」字图标，可能收在 ^ 里面）。"
RUNNING_PLAIN = "截图翻译已在运行（右下角托盘里蓝色的「译」字图标，可能收在 ^ 里面）。"


def test_already_running_names_hotkey(qapp, tmp_path, shown):
    (tmp_path / "config.toml").write_text(CFG, encoding="utf-8")
    assert _second_launch(tmp_path) == 0
    assert shown == [RUNNING_HK.format("Ctrl+Alt+Shift+F10")]


def test_already_running_english_names_hotkey(qapp, tmp_path, shown):
    (tmp_path / "config.toml").write_text('[general]\nlanguage = "en"\n' + CFG, encoding="utf-8")
    assert _second_launch(tmp_path) == 0
    assert shown == ["ShotTrans is already running. Press Ctrl+Alt+Shift+F10 to capture and translate "
                     "(the blue tray icon at the bottom right, possibly hidden under ^)."]


def test_already_running_default_hotkey_when_not_written(qapp, tmp_path, shown):
    (tmp_path / "config.toml").write_text('[general]\nlanguage = "zh"\n', encoding="utf-8")
    assert _second_launch(tmp_path) == 0
    assert shown == [RUNNING_HK.format("Ctrl+Alt+Q")]


def test_already_running_without_config_has_no_hotkey(qapp, tmp_path, shown):
    assert _second_launch(tmp_path) == 0
    assert shown == [RUNNING_PLAIN]


def test_already_running_broken_config_has_no_hotkey(qapp, tmp_path, shown):
    (tmp_path / "config.toml").write_text("[hotkey\n", encoding="utf-8")
    assert _second_launch(tmp_path) == 0
    assert shown == [RUNNING_PLAIN]


@pytest.mark.parametrize("text", ['[hotkey]\ncapture = "Q"\n', '[hotkey]\ncapture = 3\n'])
def test_already_running_invalid_hotkey_has_no_hotkey(qapp, tmp_path, shown, text):
    (tmp_path / "config.toml").write_text(text, encoding="utf-8")
    assert _second_launch(tmp_path) == 0
    assert shown == [RUNNING_PLAIN]

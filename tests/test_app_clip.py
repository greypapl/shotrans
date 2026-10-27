"""托盘程序里的「复制后翻译」与「添加术语」：第二个快捷键的注册/占用/应用配置、读剪贴板弹小窗、
新开关旧、截图时一起藏、浮窗加术语后刷新设置窗口的术语表页。register 一律是替身，不碰真快捷键。"""
import pytest
from PySide6.QtWidgets import QApplication, QWidget

from shotrans import autostart
from shotrans.app import CLIP_HOTKEY_ID, ShotTransApp
from shotrans.config import Config
from shotrans.hotkey import GlobalHotkey, parse_hotkey
from shotrans.ui.clip_window import ClipWindow
from shotrans.ui.notice import NoticePopup

F10 = "Ctrl+Alt+Shift+F10"
F9 = "Ctrl+Alt+Shift+F9"


class R:
    glossary = None

    def vllm_down(self):
        return False

    def vllm_last_error_kind(self):
        return None

    def translate(self, segments, direction, on_result, cancel=None):
        pass


@pytest.fixture
def env(qapp, monkeypatch):
    got = {"messages": [], "reg": [], "unreg": [], "taken": set(), "opened": []}
    monkeypatch.setattr(NoticePopup, "show_notice", lambda self, t, msg, *a, **k: got["messages"].append(msg))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)

    def reg(self):
        got["reg"].append((self.id, self.vk))
        self.registered = (self.mods, self.vk) not in got["taken"]
        return self.registered

    def unreg(self):
        got["unreg"].append((self.id, self.vk))
        self.registered = False

    monkeypatch.setattr(GlobalHotkey, "register", reg)
    monkeypatch.setattr(GlobalHotkey, "unregister", unreg)
    return got


@pytest.fixture
def make(qapp, env, tmp_path):
    made = []

    def _make(clip=F9, setup=True):
        if setup:
            (tmp_path / "config.toml").write_text(f'[hotkey]\ncapture = "{F10}"\nclipboard = "{clip}"\n',
                                                  encoding="utf-8")

        def win(kind):
            def build(page):
                env["opened"].append((kind, page))
                return QWidget()
            return build

        a = ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router_factory=lambda c, g: R(),
                         window_factories={k: win(k) for k in ("settings", "doctor", "wizard")},
                         ocr_languages=lambda: ["en-US", "zh-Hans-CN"])
        made.append(a)
        return a

    yield _make
    for a in made:
        a.shutdown()


def test_registers_second_hotkey(make, env):
    a = make()
    assert (CLIP_HOTKEY_ID, parse_hotkey(F9)[1]) in env["reg"] and a.clip_hotkey.registered
    assert a.hotkey_free(F9) and a.hotkey_free(F10)  # 自己占着的两个键都算「可用」
    assert a.clip_hotkey.callback == a.start_clip


def test_off_registers_nothing(make, env):
    a = make(clip="")
    assert a.clip_hotkey is None and all(i != CLIP_HOTKEY_ID for i, _ in env["reg"])
    assert [x.text() for x in a._menu.actions()][1] == "翻译剪贴板"


def test_taken_notifies(make, env):
    env["taken"].add(parse_hotkey(F9))
    a = make()
    assert a.clip_hotkey is not None and not a.clip_hotkey.registered
    assert any("复制后翻译" in m and F9 in m for m in env["messages"])


def test_apply_swaps_clip_hotkey(make, env):
    a = make()
    env["reg"].clear()
    failed = a.apply_config(Config(hotkey=F10, clip_hotkey="Ctrl+Alt+Shift+F8"))
    assert failed == [] and a.clip_hotkey.registered and a.config.clip_hotkey == "Ctrl+Alt+Shift+F8"
    assert (CLIP_HOTKEY_ID, parse_hotkey(F9)[1]) in env["unreg"]
    assert [x.text() for x in a._menu.actions()][1] == "翻译剪贴板（Ctrl+Alt+Shift+F8）"


def test_apply_same_clip_does_not_reregister(make, env):
    a = make()
    env["reg"].clear()
    assert a.apply_config(Config(hotkey=F10, clip_hotkey=F9.lower())) == []
    assert all(i != CLIP_HOTKEY_ID for i, _ in env["reg"])


def test_apply_turn_off(make, env):
    a = make()
    assert a.apply_config(Config(hotkey=F10, clip_hotkey="")) == [] and a.clip_hotkey is None


def test_apply_swap_two_keys(make, env, monkeypatch):
    """截图键和复制翻译键对调：先放掉旧的复制翻译键，截图键才注册得上。"""
    a = make()
    taken_now = {parse_hotkey(F9)}  # 模拟系统：已注册的键别人（包括自己另一个 id）注册不上
    live = {}

    def reg(self):
        key = (self.mods, self.vk)
        if key in live.values():
            self.registered = False
            return False
        live[self.id] = key
        self.registered = True
        return True

    def unreg(self):
        live.pop(self.id, None)
        self.registered = False

    monkeypatch.setattr(GlobalHotkey, "register", reg)
    monkeypatch.setattr(GlobalHotkey, "unregister", unreg)
    live.update({a.hotkey.id: (a.hotkey.mods, a.hotkey.vk), CLIP_HOTKEY_ID: taken_now.pop()})
    failed = a.apply_config(Config(hotkey=F9, clip_hotkey=F10))
    assert failed == [] and a.hotkey.registered and a.clip_hotkey.registered


def test_apply_clip_taken_reports_and_points_to_hotkey_page(make, env):
    a = make()
    env["taken"].add(parse_hotkey("Ctrl+Alt+Shift+F7"))
    env["messages"].clear()
    failed = a.apply_config(Config(hotkey=F10, clip_hotkey="Ctrl+Alt+Shift+F7"))
    assert failed == ["「复制后翻译」快捷键 Ctrl+Alt+Shift+F7（被占用）"]
    assert a._notice_target == "settings:hotkey"


def test_shutdown_unregisters_clip(make, env):
    a = make()
    a.shutdown()
    assert (CLIP_HOTKEY_ID, parse_hotkey(F9)[1]) in env["unreg"] and a.clip_hotkey is None


# ---------- start_clip ----------
def test_empty_clipboard_notice(make, env, fake_clipboard):
    a = make()
    fake_clipboard.setText("  \n ")
    a.start_clip()
    assert a.clip_window is None and "剪贴板里没有文字" in env["messages"][-1]


def test_too_long_notice(make, env, fake_clipboard):
    a = make()
    fake_clipboard.setText("字" * 20001)
    a.start_clip()
    assert a.clip_window is None and "20001" in env["messages"][-1]


def test_needs_setup_opens_wizard(make, env, fake_clipboard):
    a = make(setup=False)
    fake_clipboard.setText("hello")
    a.start_clip()
    assert a.clip_window is None and ("wizard", None) in env["opened"]


def test_opens_window_and_replaces_unpinned(make, env, fake_clipboard, qtbot):
    a = make()
    fake_clipboard.setText("Depression is a common\nmental disorder.")
    a.start_clip()
    w1 = a.clip_window
    assert isinstance(w1, ClipWindow) and w1 in a.overlays
    assert [s.text for s in w1.segments] == ["Depression is a common mental disorder."]
    closed = []
    w1.closed.connect(closed.append)
    a.start_clip()
    assert closed == [w1] and a.clip_window is not w1 and w1 not in a.overlays


def test_pinned_window_stays(make, env, fake_clipboard):
    a = make()
    fake_clipboard.setText("abc def")
    a.start_clip()
    w1 = a.clip_window
    w1.pinned = True
    a.start_clip()
    assert w1.isVisible() and a.clip_window is not w1 and len(a.overlays) == 2


def test_clipboard_not_written(make, fake_clipboard):
    a = make()
    fake_clipboard.setText("abc def")
    a.start_clip()
    assert fake_clipboard.text() == "abc def"


def test_hidden_before_grab(make, fake_clipboard):
    a = make()
    fake_clipboard.setText("abc def")
    a.start_clip()
    a._before_grab()
    assert not a.clip_window.isVisible()


def test_reloads_glossary(make, fake_clipboard, tmp_path):
    a = make()
    (tmp_path / "术语表.txt").write_text("anhedonia => 快感缺失\n", encoding="utf-8")
    fake_clipboard.setText("abc def")
    a.start_clip()
    assert a.router.glossary.mappings == (("anhedonia", "快感缺失"),)


# ---------- 浮窗加术语后的通知 ----------
class Page:
    def __init__(self, dirty, load_error=False):
        self.dirty, self.load_error, self.calls = dirty, load_error, []

    def is_dirty(self):
        return self.dirty

    def load(self):
        self.calls.append("load")

    def merge_entry(self, en, zh):
        self.calls.append(("merge", en, zh))


@pytest.mark.parametrize("dirty,expect", [(False, ["load"]), (True, [("merge", "a", "b")])])
def test_glossary_saved_refreshes_settings_page(make, monkeypatch, dirty, expect):
    a = make()
    page = Page(dirty)

    class W:
        glossary = page
    monkeypatch.setattr(a.windows, "get", lambda kind: W() if kind == "settings" else None)
    a.on_glossary_saved("a", "b")
    assert page.calls == expect


def test_glossary_saved_without_settings_window(make):
    make().on_glossary_saved("a", "b")  # 没开设置窗口：什么都不做、不报错


# ---------- 评审第 1 轮 ----------
def test_modal_dialog_blocks_hotkeys(make, monkeypatch, fake_clipboard):
    a = make()
    raised = []

    class Modal:
        def raise_(self):
            raised.append("raise")

        def activateWindow(self):
            raised.append("activate")

    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: Modal()))
    started = []
    monkeypatch.setattr(a.capturer, "start", lambda: started.append(1))
    fake_clipboard.setText("abc def")
    a.start_capture()
    a.start_clip()
    assert started == [] and a.clip_window is None and raised == ["raise", "activate"] * 2


def test_clip_ignored_while_selecting(make, monkeypatch, fake_clipboard):
    a = make()
    monkeypatch.setattr(type(a.capturer), "busy", property(lambda self: True))
    fake_clipboard.setText("abc def")
    a.start_clip()
    assert a.clip_window is None


def test_too_long_checked_before_splitting(make, env, monkeypatch, fake_clipboard):
    a = make()
    import shotrans.app as app_mod
    monkeypatch.setattr(app_mod, "split_paragraphs", lambda text: pytest.fail("不该分段"))
    fake_clipboard.setText("x-\ny" * 6000)
    a.start_clip()
    assert "24000" in env["messages"][-1]

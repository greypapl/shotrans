from dataclasses import replace

import pytest

from shotrans.config import Config
from shotrans.selection import ForegroundInfo, SelectionGesture, should_offer
from shotrans.terminal_detect import ControlInfo, TerminalProbe, embedded_terminal, terminal_editor


def test_selection_gestures():
    gesture = SelectionGesture(500)
    assert not gesture.feed("left_down", 0, 0, 10)
    assert gesture.feed("left_up", 0, 8, 3000)
    assert not gesture.feed("left_down", 0, 0, 3010)
    assert not gesture.feed("left_up", 0, 7, 3020)
    assert not gesture.feed("left_down", 0, 0, 3030)
    assert not gesture.feed("left_up", 0, 0, 4050)


def test_double_and_triple_click_and_other_buttons():
    gesture = SelectionGesture(500)
    for x, when, expected in ((0, 0, False), (4, 500, True), (8, 700, True)):
        assert not gesture.feed("left_down", x, 0, when)
        assert gesture.feed("left_up", x, 0, when + 10) is expected
    assert not gesture.feed("right_down", 8, 0, 750)
    assert not gesture.feed("left_down", 8, 0, 800)
    assert not gesture.feed("left_up", 8, 0, 810)
    assert not gesture.feed("left_down", 14, 0, 820)
    assert not gesture.feed("left_up", 14, 0, 830)


@pytest.mark.parametrize("changes", [
    {"pid": 0}, {"own": True}, {"fullscreen": True}, {"process": ""},
    {"window_class": "CabinetWClass"}, {"window_class": "Progman"}, {"window_class": "WorkerW"},
    {"window_class": "ConsoleWindowClass"}, {"window_class": "CASCADIA_HOSTING_WINDOW_CLASS"},
    {"window_class": "MiNtTy"}, {"window_class": "PuTTY"}, {"window_class": "VirtualConsoleClass"},
    {"process": "PwSh.EXE"}, {"process": "PHOTOSHOP.exe"},
])
def test_foreground_exclusions(changes):
    info = ForegroundInfo(hwnd=1, pid=2, process="reader.exe")
    config = Config(select_exclude=["Photoshop.EXE"])
    assert should_offer(info, config)
    assert not should_offer(replace(info, **changes), config)
    assert not should_offer(info, replace(config, select_enabled=False))
    assert not should_offer(info, config, busy=True)
    assert not should_offer(info, config, modal=True)


@pytest.mark.parametrize(("editor", "control"), [
    ("CODE.exe", ControlInfo(class_name="xterm-helper-textarea")),
    ("idea64.exe", ControlInfo(name="Terminal")),
    ("devenv.exe", ControlInfo(automation_id="TerminalControl")),
])
def test_embedded_terminal_markers(editor, control):
    assert embedded_terminal(editor, [ControlInfo()] * 5 + [control])
    assert not embedded_terminal(editor, [ControlInfo()] * 6 + [control])
    assert not embedded_terminal(editor, [ControlInfo(name="Editor", class_name="TextEditor")])
    assert not embedded_terminal(editor, [])
    assert not embedded_terminal("chrome.exe", [control])
    assert terminal_editor(editor)
    assert not terminal_editor("chrome.exe")


def test_only_editors_query_uia_and_timeout_drops_old_result(qtbot):
    from threading import Event

    unblock, calls, results = Event(), [], []

    def controls(pid):
        calls.append(pid)
        unblock.wait(2)
        return [ControlInfo(class_name="xterm")]

    probe = TerminalProbe(query=controls)
    try:
        probe.check("chrome.exe", 1, results.append)
        assert results == [False]
        assert calls == []
        probe.check("code.exe", 2, results.append)
        qtbot.waitUntil(lambda: len(results) == 2, timeout=800)
        assert results == [False, False]
        assert calls == [2]
    finally:
        unblock.set()
    probe.check("code.exe", 3, results.append)
    qtbot.waitUntil(lambda: len(results) == 3, timeout=800)
    assert results == [False, False, True]
    assert calls == [2, 3]


def test_uia_accepts_focused_renderer_when_ancestor_belongs_to_editor(monkeypatch):
    from types import SimpleNamespace

    from shotrans import terminal_detect

    def element(pid, class_name, parent=None):
        return SimpleNamespace(CurrentProcessId=pid, CurrentNativeWindowHandle=0, CurrentClassName=class_name,
                               CurrentName="", CurrentAutomationId="", parent=parent)

    parent = element(5, "editor")
    focused = element(6, "xterm", parent)
    walker = SimpleNamespace(GetParentElement=lambda item: item.parent)
    automation = SimpleNamespace(GetFocusedElement=lambda: focused, RawViewWalker=walker)
    monkeypatch.setattr(terminal_detect, "create_automation", lambda: automation)
    assert embedded_terminal("code.exe", terminal_detect._read_controls(5, 10))
    assert terminal_detect._read_controls(7, 10) == []

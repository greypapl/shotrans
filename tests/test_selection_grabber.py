import pytest

from shotrans.selection import SelectionGrabber, copy_keys


class Clipboard:
    def __init__(self, content):
        self.content = content
        self.serial = 10
        self.owner = 71

    def sequence(self):
        return self.serial

    def snapshot(self):
        return self.serial, self.content

    def take_and_restore(self, before, pid, content):
        if self.serial == before:
            return False, None
        if self.owner != pid:
            return True, None
        text = self.content["text/plain"]
        self.write(content)
        return True, text

    def discard(self, content):
        pass

    def write(self, content, owner=71):
        self.content = content
        self.serial += 1
        self.owner = owner


@pytest.mark.parametrize("before", [None, {"text/plain": "old", "text/html": "<b>old</b>"}])
def test_restore_clipboard_and_return_text(qtbot, before):
    clipboard = Clipboard(before)
    done = []

    def copy():
        clipboard.write({"text/plain": "selected"})
        return True

    grabber = SelectionGrabber(clipboard, copy)
    grabber.start(71, lambda: True, done.append)
    qtbot.waitUntil(lambda: bool(done))
    assert done == ["selected"]
    assert clipboard.content == before


@pytest.mark.parametrize("owner", [0, 74])
def test_keep_unattributed_clipboard_update(qtbot, owner):
    clipboard = Clipboard({"text/plain": "old"})
    done = []

    def copy():
        clipboard.write({"text/plain": "selected"}, owner)
        return True

    grabber = SelectionGrabber(clipboard, copy)
    grabber.start(71, lambda: True, done.append)
    qtbot.waitUntil(lambda: bool(done))
    assert done == [None]
    assert clipboard.content == {"text/plain": "selected"}


def test_keep_second_copy_from_same_app(qtbot):
    from types import SimpleNamespace

    clipboard = Clipboard({"text/plain": "old"})
    key_guard = SimpleNamespace(interrupted=False, start=lambda: True, stop=lambda: None)
    done = []

    def copy():
        clipboard.write({"text/plain": "selected"})
        return True

    grabber = SelectionGrabber(clipboard, copy, key_guard=key_guard)
    grabber.start(71, lambda: True, done.append)
    key_guard.interrupted = True
    clipboard.write({"text/plain": "user copy"})
    qtbot.waitUntil(lambda: bool(done))
    assert done == [None]
    assert clipboard.content == {"text/plain": "user copy"}


def test_shutdown_restores_copy_that_has_not_arrived_yet(qtbot):
    from PySide6.QtCore import QTimer

    clipboard = Clipboard({"text/plain": "old"})
    done = []

    def copy():
        QTimer.singleShot(40, lambda: clipboard.write({"text/plain": "selected"}))
        return True

    grabber = SelectionGrabber(clipboard, copy)
    grabber.start(71, lambda: True, done.append)
    grabber.shutdown()
    assert done == ["selected"]
    assert clipboard.content == {"text/plain": "old"}


def test_timeout_preserves_old_clipboard(qtbot):
    clipboard = Clipboard({"text/plain": "old"})
    done = []
    grabber = SelectionGrabber(clipboard, lambda: True)
    grabber.start(71, lambda: True, done.append)
    qtbot.waitUntil(lambda: bool(done), timeout=1000)
    assert done == [None]
    assert clipboard.serial == 10


def test_changed_target_is_never_sent_copy():
    clipboard = Clipboard(None)
    sent, done = [], []
    grabber = SelectionGrabber(clipboard, lambda: sent.append(True))
    grabber.start(71, lambda: False, done.append)
    assert done == [None]
    assert sent == []


def test_releases_shift_and_right_alt_before_copy():
    assert copy_keys(lambda key: 0x8000 if key in (0xA0, 0xA5) else 0) == [
        (0xA0, 2), (0xA5, 3), (0x11, 0), (ord("C"), 0), (ord("C"), 2), (0x11, 2),
    ]

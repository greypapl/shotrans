"""WindowManager：每种窗口只开一个。"""
import pytest
from PySide6.QtWidgets import QWidget

from shotrans.ui.windows import WindowManager


class Win(QWidget):
    def __init__(self, page=None):
        super().__init__()
        self.pages = [page]

    def show_page(self, page):
        self.pages.append(page)


@pytest.fixture
def wm(qtbot):
    made = []

    def factory(page):
        w = Win(page)
        made.append(w)
        return w

    m = WindowManager({"settings": factory, "about": lambda page: Win(page)})
    m.made = made
    yield m
    m.close_all()


def test_opened_once(wm):
    a = wm.show("settings")
    b = wm.show("settings")
    assert a is b and len(wm.made) == 1 and a.isVisible()


def test_page_forwarded_to_existing(wm):
    w = wm.show("settings")
    wm.show("settings", "hotkey")
    assert w.pages == [None, "hotkey"]


def test_page_given_to_new_window(wm):
    assert wm.show("settings", "glossary").pages == ["glossary"]


def test_reopened_after_close(qtbot, wm):
    first = wm.show("settings")
    first.close()
    assert wm.get("settings") is None  # 关了（等待删除中）就不算开着
    second = wm.show("settings")
    assert second is not first and len(wm.made) == 2


def test_late_destroy_of_old_does_not_forget_new(qtbot, wm):
    first = wm.show("settings")
    first.close()
    second = wm.show("settings")
    qtbot.waitUntil(lambda: not _valid(first), timeout=3000)  # 旧窗口真正被删
    assert wm.get("settings") is second


def _valid(w):
    import shiboken6
    return shiboken6.isValid(w)


def test_unknown_kind(wm):
    with pytest.raises(KeyError):
        wm.show("nope")


def test_close_all(wm):
    a = wm.show("settings")
    b = wm.show("about")
    wm.close_all()
    assert not a.isVisible() and not b.isVisible()
    assert wm.get("settings") is None


def test_kinds(wm):
    assert wm.kinds() == ("settings", "about")

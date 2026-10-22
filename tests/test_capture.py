from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPixmap

from shotrans.capture import SelectionWindow, crop_to_image


def _window(qtbot):
    pix = QPixmap(200, 100)
    pix.fill(QColor("white"))
    w = SelectionWindow(pix, QRect(0, 0, 200, 100))
    qtbot.addWidget(w)
    w.show()
    return w


def test_drag_emits_local_rect(qtbot):
    w = _window(qtbot)
    with qtbot.waitSignal(w.selected) as blocker:
        qtbot.mousePress(w, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        qtbot.mouseRelease(w, Qt.MouseButton.LeftButton, pos=QPoint(60, 40))
    assert blocker.args[0] == QRect(10, 10, 50, 30)


def test_reverse_drag_normalised(qtbot):
    w = _window(qtbot)
    with qtbot.waitSignal(w.selected) as blocker:
        qtbot.mousePress(w, Qt.MouseButton.LeftButton, pos=QPoint(60, 40))
        qtbot.mouseRelease(w, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert blocker.args[0] == QRect(10, 10, 50, 30)


def test_tiny_drag_cancels(qtbot):
    w = _window(qtbot)
    with qtbot.waitSignal(w.cancelled):
        qtbot.mousePress(w, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        qtbot.mouseRelease(w, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))


def test_escape_cancels(qtbot):
    w = _window(qtbot)
    with qtbot.waitSignal(w.cancelled):
        qtbot.keyClick(w, Qt.Key.Key_Escape)


def test_crop_respects_device_pixel_ratio(qapp):
    pix = QPixmap(400, 200)
    pix.fill(QColor("red"))
    pix.setDevicePixelRatio(2.0)
    img = crop_to_image(pix, QRect(10, 10, 50, 30))
    assert img.size == (100, 60)
    assert img.getpixel((0, 0)) == (255, 0, 0)

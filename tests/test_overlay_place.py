"""浮窗摆放（纯函数）：截图必须盖在原选区上，工具栏不能跑出屏幕。期望值写死。"""
from PySide6.QtCore import QRect

from shotrans.overlay import place_window

SCREEN = QRect(0, 0, 1920, 1080)


def _inside(win, screen):
    return screen.contains(win)


def test_room_above_bar_on_top():
    win, img, bar_y = place_window(QRect(100, 200, 300, 120), SCREEN)
    assert win == QRect(100, 172, 460, 148)
    assert img == QRect(0, 28, 300, 120) and bar_y == 0


def test_touching_top_bar_below():
    win, img, bar_y = place_window(QRect(100, 0, 300, 120), SCREEN)
    assert win == QRect(100, 0, 460, 148)
    assert img == QRect(0, 0, 300, 120) and bar_y == 120


def test_full_height_selection_bar_overlays_image_inside_screen():
    rect = QRect(0, 0, 800, 1080)
    win, img, bar_y = place_window(rect, SCREEN)
    assert win == QRect(0, 0, 800, 1080)
    assert img == QRect(0, 0, 800, 1080) and bar_y == 0
    assert _inside(win, SCREEN)


def test_bar_below_when_exactly_fits_at_bottom():
    win, img, bar_y = place_window(QRect(0, 10, 800, 1042), SCREEN)  # 10 像素上方放不下；下方正好 28
    assert bar_y == 1042 and win == QRect(0, 10, 800, 1070)
    assert _inside(win, SCREEN)


def test_bar_overlays_when_one_pixel_short_below():
    win, img, bar_y = place_window(QRect(0, 10, 800, 1043), SCREEN)
    assert bar_y == 0 and win == QRect(0, 10, 800, 1043)


def test_narrow_selection_near_right_edge_extends_left():
    rect = QRect(1800, 500, 100, 50)
    win, img, bar_y = place_window(rect, SCREEN)
    assert win == QRect(1460, 472, 460, 78)
    assert img == QRect(340, 28, 100, 50)
    assert win.left() + img.left() == rect.left()  # 截图仍在原位
    assert _inside(win, SCREEN)


def test_narrow_selection_exactly_fitting_on_right_not_shifted():
    win, img, _ = place_window(QRect(1460, 500, 100, 50), SCREEN)
    assert win.left() == 1460 and img.left() == 0


def test_secondary_screen_with_negative_coordinates():
    screen = QRect(-1920, 0, 1920, 1080)
    rect = QRect(-150, 500, 100, 50)
    win, img, _ = place_window(rect, screen)
    assert win == QRect(-460, 472, 460, 78)
    assert img == QRect(310, 28, 100, 50)
    assert _inside(win, screen)


def test_screen_narrower_than_bar_starts_at_screen_left():
    screen = QRect(0, 0, 300, 600)
    rect = QRect(100, 100, 150, 50)
    win, img, _ = place_window(rect, screen)
    assert win.left() == 0 and img.left() == 100
    assert win.left() + img.left() + img.width() <= win.left() + win.width()


def test_wide_selection_unchanged():
    win, img, _ = place_window(QRect(100, 200, 700, 120), SCREEN)
    assert win == QRect(100, 172, 700, 148) and img.left() == 0

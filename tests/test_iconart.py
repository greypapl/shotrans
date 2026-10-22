"""图标：托盘和 exe 用同一份绘制代码；ico 里每个尺寸都有。"""
from PIL import Image

from shotrans import iconart


def test_pixmap_sizes(qapp):
    for s in (16, 64, 256):
        pm = iconart.icon_pixmap(s)
        assert (pm.width(), pm.height()) == (s, s)


def test_pixmap_is_blue_with_white_glyph(qapp):
    img = iconart.icon_pixmap(64).toImage()
    c = img.pixelColor(32, 4)            # 上边中间：蓝底
    assert (c.red(), c.green(), c.blue()) == (0x25, 0x63, 0xEB)
    assert img.pixelColor(0, 0).alpha() == 0   # 圆角外透明
    whites = sum(1 for x in range(64) for y in range(64)
                 if img.pixelColor(x, y).red() > 240 and img.pixelColor(x, y).blue() > 240)
    assert whites > 50                    # 中间有白字


def test_make_icon_has_all_sizes(qapp):
    sizes = {(s.width(), s.height()) for s in iconart.make_icon().availableSizes()}
    assert {(s, s) for s in iconart.ICO_SIZES} <= sizes


def test_write_ico_contains_every_size(qapp, tmp_path):
    p = tmp_path / "icon.ico"
    iconart.write_ico(p)
    with Image.open(p) as im:
        assert set(im.info["sizes"]) == {(s, s) for s in iconart.ICO_SIZES}


def test_committed_ico_matches_sizes():
    """仓库里提交的 icon.ico 要存在且尺寸齐全（改了绘制代码记得 python -m tools.make_icon 重新生成）。"""
    with Image.open(iconart.ICON_PATH) as im:
        assert set(im.info["sizes"]) == {(s, s) for s in iconart.ICO_SIZES}


def test_app_uses_same_icon_code():
    from shotrans import app
    assert app.make_icon is iconart.make_icon
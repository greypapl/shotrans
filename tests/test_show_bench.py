import math

from PySide6.QtGui import QColor, QImage, QPixmap

from tools.show_bench import Viewer, first_unsaved


def _paths(tmp_path, n=3, size=(40, 30)):
    d = tmp_path / "images"
    d.mkdir()
    out = []
    for k in range(1, n + 1):
        img = QImage(*size, QImage.Format.Format_RGB32)
        img.fill(QColor(10 * k, 0, 0))
        p = d / f"{k:02d}.png"
        img.save(str(p))
        out.append(p)
    return out


def test_first_unsaved(tmp_path):
    paths = _paths(tmp_path)
    out = tmp_path / "youdao"
    assert first_unsaved(paths, out) == 0  # 目录还不存在
    out.mkdir()
    (out / "01.png").write_bytes(b"x")
    assert first_unsaved(paths, out) == 1
    (out / "03.png").write_bytes(b"x")  # 中间漏的优先
    assert first_unsaved(paths, out) == 1
    (out / "02.png").write_bytes(b"x")
    assert first_unsaved(paths, out) == 2  # 全做完停在最后一张


def test_viewer_starts_at_first_unsaved(qtbot, track, tmp_path):
    paths = _paths(tmp_path)
    out = tmp_path / "youdao"
    out.mkdir()
    (out / "01.png").write_bytes(b"x")
    v = Viewer(paths, out, grab=lambda *a: QPixmap())
    track(v)
    assert v.i == 1
    assert v.windowTitle().startswith("02（2/3）")


def test_capture_saves_exact_size_and_advances(qtbot, track, tmp_path):
    paths = _paths(tmp_path, size=(40, 30))
    out = tmp_path / "youdao"
    calls = []

    def fake_grab(x, y, w, h):
        calls.append((w, h))
        img = QImage(w + 3, h + 2, QImage.Format.Format_RGB32)  # 故意比原图大，应被裁回
        img.fill(QColor(0, 200, 0))
        return QPixmap.fromImage(img)

    v = Viewer(paths, out, grab=fake_grab)
    track(v)
    v.show()
    dpr = v.pixmap().devicePixelRatio()
    assert v.capture() is True
    saved = QImage(str(out / "01.png"))
    assert (saved.width(), saved.height()) == (40, 30)
    assert saved.pixelColor(5, 5) == QColor(0, 200, 0)
    assert calls[0] == (math.ceil(40 / dpr), math.ceil(30 / dpr))
    assert v.i == 1
    assert "已保存 01 ✓" in v.windowTitle() and "02（2/3）" in v.windowTitle()


def test_capture_last_says_all_done(qtbot, track, tmp_path):
    paths = _paths(tmp_path, n=1)
    out = tmp_path / "youdao"
    v = Viewer(paths, out, grab=lambda x, y, w, h: QPixmap.fromImage(QImage(w, h, QImage.Format.Format_RGB32)))
    track(v)
    v.show()
    assert v.capture() is True
    assert (out / "01.png").exists()
    assert v.i == 0
    assert "做完" in v.windowTitle()


def test_capture_too_small_fails_without_saving(qtbot, track, tmp_path):
    paths = _paths(tmp_path)
    out = tmp_path / "youdao"
    v = Viewer(paths, out, grab=lambda x, y, w, h: QPixmap.fromImage(QImage(5, 5, QImage.Format.Format_RGB32)))
    track(v)
    v.show()
    assert v.capture() is False
    assert not (out / "01.png").exists()
    assert v.i == 0

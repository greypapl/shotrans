"""历史窗口的图片区：选中有图的记录，上方显示译文图，按钮切换原图/译文；没图的记录只显示文字（图片区给一句说明）。"""
import json

import pytest
from PIL import Image

from shotrans import i18n
from shotrans.history import History
from shotrans.ui.history_window import HistoryWindow
from tests.ui_fakes import FakeApp

RED, BLUE = (255, 0, 0), (0, 0, 255)


def _entry(n, images=None):
    e = {"session": f"s{n}", "time": f"2026-10-13 10:00:0{n}", "direction": "en2zh", "service": "vllm",
         "segments": [{"src": f"Text {n}", "dst": f"文字{n}"}]}
    if images:
        e["image_id"] = images
        e["images"] = {"original": f"{images}-original.png", "rendered": f"{images}-rendered.png"}
    return e


@pytest.fixture
def make(qtbot, tmp_path, track):
    def _make(entries, size=(40, 30)):
        d = tmp_path / "history_images"
        d.mkdir(exist_ok=True)
        for e in entries:
            if "images" in e:
                Image.new("RGB", size, RED).save(d / e["images"]["original"])
                Image.new("RGB", size, BLUE).save(d / e["images"]["rendered"])
        (tmp_path / "history.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        app = FakeApp(tmp_path)
        app.history = History(tmp_path / "history.json", images_dir=d)
        w = HistoryWindow(app)
        track(w)
        w.show()
        return w, app
    return _make


def _color(w):
    img = w.image_label.pixmap().toImage()
    c = img.pixelColor(img.width() // 2, img.height() // 2)
    return (c.red(), c.green(), c.blue())


def test_selecting_entry_shows_rendered_image(make):
    w, _ = make([_entry(1, "aaa")])
    w.select(0)
    assert w.image_scroll.isVisibleTo(w) and not w.image_note.isVisibleTo(w)
    assert _color(w) == BLUE
    assert w.src_view.toPlainText() == "Text 1"  # 下方文字照旧


def test_toggle_switches_original_and_back(make):
    w, _ = make([_entry(1, "aaa")])
    w.select(0)
    assert w.btn_image_toggle.text() == "看原图"
    w.btn_image_toggle.click()
    assert _color(w) == RED and w.btn_image_toggle.text() == "看译文图"
    w.btn_image_toggle.click()
    assert _color(w) == BLUE and w.btn_image_toggle.text() == "看原图"


def test_new_selection_starts_on_rendered(make):
    w, _ = make([_entry(1, "aaa"), _entry(2, "bbb")])
    w.select(0)
    w.btn_image_toggle.click()
    w.select(1)
    assert _color(w) == BLUE


def test_text_only_entry_has_no_image(make):
    w, _ = make([_entry(1)])
    w.select(0)
    assert not w.image_scroll.isVisibleTo(w) and not w.btn_image_toggle.isVisibleTo(w)
    assert w.image_note.isVisibleTo(w) and w.image_note.text() == "这条记录没有保存截图"
    assert w.src_view.toPlainText() == "Text 1"


def test_missing_files_treated_as_text_only(make, tmp_path):
    w, _ = make([_entry(1, "aaa")])
    (tmp_path / "history_images" / "aaa-rendered.png").unlink()
    (tmp_path / "history_images" / "aaa-original.png").unlink()
    w.select(0)
    assert not w.image_scroll.isVisibleTo(w) and w.image_note.isVisibleTo(w)


def test_no_selection_hides_image_area(make):
    w, _ = make([_entry(1, "aaa")])
    w.select(0)
    w.table.clearSelection()
    assert not w.image_scroll.isVisibleTo(w) and not w.image_note.isVisibleTo(w)
    assert not w.btn_image_toggle.isVisibleTo(w)


def test_large_image_scaled_to_viewport_width(make):
    w, _ = make([_entry(1, "aaa")], size=(3000, 400))
    w.select(0)
    vw = w.image_scroll.viewport().width()
    pm = w.image_label.pixmap()
    assert 0 < pm.width() <= vw and pm.width() < 3000
    assert abs(pm.height() - round(400 * pm.width() / 3000)) <= 1  # 等比


def test_small_image_not_upscaled(make):
    w, _ = make([_entry(1, "aaa")], size=(40, 30))
    w.select(0)
    pm = w.image_label.pixmap()
    assert (pm.width(), pm.height()) == (40, 30)


def test_search_still_text_only(make):
    w, _ = make([_entry(1, "aaa"), _entry(2)])
    w.search.setText("aaa")  # image_id / 文件名不参与搜索
    assert all(w.table.isRowHidden(i) for i in range(w.table.rowCount()))


def test_delete_from_window_removes_images(make, tmp_path):
    w, app = make([_entry(1, "aaa"), _entry(2, "bbb")])
    w.select(1)  # 新的在上：第 1 行是 s1
    assert w.delete_selected() is True
    assert sorted(x.name for x in (tmp_path / "history_images").iterdir()) == ["bbb-original.png",
                                                                               "bbb-rendered.png"]


def test_language_switch_updates_toggle_and_note(make):
    w, _ = make([_entry(1, "aaa"), _entry(2)])
    w.select(1)
    i18n.set_language("en")
    assert w.btn_image_toggle.text() == "Show original"
    w.btn_image_toggle.click()
    assert w.btn_image_toggle.text() == "Show translation"
    w.select(0)
    assert w.image_note.text() == "No screenshot saved for this entry"


def test_fresh_window_hides_image_area(make):
    w, _ = make([_entry(1, "aaa")])
    assert not w.image_scroll.isVisibleTo(w) and not w.image_note.isVisibleTo(w)
    assert not w.btn_image_toggle.isVisibleTo(w)


def test_corrupt_png_treated_as_missing(make, tmp_path):
    w, _ = make([_entry(1, "aaa")])
    for kind in ("original", "rendered"):
        (tmp_path / "history_images" / f"aaa-{kind}.png").write_bytes(b"not a png")
    w.select(0)
    assert not w.image_scroll.isVisibleTo(w) and w.image_note.isVisibleTo(w)


def test_only_rendered_left_shows_it_without_toggle(make, tmp_path):
    w, _ = make([_entry(1, "aaa")])
    (tmp_path / "history_images" / "aaa-original.png").unlink()
    w.select(0)
    assert _color(w) == BLUE and not w.btn_image_toggle.isVisibleTo(w)
    w.toggle_image()
    assert _color(w) == BLUE


def test_only_original_left_shows_it(make, tmp_path):
    w, _ = make([_entry(1, "aaa")])
    (tmp_path / "history_images" / "aaa-rendered.png").unlink()
    w.select(0)
    assert _color(w) == RED


def test_tiny_area_still_shows_image(make):
    w, _ = make([_entry(1, "aaa")], size=(300, 40))
    w.image_scroll.setFixedWidth(8)
    w.select(0)
    assert not w.image_label.pixmap().isNull()


def test_rescales_when_area_resized(make, qtbot):
    w, _ = make([_entry(1, "aaa")], size=(3000, 400))
    w.select(0)
    before = w.image_label.pixmap().width()
    w.resize(w.width() - 300, w.height())
    qtbot.waitUntil(lambda: w.image_label.pixmap().width() < before, timeout=2000)

# ---------- 审查 I2：窗口打开时图还在后台写 ----------
def _pending_app(make, tmp_path):
    w, app = make([])
    iid = app.history.add(_entry(1), with_images=True)
    w.reload()
    return w, app, iid


def test_saving_note_then_image_appears(make, tmp_path, qtbot):
    w, app, iid = _pending_app(make, tmp_path)
    w.select(0)
    assert w.image_note.isVisibleTo(w) and w.image_note.text() == "截图正在保存…"
    assert app.history.write_images(iid, Image.new("RGB", (40, 30), RED), Image.new("RGB", (40, 30), BLUE))
    qtbot.waitUntil(lambda: w.image_scroll.isVisibleTo(w), timeout=3000)
    assert _color(w) == BLUE and not w.image_note.isVisibleTo(w)


def test_failed_write_turns_note_into_no_image(make, tmp_path, qtbot, monkeypatch):
    w, app, iid = _pending_app(make, tmp_path)
    w.select(0)
    monkeypatch.setattr(Image.Image, "save", lambda self, *a, **k: (_ for _ in ()).throw(OSError("满")))
    app.history.write_images(iid, *(Image.new("RGB", (4, 3)),) * 2)
    qtbot.waitUntil(lambda: w.image_note.text() == "这条记录没有保存截图", timeout=3000)


def test_delete_stale_selected_entry_after_images_landed(make, tmp_path):
    w, app, iid = _pending_app(make, tmp_path)
    w.select(0)
    app.history.write_images(iid, Image.new("RGB", (4, 3), RED), Image.new("RGB", (4, 3), BLUE))
    assert w.delete_selected() is True
    assert app.history.load() == [] and w.table.rowCount() == 0
    assert list((tmp_path / "history_images").iterdir()) == []


def test_saving_note_follows_language(make, tmp_path):
    w, _, _ = _pending_app(make, tmp_path)
    w.select(0)
    i18n.set_language("en")
    assert w.image_note.text() == "Saving screenshot…"

def test_stale_recheck_does_not_reset_new_selection(make, tmp_path, qtbot):
    w, app = make([_entry(2, "bbb")])
    app.history.add(_entry(1), with_images=True)  # 最新一条，图还在写
    w.reload()
    w.select(0)
    assert w.image_note.text() == "截图正在保存…"
    w.select(1)
    w.btn_image_toggle.click()
    assert _color(w) == RED
    qtbot.wait(450)  # 上一条留下的定时器到点
    assert _color(w) == RED
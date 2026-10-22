"""历史记录连同截图一起存：每条记录绑两张 PNG（原图、译文图），与记录同生同灭。

图片放 history_images/，文件名 <image_id>-original.png / <image_id>-rendered.png；
记录里 "image_id" 是添加时就写下的编号，"images" 只在两张图都写成功后才补上。"""
import json

from PIL import Image

from shotrans import paths
from shotrans.history import History


def _h(tmp_path, limit=200, save=True):
    return History(tmp_path / "history.json", limit, images_dir=tmp_path / "history_images", save_images=save)


def _imgs(color="red"):
    return Image.new("RGB", (8, 6), color), Image.new("RGB", (8, 6), "blue")


def _files(tmp_path):
    d = tmp_path / "history_images"
    return sorted(x.name for x in d.iterdir()) if d.exists() else []


def _add(h, sid, color="red"):
    iid = h.add({"session": sid, "v": color}, with_images=True)
    assert iid is not None
    assert h.write_images(iid, *_imgs(color)) is True
    return iid


def test_paths_history_images_dir(tmp_path):
    assert paths.history_images_dir(tmp_path) == tmp_path / "history_images"


def test_images_saved_and_bound_to_entry(tmp_path):
    h = _h(tmp_path)
    iid = _add(h, "s1")
    [e] = h.load()
    assert e["image_id"] == iid
    assert e["images"] == {"original": f"{iid}-original.png", "rendered": f"{iid}-rendered.png"}
    assert _files(tmp_path) == [f"{iid}-original.png", f"{iid}-rendered.png"]
    assert h.image_path(e, "original") == tmp_path / "history_images" / f"{iid}-original.png"
    with Image.open(h.image_path(e, "original")) as im:
        assert im.format == "PNG" and im.getpixel((0, 0)) == (255, 0, 0)
    with Image.open(h.image_path(e, "rendered")) as im:
        assert im.getpixel((0, 0)) == (0, 0, 255)


def test_add_without_images_leaves_entry_untouched(tmp_path):
    h = _h(tmp_path)
    assert h.add({"id": 1}) is None
    assert h.load() == [{"id": 1}] and _files(tmp_path) == []


def test_save_images_off_gives_no_id_and_no_files(tmp_path):
    h = _h(tmp_path, save=False)
    assert h.wants_images is False
    assert h.add({"session": "s1"}, with_images=True) is None
    assert h.load() == [{"session": "s1"}] and not (tmp_path / "history_images").exists()


def test_no_images_dir_gives_no_id(tmp_path):
    h = History(tmp_path / "history.json")
    assert h.wants_images is False
    assert h.add({"session": "s1"}, with_images=True) is None
    assert h.load() == [{"session": "s1"}]


def test_wants_images_true_when_dir_and_switch(tmp_path):
    assert _h(tmp_path).wants_images is True


def test_retranslate_same_session_replaces_old_pair(tmp_path):
    h = _h(tmp_path)
    old = _add(h, "s1", "red")
    new = _add(h, "s1", "green")
    assert old != new
    [e] = h.load()
    assert e["image_id"] == new
    assert _files(tmp_path) == [f"{new}-original.png", f"{new}-rendered.png"]


def test_late_images_of_replaced_entry_are_dropped(tmp_path):
    """旧那次的图后写完（新那次已经替换了记录）：旧图不能挂到新记录上，文件也删掉。"""
    h = _h(tmp_path)
    old = h.add({"session": "s1", "v": 1}, with_images=True)
    new = h.add({"session": "s1", "v": 2}, with_images=True)
    assert h.write_images(new, *_imgs("green")) is True
    assert h.write_images(old, *_imgs("red")) is False
    [e] = h.load()
    assert e["image_id"] == new and e["images"]["original"] == f"{new}-original.png"
    assert _files(tmp_path) == [f"{new}-original.png", f"{new}-rendered.png"]


def test_trim_past_limit_deletes_images(tmp_path):
    h = _h(tmp_path, limit=2)
    a = _add(h, "a")
    b = _add(h, "b")
    c = _add(h, "c")
    assert [e["session"] for e in h.load()] == ["b", "c"]
    names = _files(tmp_path)
    assert not any(n.startswith(a) for n in names)
    assert names == sorted([f"{b}-original.png", f"{b}-rendered.png", f"{c}-original.png", f"{c}-rendered.png"])


def test_delete_entry_deletes_its_images(tmp_path):
    h = _h(tmp_path)
    a = _add(h, "a")
    b = _add(h, "b")
    entry_a = h.load()[0]
    assert h.delete(entry_a) is True
    assert _files(tmp_path) == [f"{b}-original.png", f"{b}-rendered.png"]
    assert a not in "".join(_files(tmp_path))


def test_delete_by_index_deletes_its_images(tmp_path):
    h = _h(tmp_path)
    _add(h, "a")
    b = _add(h, "b")
    assert h.delete(0) is True
    assert _files(tmp_path) == [f"{b}-original.png", f"{b}-rendered.png"]


def test_clear_deletes_all_images(tmp_path):
    h = _h(tmp_path)
    _add(h, "a")
    _add(h, "b")
    h.clear()
    assert h.load() == [] and _files(tmp_path) == []


def test_orphans_cleaned_on_add(tmp_path):
    (tmp_path / "history.json").write_text("[]", encoding="utf-8")  # 读得出来的（空）历史
    d = tmp_path / "history_images"
    d.mkdir()
    (d / "20200101000000deadbeef-original.png").write_bytes(b"x")
    (d / "stray.png.tmp").write_bytes(b"x")
    h = _h(tmp_path)
    h.add({"id": 1})
    assert _files(tmp_path) == []


def test_orphan_cleanup_keeps_subfolders(tmp_path):
    d = tmp_path / "history_images" / "子文件夹"
    d.mkdir(parents=True)
    _h(tmp_path).add({"id": 1})
    assert _files(tmp_path) == ["子文件夹"]


def test_pending_images_not_cleaned_by_other_add(tmp_path):
    """图还在后台写的时候，别的记录添加触发清理：不能把它当孤儿删掉。"""
    h = _h(tmp_path)
    iid = h.add({"session": "s1"}, with_images=True)
    d = tmp_path / "history_images"
    d.mkdir()
    (d / f"{iid}-original.png.tmp").write_bytes(b"half")  # 写到一半
    h.add({"session": "s2"})
    assert _files(tmp_path) == [f"{iid}-original.png.tmp"]


def test_write_failure_keeps_text_entry(tmp_path, monkeypatch, caplog):
    h = _h(tmp_path)
    iid = h.add({"session": "s1", "segments": []}, with_images=True)

    def boom(self, *a, **k):
        raise OSError("磁盘满了")

    monkeypatch.setattr(Image.Image, "save", boom)
    assert h.write_images(iid, *_imgs()) is False
    [e] = h.load()
    assert e["session"] == "s1" and "images" not in e
    assert h.image_path(e, "rendered") is None
    assert _files(tmp_path) == []
    assert "历史截图保存失败" in caplog.text


def test_partial_write_failure_removes_first_file(tmp_path, monkeypatch):
    h = _h(tmp_path)
    iid = h.add({"session": "s1"}, with_images=True)
    real = Image.Image.save
    calls = []

    def second_fails(self, fp, *a, **k):
        calls.append(fp)
        if len(calls) == 2:
            raise OSError("第二张写不进去")
        return real(self, fp, *a, **k)

    monkeypatch.setattr(Image.Image, "save", second_fails)
    assert h.write_images(iid, *_imgs()) is False
    assert _files(tmp_path) == []
    assert "images" not in h.load()[0]


def test_write_images_never_raises_when_history_unwritable(tmp_path, monkeypatch, caplog):
    h = _h(tmp_path)
    iid = h.add({"session": "s1"}, with_images=True)
    monkeypatch.setattr(History, "_write", lambda self, items: (_ for _ in ()).throw(OSError("被占用")))
    assert h.write_images(iid, *_imgs()) is False  # 不抛即通过
    assert "历史截图保存失败" in caplog.text
    assert _files(tmp_path) == []  # 没绑上的图当场删掉（审查 M2）


def test_image_path_none_for_text_only_and_missing_file(tmp_path):
    h = _h(tmp_path)
    assert h.image_path({"segments": []}, "original") is None
    iid = _add(h, "s1")
    e = h.load()[0]
    (tmp_path / "history_images" / f"{iid}-original.png").unlink()
    assert h.image_path(e, "original") is None
    assert h.image_path(e, "rendered") is not None


def test_image_path_rejects_names_outside_folder(tmp_path):
    outside = tmp_path / "secret.png"
    Image.new("RGB", (2, 2)).save(outside)
    h = _h(tmp_path)
    (tmp_path / "history_images").mkdir()
    e = {"images": {"original": "../secret.png", "rendered": str(outside)}}
    assert h.image_path(e, "original") is None
    assert h.image_path(e, "rendered") is None


def test_cleanup_never_touches_files_outside_folder(tmp_path):
    keep = tmp_path / "keep.png"
    keep.write_bytes(b"x")
    h = _h(tmp_path)
    (tmp_path / "history.json").write_text(json.dumps([{"images": {"original": "../keep.png"}}]), encoding="utf-8")
    h.clear()
    assert keep.exists()


def test_old_text_only_entries_still_load(tmp_path):
    p = tmp_path / "history.json"
    old = [{"session": "s0", "time": "2026-10-01 10:00:00", "segments": [{"src": "a", "dst": "甲"}]}]
    p.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    h = _h(tmp_path)
    _add(h, "s1")
    items = h.load()
    assert items[0] == old[0] and len(items) == 2


def test_orphan_cleanup_without_folder_logs_nothing(tmp_path, caplog):
    _h(tmp_path).add({"id": 1})
    assert "历史截图" not in caplog.text


def test_subfolder_not_attempted(tmp_path, caplog):
    (tmp_path / "history.json").write_text("[]", encoding="utf-8")
    (tmp_path / "history_images" / "子文件夹").mkdir(parents=True)
    _h(tmp_path).add({"id": 1})
    assert "删不掉历史截图" not in caplog.text


def test_cleanup_tolerates_bad_image_values(tmp_path):
    (tmp_path / "history.json").write_text(json.dumps([{"images": {"original": ["坏"], "rendered": 3}}]),
                                           encoding="utf-8")
    h = _h(tmp_path)
    (tmp_path / "history_images").mkdir()
    h.add({"id": 1})  # 不抛即通过
    assert len(h.load()) == 2


def test_unreadable_folder_does_not_break_add(tmp_path, monkeypatch, caplog):
    from pathlib import Path
    (tmp_path / "history_images").mkdir()

    def boom(self):
        raise PermissionError("没权限")

    monkeypatch.setattr(Path, "iterdir", boom)
    h = _h(tmp_path)
    h.add({"id": 1})
    assert h.load() == [{"id": 1}] and "读不了历史截图文件夹" in caplog.text


def test_undeletable_file_does_not_break_delete(tmp_path, monkeypatch, caplog):
    from pathlib import Path
    h = _h(tmp_path)
    _add(h, "a")

    def boom(self, missing_ok=False):
        raise PermissionError("被占用")

    monkeypatch.setattr(Path, "unlink", boom)
    assert h.delete(0) is True
    assert h.load() == [] and "删不掉历史截图" in caplog.text

def test_image_path_without_images_dir_is_none(tmp_path):
    h = History(tmp_path / "history.json")
    assert h.image_path({"images": {"original": "a-original.png"}}, "original") is None

# ---------- 审查 I2：后台补 images 之后，手里的旧副本也能删 ----------
def test_delete_stale_copy_after_images_landed(tmp_path):
    h = _h(tmp_path)
    iid = h.add({"session": "s1", "time": "2026-10-20 10:00:00"}, with_images=True)
    stale = h.load()[0]
    assert h.write_images(iid, *_imgs()) is True
    assert h.load()[0] != stale  # 文件里那条已补上 images
    assert h.delete(stale) is True
    assert h.load() == [] and _files(tmp_path) == []


def test_find_returns_current_version(tmp_path):
    h = _h(tmp_path)
    iid = h.add({"session": "s1", "time": "t1"}, with_images=True)
    stale = h.load()[0]
    h.write_images(iid, *_imgs())
    assert "images" in h.find(stale)
    assert h.find({"session": "s9", "time": "t1"}) is None


def test_stale_copy_of_replaced_entry_not_deleted(tmp_path):
    """同一张截图重翻后（time 不同）：旧副本不能删掉新记录。"""
    h = _h(tmp_path)
    h.add({"session": "s1", "time": "t1"})
    old = h.load()[0]
    h.add({"session": "s1", "time": "t2"})
    assert h.delete(old) is False
    assert h.load() == [{"session": "s1", "time": "t2"}]


def test_old_entries_without_session_deleted_by_equality(tmp_path):
    h = _h(tmp_path)
    for e in ({"time": "t", "v": 1}, {"time": "t", "v": 2}):
        h.add(e)
    assert h.delete({"time": "t", "v": 2}) is True
    assert h.load() == [{"time": "t", "v": 1}]


def test_images_pending_states(tmp_path, monkeypatch):
    h = _h(tmp_path)
    iid = h.add({"session": "s1", "time": "t"}, with_images=True)
    assert h.images_pending(h.load()[0]) is True
    h.write_images(iid, *_imgs())
    assert h.images_pending(h.load()[0]) is False
    iid2 = h.add({"session": "s2", "time": "t"}, with_images=True)
    monkeypatch.setattr(Image.Image, "save", lambda self, *a, **k: (_ for _ in ()).throw(OSError("满")))
    h.write_images(iid2, *_imgs())
    assert h.images_pending(h.load()[-1]) is False  # 写失败：不再算保存中
    assert h.images_pending({"image_id": "x"}) is False

# ---------- 审查 M1：历史读不出来时一张图都不删 ----------
def _seed_images(tmp_path, names=("a1-original.png", "a1-rendered.png", "b2-original.png")):
    d = tmp_path / "history_images"
    d.mkdir(exist_ok=True)
    for n in names:
        Image.new("RGB", (2, 2)).save(d / n)
    return d


def _all_pngs(tmp_path):
    return sorted(p.name for p in (tmp_path / "history_images").rglob("*.png"))


def test_corrupt_history_keeps_all_images_through_add_and_trim(tmp_path):
    (tmp_path / "history.json").write_text("{坏掉的", encoding="utf-8")
    _seed_images(tmp_path)
    h = _h(tmp_path, limit=1)
    _add(h, "s1")
    _add(h, "s2")  # 挤掉 s1
    kept = [p for p in (tmp_path / "history_images").iterdir() if p.is_dir()]
    assert len(kept) == 1 and kept[0].name.startswith(".kept-")
    assert sorted(p.name for p in kept[0].iterdir()) == ["a1-original.png", "a1-rendered.png", "b2-original.png"]
    assert len(list(tmp_path.glob("history.json.bad-*"))) == 1


def test_non_list_history_keeps_images(tmp_path):
    (tmp_path / "history.json").write_text('{"a": 1}', encoding="utf-8")
    _seed_images(tmp_path)
    h = _h(tmp_path)
    h.add({"id": 1})
    h.add({"id": 2})
    assert "a1-original.png" in _all_pngs(tmp_path) and len(_all_pngs(tmp_path)) == 3


def test_corrupt_history_seen_by_window_load_keeps_images(tmp_path):
    (tmp_path / "history.json").write_text("[1, 2", encoding="utf-8")
    _seed_images(tmp_path)
    h = _h(tmp_path)
    assert h.load() == []  # 历史窗口打开时走的就是 load()
    h.add({"id": 1})
    assert len(_all_pngs(tmp_path)) == 3


def test_missing_history_file_keeps_images(tmp_path):
    _seed_images(tmp_path)
    h = _h(tmp_path)
    h.add({"id": 1})
    h.add({"id": 2})
    assert len(_all_pngs(tmp_path)) == 3


def test_corrupt_during_bind_keeps_other_images(tmp_path):
    h = _h(tmp_path)
    iid = h.add({"session": "s1", "time": "t"}, with_images=True)
    _seed_images(tmp_path)
    (tmp_path / "history.json").write_text("坏", encoding="utf-8")
    assert h.write_images(iid, *_imgs()) is False
    h.add({"id": 1})
    names = _all_pngs(tmp_path)
    assert {"a1-original.png", "a1-rendered.png", "b2-original.png"} <= set(names)
    assert not any(n.startswith(iid) for n in names)  # 自己刚写的图没处绑，删掉


def test_keep_move_failure_blocks_cleanup(tmp_path, monkeypatch, caplog):
    from shotrans import history as history_mod
    (tmp_path / "history.json").write_text("坏", encoding="utf-8")
    _seed_images(tmp_path)

    def boom(src, dst):
        raise PermissionError("被占用")

    monkeypatch.setattr(history_mod, "_move", boom)
    h = _h(tmp_path)
    h.add({"id": 1})
    h.add({"id": 2})
    assert len(_all_pngs(tmp_path)) == 3 and "本次运行不再清理" in caplog.text


def test_keep_unreadable_folder_blocks_cleanup(tmp_path, monkeypatch):
    from pathlib import Path
    (tmp_path / "history.json").write_text("坏", encoding="utf-8")
    _seed_images(tmp_path)
    real = Path.iterdir
    calls = []

    def flaky(self):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("没权限")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", flaky)
    h = _h(tmp_path)
    h.add({"id": 1})
    h.add({"id": 2})
    monkeypatch.setattr(Path, "iterdir", real)
    assert len(_all_pngs(tmp_path)) == 3


def test_keep_skips_pending_images(tmp_path):
    h = _h(tmp_path)
    iid = h.add({"session": "s1", "time": "t"}, with_images=True)
    (tmp_path / "history.json").unlink()
    d = tmp_path / "history_images"
    d.mkdir()
    (d / f"{iid}-original.png.tmp").write_bytes(b"half")
    h.add({"id": 1})
    assert (d / f"{iid}-original.png.tmp").exists()


def test_clear_still_deletes_everything(tmp_path):
    h = _h(tmp_path)
    _add(h, "a")
    h.clear()
    assert _files(tmp_path) == []


# ---------- 审查 M3：同一种写图错误只记一次完整堆栈 ----------
def test_same_failure_logged_with_traceback_once(tmp_path, monkeypatch, caplog):
    h = _h(tmp_path)
    monkeypatch.setattr(Image.Image, "save", lambda self, *a, **k: (_ for _ in ()).throw(OSError("磁盘满了")))
    for sid in ("s1", "s2"):
        h.write_images(h.add({"session": sid, "time": "t"}, with_images=True), *_imgs())
    recs = [r for r in caplog.records if "历史截图保存失败" in r.getMessage()]
    assert len(recs) == 2 and [bool(r.exc_info) for r in recs] == [True, False]


def test_cleanup_dir_warning_logged_once(tmp_path, caplog):
    (tmp_path / "history.json").write_text("[]", encoding="utf-8")
    (tmp_path / "history_images").write_text("我是文件不是文件夹", encoding="utf-8")
    h = _h(tmp_path)
    h.add({"id": 1})
    h.add({"id": 2})
    assert caplog.text.count("读不了历史截图文件夹") == 1


def test_cleanup_without_folder_logs_nothing_when_history_readable(tmp_path, caplog):
    (tmp_path / "history.json").write_text("[]", encoding="utf-8")
    _h(tmp_path).add({"id": 1})
    assert "历史截图" not in caplog.text
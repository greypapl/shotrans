import json

from shotrans.history import History


def test_add_and_reload(tmp_path):
    h = History(tmp_path / "history.json", limit=200)
    h.add({"id": 1})
    assert History(tmp_path / "history.json").load() == [{"id": 1}]


def test_limit_trims_oldest(tmp_path):
    h = History(tmp_path / "history.json", limit=3)
    for i in range(5):
        h.add({"id": i})
    assert [e["id"] for e in h.load()] == [2, 3, 4]


def test_chinese_kept_readable(tmp_path):
    p = tmp_path / "history.json"
    History(p).add({"src": "抑郁"})
    assert "抑郁" in p.read_text(encoding="utf-8")


def test_corrupt_file_backed_up(tmp_path):
    p = tmp_path / "history.json"
    p.write_text("{坏掉的", encoding="utf-8")
    h = History(p)
    h.add({"id": 1})
    bad = list(tmp_path.glob("history.json.bad-*"))
    assert len(bad) == 1 and bad[0].read_text(encoding="utf-8") == "{坏掉的"
    assert h.load() == [{"id": 1}]


def test_non_list_json_backed_up(tmp_path):
    p = tmp_path / "history.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    History(p).add({"id": 1})
    assert len(list(tmp_path.glob("history.json.bad-*"))) == 1


def test_no_tmp_left(tmp_path):
    History(tmp_path / "history.json").add({"id": 1})
    assert sorted(x.name for x in tmp_path.iterdir()) == ["history.json"]


def test_load_missing_file_returns_empty_and_creates_nothing(tmp_path):
    h = History(tmp_path / "history.json")
    assert h.load() == []
    assert list(tmp_path.iterdir()) == []


def test_default_limit_is_200(tmp_path):
    h = History(tmp_path / "history.json")
    for i in range(205):
        h.add({"id": i})
    items = h.load()
    assert len(items) == 200 and items[0]["id"] == 5 and items[-1]["id"] == 204


def test_backup_name_has_timestamp(tmp_path):
    import re
    p = tmp_path / "history.json"
    p.write_text("坏", encoding="utf-8")
    History(p).load()
    names = [x.name for x in tmp_path.iterdir()]
    assert len(names) == 1 and re.fullmatch(r"history\.json\.bad-\d{8}-\d{6}", names[0])
    assert not p.exists()


def test_load_corrupt_returns_empty_and_moves_file(tmp_path):
    p = tmp_path / "history.json"
    p.write_text("[1, 2", encoding="utf-8")
    assert History(p).load() == []
    assert not p.exists()


def test_non_utf8_file_backed_up(tmp_path):
    p = tmp_path / "history.json"
    p.write_bytes(b"\xff\xfe\x00\x80bad")
    h = History(p)
    h.add({"id": 1})
    assert len(list(tmp_path.glob("history.json.bad-*"))) == 1
    assert h.load() == [{"id": 1}]


def test_valid_empty_list_not_backed_up(tmp_path):
    p = tmp_path / "history.json"
    p.write_text("[]", encoding="utf-8")
    h = History(p)
    h.add({"id": 1})
    assert list(tmp_path.glob("history.json.bad-*")) == []
    assert h.load() == [{"id": 1}]


def test_add_holds_lock(tmp_path):
    events = []

    class SpyLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, *a):
            events.append("exit")

    h = History(tmp_path / "history.json")
    h._lock = SpyLock()
    h.add({"id": 1})
    assert events == ["enter", "exit"]


def test_concurrent_adds_all_kept(tmp_path):
    import threading
    h = History(tmp_path / "history.json", limit=200)
    ts = [threading.Thread(target=h.add, args=({"id": i},)) for i in range(30)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(e["id"] for e in h.load()) == list(range(30))


def test_add_overwrites_previous_content_atomically(tmp_path):
    p = tmp_path / "history.json"
    h = History(p)
    h.add({"id": 1})
    h.add({"id": 2})
    assert json.loads(p.read_text(encoding="utf-8")) == [{"id": 1}, {"id": 2}]


def test_add_is_atomic_when_replace_fails(tmp_path, monkeypatch):
    p = tmp_path / "history.json"
    h = History(p)
    h.add({"id": 1})

    def boom(src, dst):
        raise OSError("磁盘满了")

    monkeypatch.setattr("shotrans.history.os.replace", boom)
    try:
        h.add({"id": 2})
    except OSError:
        pass
    assert json.loads(p.read_text(encoding="utf-8")) == [{"id": 1}]


# ---------- 同一张截图只留一条（session） ----------
def test_same_session_replaces_previous_entry(tmp_path):
    h = History(tmp_path / "history.json")
    h.add({"session": "s1", "direction": "en2zh"})
    h.add({"session": "s1", "direction": "zh2en"})
    assert h.load() == [{"session": "s1", "direction": "zh2en"}]


def test_replaced_entry_moves_to_end(tmp_path):
    h = History(tmp_path / "history.json")
    h.add({"session": "s1", "v": 1})
    h.add({"session": "s2", "v": 1})
    h.add({"session": "s1", "v": 2})
    assert h.load() == [{"session": "s2", "v": 1}, {"session": "s1", "v": 2}]


def test_entries_without_session_never_replaced(tmp_path):
    h = History(tmp_path / "history.json")
    h.add({"id": 1})
    h.add({"id": 1})
    h.add({"session": "s1"})
    assert h.load() == [{"id": 1}, {"id": 1}, {"session": "s1"}]


def test_non_dict_items_in_file_tolerated_when_replacing(tmp_path):
    p = tmp_path / "history.json"
    p.write_text('[1, {"session": "s1"}]', encoding="utf-8")
    h = History(p)
    h.add({"session": "s1", "v": 2})
    assert h.load() == [1, {"session": "s1", "v": 2}]

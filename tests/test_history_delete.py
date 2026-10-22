import json

from shotrans.history import History


def _h(tmp_path, n=3):
    h = History(tmp_path / "history.json")
    for i in range(n):
        h.add({"id": i})
    return h


def test_delete_by_index(tmp_path):
    h = _h(tmp_path)
    assert h.delete(1) is True
    assert h.load() == [{"id": 0}, {"id": 2}]


def test_delete_index_out_of_range(tmp_path):
    h = _h(tmp_path)
    assert h.delete(3) is False and h.delete(-1) is False
    assert len(h.load()) == 3


def test_delete_by_entry(tmp_path):
    h = _h(tmp_path)
    assert h.delete({"id": 2}) is True
    assert h.load() == [{"id": 0}, {"id": 1}]


def test_delete_missing_entry(tmp_path):
    h = _h(tmp_path)
    assert h.delete({"id": 9}) is False and len(h.load()) == 3


def test_clear(tmp_path):
    h = _h(tmp_path)
    h.clear()
    assert h.load() == [] and json.loads((tmp_path / "history.json").read_text(encoding="utf-8")) == []


def test_no_tmp_left(tmp_path):
    h = _h(tmp_path)
    h.delete(0)
    h.clear()
    assert sorted(x.name for x in tmp_path.iterdir()) == ["history.json"]


def test_add_still_works_after_refactor(tmp_path):
    h = History(tmp_path / "history.json", limit=2)
    for i in range(3):
        h.add({"id": i})
    assert h.load() == [{"id": 1}, {"id": 2}]

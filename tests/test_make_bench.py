"""tools.make_bench：用例里的 pdf 相对路径按 bench 目录解析，绝对路径原样用。"""
from __future__ import annotations

import sys
import tomllib
import types
from pathlib import Path

import pytest

from tools import make_bench

REPO = Path(__file__).resolve().parents[1]


def test_relative_pdf_resolves_against_bench_dir(tmp_path):
    assert make_bench.pdf_path({"pdf": "pdfs/a.pdf"}, tmp_path) == tmp_path / "pdfs" / "a.pdf"


def test_relative_pdf_default_bench_is_repo_bench():
    assert make_bench.pdf_path({"pdf": "pdfs/a.pdf"}) == REPO / "bench" / "pdfs" / "a.pdf"


def test_absolute_pdf_kept(tmp_path):
    p = tmp_path / "elsewhere" / "b.pdf"
    assert make_bench.pdf_path({"pdf": str(p)}, Path("C:/nowhere")) == p


def test_make_pdf_opens_resolved_path(tmp_path, monkeypatch):
    opened = []

    class _Page:
        def get_pixmap(self, **kw):
            return types.SimpleNamespace(save=lambda path: None)

        def get_text(self, *a, **kw):
            return "x"

    fake = types.ModuleType("pymupdf")
    fake.open = lambda path: opened.append(path) or [_Page()]
    fake.Rect = lambda *a: a
    fake.Matrix = lambda *a: a
    monkeypatch.setitem(sys.modules, "pymupdf", fake)
    monkeypatch.setattr(make_bench, "BENCH", tmp_path)
    (tmp_path / "pdfs").mkdir()
    (tmp_path / "pdfs" / "c.pdf").write_bytes(b"%PDF-")
    make_bench.make_pdf({"pdf": "pdfs/c.pdf", "page": 1, "clip": [0, 0, 1, 1]},
                        tmp_path / "c.png", tmp_path / "c.txt")
    assert opened == [str(tmp_path / "pdfs" / "c.pdf")]


def test_missing_pdf_fails_with_expected_path(tmp_path, monkeypatch):
    monkeypatch.setattr(make_bench, "BENCH", tmp_path)
    monkeypatch.setitem(sys.modules, "pymupdf", None)  # 没走到打开 PDF 那一步：缺文件时先报清楚
    with pytest.raises(FileNotFoundError) as ei:
        make_bench.make_pdf({"id": "04", "pdf": "pdfs/N19-1423.pdf", "page": 1, "clip": [0, 0, 1, 1]},
                            tmp_path / "x.png", tmp_path / "x.txt")
    msg = str(ei.value)
    assert str(tmp_path / "pdfs" / "N19-1423.pdf") in msg and "04" in msg and "bench/pdfs/" in msg
    assert not (tmp_path / "x.png").exists() and not (tmp_path / "x.txt").exists()


def test_committed_cases_use_relative_pdf_paths():
    cases = tomllib.loads((REPO / "bench" / "cases.toml").read_text(encoding="utf-8"))["case"]
    pdfs = [c["pdf"] for c in cases if c["kind"] == "pdf"]
    assert pdfs
    for p in pdfs:
        assert not Path(p).is_absolute() and not p.startswith("/"), p
        assert p.startswith("pdfs/"), p

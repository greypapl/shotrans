"""Task 18：[translate] 的分块并发三项。期望值写死。"""
import pytest

from shotrans.app import build_router
from shotrans.config import ConfigError, load_config
from shotrans.glossary import Glossary


def _write(tmp_path, text):
    p = tmp_path / "config.local.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_chunk_defaults(tmp_path):
    c = load_config(_write(tmp_path, ""))
    assert (c.chunk_chars, c.chunk_max_segments, c.max_concurrency) == (600, 8, 4)


def test_chunk_values_read(tmp_path):
    c = load_config(_write(tmp_path, "[translate]\nchunk_chars = 300\nchunk_max_segments = 5\nmax_concurrency = 1\n"))
    assert (c.chunk_chars, c.chunk_max_segments, c.max_concurrency) == (300, 5, 1)


@pytest.mark.parametrize("key,val", [("chunk_chars", '"600"'), ("chunk_max_segments", "8.0"),
                                     ("max_concurrency", "true")])
def test_chunk_wrong_type(tmp_path, key, val):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, f"[translate]\n{key} = {val}\n"))
    assert f"[translate] {key}" in str(ei.value) and "int" in str(ei.value)


@pytest.mark.parametrize("key", ["chunk_chars", "chunk_max_segments", "max_concurrency"])
def test_chunk_zero_rejected(tmp_path, key):
    with pytest.raises(ConfigError) as ei:
        load_config(_write(tmp_path, f"[translate]\n{key} = 0\n"))
    assert f"[translate] {key}" in str(ei.value) and "至少是 1" in str(ei.value)


def test_chunk_one_accepted(tmp_path):
    c = load_config(_write(tmp_path, "[translate]\nchunk_chars = 1\nchunk_max_segments = 1\nmax_concurrency = 1\n"))
    assert (c.chunk_chars, c.chunk_max_segments, c.max_concurrency) == (1, 1, 1)


def test_build_router_passes_chunk_settings(tmp_path):
    c = load_config(_write(tmp_path, "[translate]\nchunk_chars = 123\nchunk_max_segments = 4\nmax_concurrency = 2\n"
                                     "[tencent]\nenabled = false\n"))
    r = build_router(c, Glossary())
    assert (r.chunk_chars, r.chunk_max_segments, r.max_concurrency) == (123, 4, 2)

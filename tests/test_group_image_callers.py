"""Task 19：放宽合并要看截图里的间隙，三个调用方（会话、对比测试、快照重放）都得把截图传给 group_lines。
数据：同一行两段，间距 30（> 1 倍行高 20，≤ 3 倍），间隙是干净白底——传了截图才并成一段。"""
from PIL import Image

from shotrans.models import Box, OcrLine

FRAGS = [OcrLine("aa", Box(10, 10, 100, 20)), OcrLine("bb", Box(140, 10, 50, 20))]


def _white():
    return Image.new("RGB", (400, 100), "white")


def test_session_passes_image_to_grouping():
    from shotrans.session import Session

    s = Session(_white())
    s.set_lines(FRAGS)
    assert [g.text for g in s.segments] == ["aa bb"]


def test_run_bench_passes_image_to_grouping(monkeypatch, tmp_path):
    from tools import run_bench as rb

    monkeypatch.setattr(rb, "BENCH", tmp_path)
    monkeypatch.setattr(rb, "recognize", lambda img: list(FRAGS))
    assert [g.text for g in rb.analyse("96", _white())["segs"]] == ["aa bb"]


def test_replay_passes_image_to_grouping(tmp_path):
    from shotrans import snapshot
    from shotrans.session import Session
    from tools.replay import replay_report

    s = Session(_white())
    s.set_lines(FRAGS)
    folder = snapshot.write_snapshot(snapshot.build_snapshot(s, now=1_700_000_000.0), tmp_path / "out")
    assert [g.text for g in replay_report(folder).segs] == ["aa bb"]

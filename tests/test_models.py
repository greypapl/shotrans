from shotrans.models import Box, OcrLine, Segment, SegmentResult


def test_box_union_covers_both():
    assert Box(10, 20, 30, 40).union(Box(5, 50, 10, 30)) == Box(5, 20, 35, 60)


def test_box_contains_is_half_open():
    b = Box(10, 10, 5, 5)
    assert b.contains(10, 10)
    assert b.contains(14.9, 14.9)
    assert not b.contains(15, 12)
    assert not b.contains(12, 15)
    assert not b.contains(9.9, 12)


def test_segment_box_is_union_of_lines():
    seg = Segment(1, "a b", [OcrLine("a", Box(0, 0, 10, 10)), OcrLine("b", Box(2, 12, 20, 10))])
    assert seg.box == Box(0, 0, 22, 22)


def test_segment_result_note_defaults_none():
    assert SegmentResult(1, "x", "vllm").note is None

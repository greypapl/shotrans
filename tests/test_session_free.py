"""Session.service_status 的新优先级：none > microsoft（免费翻译层）> fallback > vllm。"""
from PIL import Image

from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.session import Session


def _three():
    s = Session(Image.new("RGB", (300, 200), "white"))
    s.set_lines([OcrLine("Alpha one", Box(10, 10, 150, 20)),
                 OcrLine("Beta two", Box(10, 70, 150, 20)),
                 OcrLine("Gamma three", Box(10, 130, 150, 20))])
    assert len(s.segments) == 3
    return s


def test_free_alone():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "microsoft"))
    assert s.service_status() == "microsoft"


def test_free_beats_vllm():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "microsoft"))
    s.apply(s.generation, SegmentResult(2, "乙", "vllm"))
    assert s.service_status() == "microsoft"


def test_free_beats_fallback_regardless_of_order():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "fallback"))
    s.apply(s.generation, SegmentResult(2, "乙", "microsoft"))
    s.apply(s.generation, SegmentResult(3, "丙", "fallback"))
    assert s.service_status() == "microsoft"


def test_none_beats_free():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "microsoft"))
    s.apply(s.generation, SegmentResult(2, "Beta two", "none", "翻译服务不可用"))
    assert s.service_status() == "none"


def test_history_entry_records_free():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "microsoft"))
    assert s.history_entry()["service"] == "microsoft"

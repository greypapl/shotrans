"""Session.service_status 的新优先级：none > tencent > fallback > vllm。"""
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


def test_tencent_alone():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "tencent"))
    assert s.service_status() == "tencent"


def test_tencent_beats_vllm():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "tencent"))
    s.apply(s.generation, SegmentResult(2, "乙", "vllm"))
    assert s.service_status() == "tencent"


def test_tencent_beats_fallback_regardless_of_order():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "fallback"))
    s.apply(s.generation, SegmentResult(2, "乙", "tencent"))
    s.apply(s.generation, SegmentResult(3, "丙", "fallback"))
    assert s.service_status() == "tencent"


def test_none_beats_tencent():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "tencent"))
    s.apply(s.generation, SegmentResult(2, "Beta two", "none", "翻译服务不可用"))
    assert s.service_status() == "none"


def test_history_entry_records_tencent():
    s = _three()
    s.apply(s.generation, SegmentResult(1, "甲", "tencent"))
    assert s.history_entry()["service"] == "tencent"

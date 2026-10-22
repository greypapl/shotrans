"""OCR 单引擎降级：装了哪个语言包用哪个，两个都缺才报错。识别引擎用假的（不调真 OCR）。"""
import pytest
from PIL import Image

from shotrans import ocr
from shotrans.models import Box


class _Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class _Word:
    def __init__(self, text, rect):
        self.text, self.bounding_rect = text, rect


class _Line:
    def __init__(self, words):
        self.words = words


class _Result:
    def __init__(self, lines):
        self.lines = lines


def _result(*words):
    """一行，每个词宽 50、间隔 60，从 x=10 开始（放大后图上的坐标）。"""
    return _Result([_Line([_Word(w, _Rect(10 + i * 60, 10, 50, 20)) for i, w in enumerate(words)])])


@pytest.fixture
def engines(monkeypatch):
    calls = []
    state = {"langs": [], "results": {}}

    async def fake_png(png, tag):
        calls.append(tag)
        return state["results"][tag]

    monkeypatch.setattr(ocr, "available_languages", lambda: list(state["langs"]))
    monkeypatch.setattr(ocr, "_max_dim", lambda: 10000)
    monkeypatch.setattr(ocr, "_recognize_png", fake_png)
    state["calls"] = calls
    return state


IMG = Image.new("RGB", (100, 40), "white")  # 小图：放大 2 倍识别


def test_both_installed_runs_both_and_reports_nothing_missing(engines):
    engines["langs"] = ["en-US", "zh-Hans-CN", "ja"]
    engines["results"] = {"en-US": _result("Hello", "world"), "zh-Hans-CN": _result("Hello", "world")}
    lines = ocr.recognize(IMG)
    assert engines["calls"] == ["en-US", "zh-Hans-CN"]
    assert lines.missing == ()
    assert [l.text for l in lines] == ["Hello world"]


def test_only_english_uses_english_only(engines):
    engines["langs"] = ["en-US"]
    engines["results"] = {"en-US": _result("Hello", "world")}
    lines = ocr.recognize(IMG)
    assert engines["calls"] == ["en-US"]
    assert lines.missing == ("zh-Hans-CN",)
    assert [l.text for l in lines] == ["Hello world"]


def test_only_chinese_uses_chinese_only(engines):
    engines["langs"] = ["zh-Hans-CN"]
    engines["results"] = {"zh-Hans-CN": _result("抑郁", "症")}
    lines = ocr.recognize(IMG)
    assert engines["calls"] == ["zh-Hans-CN"]
    assert lines.missing == ("en-US",)
    assert [l.text for l in lines] == ["抑郁症"]


def test_single_engine_coordinates_scaled_back(engines):
    engines["langs"] = ["en-US"]
    engines["results"] = {"en-US": _result("Hello", "world")}
    lines = ocr.recognize(IMG)
    assert lines[0].box == Box(5, 5, 55, 10)  # 放大 2 倍图上的 (10,10)-(120,30) 换回原图


def test_neither_installed_raises_without_calling_engine(engines):
    engines["langs"] = ["ja"]
    with pytest.raises(ocr.OcrError) as ei:
        ocr.recognize(IMG)
    assert "en-US" in str(ei.value) and "zh-Hans-CN" in str(ei.value)
    assert engines["calls"] == []


def test_result_is_a_real_list(engines):
    engines["langs"] = ["en-US"]
    engines["results"] = {"en-US": _result("Hi")}
    lines = ocr.recognize(IMG)
    assert isinstance(lines, list) and list(lines) == [lines[0]]


def test_ocrlines_defaults():
    assert ocr.OcrLines().missing == () and list(ocr.OcrLines()) == []

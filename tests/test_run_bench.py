"""bench 报告：两个错字率都列出，关卡（汇总第一行）用顺序无关的那个。"""
from tools.run_bench import write_report


def _row(i, cer, bag, miss, extra):
    return {"id": i, "direction": "en2zh", "cer": cer, "bag_cer": bag, "bag_missing": miss,
            "bag_extra": extra, "ocr_ms": 100.0, "min_contrast": 7.0, "truncated": 0}


def test_report_lists_both_metrics_and_gates_on_order_free(tmp_path):
    rows = [_row("01", 0.40, 0.02, 3, 1), _row("02", 0.20, 0.04, 2, 5),
            {"id": "12", "direction": "en2zh", "cer": None, "bag_cer": None, "bag_missing": None,
             "bag_extra": None, "ocr_ms": 50.0, "min_contrast": 6.0, "truncated": 0}]
    out = tmp_path / "report.md"
    write_report(rows, out)
    text = out.read_text(encoding="utf-8")
    assert "| 01 | en2zh | 2.00% | 3/1 | 40.00% |" in text
    assert "| 12 | en2zh | - | - | - |" in text
    assert "- 平均错字率（顺序无关，2 张有真值）：3.00%（关卡标准 ≤ 3%）" in text
    assert "- 平均错字率（按顺序，参考）：30.00%" in text


class _NoneThenRealRouter:
    """先立刻回一段 service=none 的原文，睡 50 毫秒后再回真译文。"""

    def translate(self, segs, direction, on_result):
        import time

        from shotrans.models import SegmentResult
        on_result(SegmentResult(1, "Hello", "none", "翻译服务不可用"))
        time.sleep(0.05)
        on_result(SegmentResult(2, "世界", "vllm"))


class _AllNoneRouter:
    def translate(self, segs, direction, on_result):
        from shotrans.models import SegmentResult
        for s in segs:
            on_result(SegmentResult(s.idx, s.text, "none", "翻译服务不可用"))


def _stage_full_with(router, tmp_path, monkeypatch):
    from PIL import Image

    from shotrans.models import Box, OcrLine
    from tools import run_bench as rb

    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(rb, "OUT", out)
    monkeypatch.setattr(rb, "BENCH", tmp_path)
    monkeypatch.setattr(rb, "recognize", lambda img: [OcrLine("Hello", Box(10, 10, 120, 24)),
                                                      OcrLine("World", Box(10, 60, 120, 24))])
    monkeypatch.setattr(rb, "_router", lambda: router)
    return rb.stage_full("98", Image.new("RGB", (300, 100), "white"))


def test_first_ms_ignores_none_results(tmp_path, monkeypatch):
    row = _stage_full_with(_NoneThenRealRouter(), tmp_path, monkeypatch)
    assert row["first_ms"] >= 45  # 从第二段（真译文）才开始算


def test_first_ms_none_when_only_none_results(tmp_path, monkeypatch):
    row = _stage_full_with(_AllNoneRouter(), tmp_path, monkeypatch)
    assert row["first_ms"] is None
    assert row["service"] == "none"


class _FakeRouter:
    """不联网的路由：每段立刻回一个固定译文，服务名 vllm。"""

    def translate(self, segs, direction, on_result):
        from shotrans.models import SegmentResult
        for s in segs:
            on_result(SegmentResult(s.idx, "你好世界", "vllm"))


def test_stage_full_records_timing_service_and_writes_image(tmp_path, monkeypatch):
    from PIL import Image

    from shotrans.models import Box, OcrLine
    from tools import run_bench as rb

    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(rb, "OUT", out)
    monkeypatch.setattr(rb, "BENCH", tmp_path)  # 没有 truth 目录 → 不算错字率
    monkeypatch.setattr(rb, "recognize", lambda img: [OcrLine("Hello world", Box(10, 10, 120, 24))])
    monkeypatch.setattr(rb, "_router", lambda: _FakeRouter())

    row = rb.stage_full("99", Image.new("RGB", (300, 100), "white"))

    assert row["first_ms"] is not None and row["first_ms"] >= 0
    assert row["total_ms"] is not None and row["total_ms"] >= row["first_ms"]
    assert row["service"] == "vllm"
    assert (out / "99_ours.png").exists()
    assert row["min_contrast"] is not None

    report = tmp_path / "report.md"
    rb.write_report([row], report)
    line = next(ln for ln in report.read_text(encoding="utf-8").splitlines() if ln.startswith("| 99 |"))
    cells = [c.strip() for c in line.strip("|").split("|")]
    assert cells[6] != "-" and cells[7] != "-"   # 首段ms、全部ms 有数
    assert cells[-1] == "vllm"                    # 服务列


def test_stage_ocr_redraws_even_identical_text(tmp_path, monkeypatch):
    # 「原样重画」关卡：译文就是原文，也要抹了再画（Task 19 起正式翻译时相同的段不动）
    from PIL import Image

    from shotrans.models import Box, OcrLine
    from tools import run_bench as rb

    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(rb, "OUT", out)
    monkeypatch.setattr(rb, "BENCH", tmp_path)
    monkeypatch.setattr(rb, "recognize", lambda img: [OcrLine("Hello world", Box(10, 10, 120, 24))])
    row = rb.stage_ocr("97", Image.new("RGB", (300, 100), "white"))
    assert row["min_contrast"] is not None     # 有排版记录 = 画了


class _UnprotectedRouter:
    """第 1 段是腾讯不带占位符的译文（要画），第 2 段是占位符丢失的原文（不画）。"""

    def translate(self, segs, direction, on_result):
        from shotrans.models import SegmentResult
        on_result(SegmentResult(segs[0].idx, "你好", "tencent", "引用/术语可能未保持原样"))
        on_result(SegmentResult(segs[1].idx, segs[1].text, "tencent", "占位符丢失，显示原文"))


def test_stage_full_draws_unprotected_but_not_lost(tmp_path, monkeypatch):
    from PIL import Image

    row = _stage_full_with(_UnprotectedRouter(), tmp_path, monkeypatch)
    img = Image.open(tmp_path / "out" / "98_ours.png")
    assert img.getpixel((129, 10)) == (245, 158, 11)  # 两段都有角标
    assert img.getpixel((129, 60)) == (245, 158, 11)
    assert row["notes"] == {1: "引用/术语可能未保持原样", 2: "占位符丢失，显示原文"}
    assert row["truncated"] == 0 and row["min_contrast"] is not None  # 只有第 1 段画了译文（有 placement）
    box2 = [img.getpixel((x, y)) for x in range(10, 120) for y in range(60, 84)]
    assert all(p == (255, 255, 255) for p in box2)  # 第 2 段没画字

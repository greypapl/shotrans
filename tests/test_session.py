import numpy as np
from PIL import Image

from shotrans.models import Box, OcrLine, SegmentResult
from shotrans.session import Session


def _session(lines=None):
    s = Session(Image.new("RGB", (300, 120), "white"))
    s.set_lines(lines if lines is not None else [
        OcrLine("Hello world", Box(10, 10, 150, 20)),
        OcrLine("Second para", Box(10, 60, 150, 20)),
    ])
    return s


def test_set_lines_groups_and_detects_direction():
    s = _session()
    assert [x.text for x in s.segments] == ["Hello world", "Second para"]
    assert s.direction == "en2zh" and s.target == "zh"


def test_chinese_lines_give_zh2en():
    s = _session([OcrLine("抑郁症的早期识别", Box(10, 10, 150, 20))])
    assert s.direction == "zh2en" and s.target == "en"


def test_apply_ignores_stale_generation():
    s = _session()
    old = s.generation
    s.set_mode("line")
    assert s.apply(old, SegmentResult(1, "x", "vllm")) is False
    assert s.results == {}
    assert s.apply(s.generation, SegmentResult(1, "x", "vllm")) is True


def test_flip_direction_survives_regroup():
    s = _session()
    s.flip_direction()
    s.set_mode("line")
    assert s.direction == "zh2en"


def test_flip_clears_results():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "x", "vllm"))
    s.flip_direction()
    assert s.results == {}


def test_service_status_priority():
    s = _session()
    assert s.service_status() == "pending"
    g = s.generation
    s.apply(g, SegmentResult(1, "x", "vllm"))
    assert s.service_status() == "vllm"
    s.apply(g, SegmentResult(2, "y", "fallback"))
    assert s.service_status() == "fallback"
    s.apply(g, SegmentResult(2, "Second para", "none", "翻译服务不可用"))
    assert s.service_status() == "none"


def test_service_status_empty():
    assert _session([]).service_status() == "empty"


def test_display_switches_between_original_and_translation():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "你好世界", "vllm"))
    translated = np.asarray(s.display_image())
    assert (translated != np.asarray(s.image)).any()
    s.toggle_view()
    assert s.display_image() is s.image


def test_results_with_note_not_drawn():
    # 改动说明：带 note 的段现在会在框右上角画一个 6 像素的角标（规格 §5.3「在该段角落标出提示」），
    # 所以不再整图不变；这里改为「角标以外的像素都不变」，仍然守住「不把译文画上去」。
    s = _session()
    s.apply(s.generation, SegmentResult(1, "Hello world", "vllm", "占位符丢失，显示原文"))
    out, src = np.asarray(s.display_image()).copy(), np.asarray(s.image)
    out[10:16, 154:160] = src[10:16, 154:160]  # 段 1 的框是 (10,10,150,20)，右上角 6x6 留给角标
    assert (out == src).all()


AMBER = (245, 158, 11)


def test_noted_segment_gets_corner_marker():
    s = _session()
    _a_result(s, 1, "Hello world", "vllm", "占位符丢失，显示原文")
    img = s.display_image()
    assert img.getpixel((159, 10)) == AMBER   # 右上角顶点
    assert img.getpixel((158, 11)) == AMBER   # 三角形内部
    assert img.getpixel((154, 10)) == AMBER   # 上边最左端（边长 6）
    assert img.getpixel((153, 10)) == (255, 255, 255)
    assert img.getpixel((159, 16)) == (255, 255, 255)
    assert img.getpixel((155, 14)) == (255, 255, 255)  # 斜边外侧


def test_segment_without_note_gets_no_marker():
    s = _session()
    _a_result(s, 2, "Second para", "none")  # 服务名 none 但没有 note：不画
    _a_result(s, 1, "你好世界", "vllm")
    img = s.display_image()
    assert img.getpixel((159, 60)) == (255, 255, 255)
    assert img.getpixel((159, 10)) != AMBER


def test_hover_text_appends_note_in_translation_view():
    s = _session()
    _a_result(s, 1, "Hello world", "vllm", "占位符丢失，显示原文")
    assert s.hover_text(s.segments[0]) == "Hello world\n（占位符丢失，显示原文）"


def test_hover_text_appends_note_in_original_view():
    s = _session()
    _a_result(s, 1, "Hello world", "none", "翻译服务不可用")
    s.toggle_view()
    assert s.hover_text(s.segments[0]) == "Hello world\n（翻译服务不可用）"


def test_hover_text_without_note_has_no_suffix():
    s = _session()
    _a_result(s, 1, "你好世界", "vllm")
    s.toggle_view()
    assert s.hover_text(s.segments[0]) == "你好世界"


def test_hover_text_by_view():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "你好世界", "vllm"))
    seg = s.segments[0]
    assert s.hover_text(seg) == "Hello world"
    s.toggle_view()
    assert s.hover_text(seg) == "你好世界"


def test_hover_text_shows_full_translation_when_truncated():
    s = _session([OcrLine("Hi", Box(10, 10, 30, 12))])
    long = "很长的译文" * 100   # Task 19：译文会延伸到框外的空白，原来的 20 遍已能放下，加到 100 遍才截断
    s.apply(s.generation, SegmentResult(1, long, "vllm"))
    assert s.hover_text(s.segments[0]) == f"{long}\n——\nHi"


def test_segment_at():
    s = _session()
    assert s.segment_at(20, 15).idx == 1
    assert s.segment_at(20, 65).idx == 2
    assert s.segment_at(250, 100) is None


def test_copy_texts_fall_back_to_original():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "你好世界", "vllm"))
    assert s.all_translation() == "你好世界\n\nSecond para"
    assert s.all_original() == "Hello world\n\nSecond para"


def test_retranslation_undo():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "旧", "vllm"))
    assert not s.can_undo(1)
    s.apply_retranslation(SegmentResult(1, "新", "fallback"))
    assert s.results[1].text == "新" and s.can_undo(1)
    assert s.undo_retranslation(1) is True
    assert s.results[1].text == "旧"
    assert s.undo_retranslation(1) is False


def test_history_entry_shape():
    s = _session()
    s.apply(s.generation, SegmentResult(1, "你好世界", "vllm"))
    e = s.history_entry()
    assert e["direction"] == "en2zh" and e["service"] == "vllm"
    assert e["segments"] == [{"src": "Hello world", "dst": "你好世界"}, {"src": "Second para", "dst": None}]
    assert len(e["time"]) == 19


def _a_result(s, idx=1, text="你好世界", service="vllm", note=None):
    return s.apply(s.generation, SegmentResult(idx, text, service, note))


def test_set_mode_same_mode_is_noop():
    s = _session()
    _a_result(s)
    g = s.generation
    s.set_mode("para")
    assert s.generation == g and 1 in s.results


def test_set_mode_change_regroups_and_clears():
    s = _session([
        OcrLine("Line one of text", Box(10, 10, 150, 20)),
        OcrLine("line two of text", Box(10, 32, 150, 20)),
    ])
    assert len(s.segments) == 1
    g = s.generation
    _a_result(s)
    s.set_mode("line")
    assert s.mode == "line" and len(s.segments) == 2
    assert s.generation == g + 1 and s.results == {}


def test_flip_bumps_generation_and_flips_twice():
    s = _session()
    g = s.generation
    s.flip_direction()
    assert s.generation == g + 1 and s.direction == "zh2en" and s.target == "en"
    s.flip_direction()
    assert s.direction == "en2zh" and s.target == "zh"


def test_forced_direction_kept_on_new_lines():
    s = _session()
    s.flip_direction()
    s.set_lines([OcrLine("Plain English sentence here", Box(10, 10, 150, 20))])
    assert s.direction == "zh2en"


def test_unforced_direction_redetected_on_new_lines():
    s = _session()
    s.set_lines([OcrLine("抑郁症的早期识别", Box(10, 10, 150, 20))])
    assert s.direction == "zh2en"
    s.set_lines([OcrLine("Plain English sentence here", Box(10, 10, 150, 20))])
    assert s.direction == "en2zh"


def test_set_lines_copies_input_list():
    lines = [OcrLine("Hello world", Box(10, 10, 150, 20))]
    s = Session(Image.new("RGB", (300, 120), "white"))
    s.set_lines(lines)
    lines.clear()
    assert len(s.lines) == 1


def test_set_lines_bumps_generation_and_clears_results():
    s = _session()
    _a_result(s)
    g = s.generation
    s.set_lines([OcrLine("Other", Box(10, 10, 80, 20))])
    assert s.generation == g + 1 and s.results == {}


def test_stale_apply_does_not_invalidate_cache():
    s = _session()
    _a_result(s)
    first = s.display_image()
    assert s.apply(s.generation - 1, SegmentResult(2, "旧", "vllm")) is False
    assert s.display_image() is first


def test_render_cached_between_calls():
    s = _session()
    _a_result(s)
    assert s.display_image() is s.display_image()


def test_apply_invalidates_render_cache():
    s = _session()
    _a_result(s, 1)
    first = s.display_image()
    _a_result(s, 2, "第二段")
    second = s.display_image()
    assert second is not first
    assert (np.asarray(second) != np.asarray(first)).any()


def test_retranslation_invalidates_render_cache():
    s = _session()
    _a_result(s, 1, "旧")
    first = s.display_image()
    s.apply_retranslation(SegmentResult(1, "完全不同的新译文", "fallback"))
    second = s.display_image()
    assert second is not first
    assert (np.asarray(second) != np.asarray(first)).any()


def test_undo_invalidates_render_cache():
    s = _session()
    _a_result(s, 1, "旧")
    old_pixels = np.asarray(s.display_image()).copy()
    s.apply_retranslation(SegmentResult(1, "完全不同的新译文", "fallback"))
    s.display_image()
    assert s.undo_retranslation(1) is True
    assert (np.asarray(s.display_image()) == old_pixels).all()


def test_flip_invalidates_render_cache():
    s = _session()
    _a_result(s)
    translated = s.display_image()
    s.flip_direction()
    after = s.display_image()
    assert after is not translated
    assert (np.asarray(after) == np.asarray(s.image)).all()


def test_regroup_invalidates_render_cache():
    s = _session()
    _a_result(s)
    translated = s.display_image()
    s.set_mode("line")
    assert (np.asarray(s.display_image()) == np.asarray(s.image)).all()
    assert s.display_image() is not translated


def test_toggle_view_roundtrip_keeps_translation():
    s = _session()
    _a_result(s)
    t = s.display_image()
    s.toggle_view()
    assert s.show_original is True
    s.toggle_view()
    assert s.show_original is False and s.display_image() is t


def test_none_service_result_not_drawn():
    s = _session()
    _a_result(s, 1, "Hello world", "none")
    assert (np.asarray(s.display_image()) == np.asarray(s.image)).all()


def test_can_undo_false_after_regroup():
    s = _session()
    _a_result(s, 1, "旧")
    s.apply_retranslation(SegmentResult(1, "新", "fallback"))
    assert s.can_undo(1)
    s.set_mode("line")
    assert not s.can_undo(1)


def test_can_undo_false_after_flip_and_set_lines():
    s = _session()
    _a_result(s, 1, "旧")
    s.apply_retranslation(SegmentResult(1, "新", "fallback"))
    s.flip_direction()
    assert not s.can_undo(1)
    s.apply_retranslation(SegmentResult(1, "a", "vllm"))
    s.apply_retranslation(SegmentResult(1, "b", "vllm"))
    assert s.can_undo(1)
    s.set_lines([OcrLine("Other", Box(10, 10, 80, 20))])
    assert not s.can_undo(1)


def test_retranslation_without_previous_result_has_no_undo():
    s = _session()
    s.apply_retranslation(SegmentResult(1, "新", "fallback"))
    assert s.results[1].text == "新"
    assert not s.can_undo(1)
    assert s.undo_retranslation(1) is False
    assert s.results[1].text == "新"


def test_multi_level_undo_and_isolation_between_segments():
    s = _session()
    _a_result(s, 1, "一")
    _a_result(s, 2, "甲")
    s.apply_retranslation(SegmentResult(1, "二", "vllm"))
    s.apply_retranslation(SegmentResult(1, "三", "vllm"))
    assert not s.can_undo(2)
    assert s.undo_retranslation(1) is True and s.results[1].text == "二"
    assert s.can_undo(1)
    assert s.undo_retranslation(1) is True and s.results[1].text == "一"
    assert not s.can_undo(1)
    assert s.results[2].text == "甲"


def test_undo_unknown_idx_is_false():
    s = _session()
    assert s.undo_retranslation(99) is False
    assert s.can_undo(99) is False


def test_segment_at_overlapping_boxes_first_segment_wins():
    s = _session()
    from shotrans.models import Segment
    a = Segment(1, "A", [OcrLine("A", Box(0, 0, 100, 50))])
    b = Segment(2, "B", [OcrLine("B", Box(50, 20, 100, 50))])
    s.segments = [a, b]
    assert s.segment_at(60, 30) is a
    assert s.segment_at(120, 60) is b


def test_segment_at_outside_all_boxes_between_paragraphs():
    s = _session()
    assert s.segment_at(20, 45) is None


def test_hover_text_not_truncated_returns_only_original():
    s = _session()
    _a_result(s, 1, "你好世界")
    assert s.hover_text(s.segments[0]) == "Hello world"


def test_hover_text_original_view_without_result_is_empty():
    s = _session()
    s.toggle_view()
    assert s.hover_text(s.segments[0]) == ""


def test_hover_text_truncated_without_result_returns_original():
    s = _session([OcrLine("Hi", Box(10, 10, 30, 12))])
    assert s.hover_text(s.segments[0]) == "Hi"


def test_translation_of_uses_result_or_original():
    s = _session()
    _a_result(s, 1, "你好世界")
    assert s.translation_of(s.segments[0]) == "你好世界"
    assert s.translation_of(s.segments[1]) == "Second para"


def test_service_status_fallback_beats_vllm_regardless_of_order():
    s = _session()
    g = s.generation
    s.apply(g, SegmentResult(1, "y", "fallback"))
    s.apply(g, SegmentResult(2, "x", "vllm"))
    assert s.service_status() == "fallback"


def test_service_status_none_beats_fallback():
    s = _session()
    g = s.generation
    s.apply(g, SegmentResult(1, "y", "fallback"))
    s.apply(g, SegmentResult(2, "Second para", "none", "翻译服务不可用"))
    assert s.service_status() == "none"


def test_service_status_skipped_when_only_formula_segments():
    # 改动说明：原先只有公式行也报 "empty"（界面显示「没有识别到文字」，与事实不符），现在单独报 "skipped"
    s = _session([OcrLine("x = y + 1", Box(10, 10, 100, 20))])
    assert s.segments[0].skip is True
    assert s.service_status() == "skipped"


def test_service_status_pending_with_translatable_and_formula():
    s = _session([OcrLine("x = y + 1", Box(10, 10, 100, 20)),
                  OcrLine("Hello world", Box(10, 80, 100, 20))])
    assert s.service_status() == "pending"


def test_history_entry_time_format_and_status_after_flip():
    import re
    s = _session()
    s.flip_direction()
    e = s.history_entry()
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", e["time"])
    assert e["direction"] == "zh2en" and e["service"] == "pending"
    assert [x["dst"] for x in e["segments"]] == [None, None]


def test_history_entry_session_id_stable_within_session_and_distinct_across():
    s, t = _session(), _session()
    first = s.history_entry()["session"]
    s.flip_direction()
    s.set_mode("line")
    assert s.history_entry()["session"] == first
    assert isinstance(first, str) and first
    assert t.history_entry()["session"] != first

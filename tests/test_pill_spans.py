"""Task 22：胶囊占位符换回时，记下每个胶囊落在译文的哪一段（pill_spans），路由各条路径都带上或退回空。
期望值都写死，不从被测代码里读。"""
from shotrans.models import Box, OcrLine, Pill, Segment
from shotrans.protect import protect, restore_spans
from tests.router_fakes import echo, make, make3, run


def pseg(idx, text, *pills):
    """pills：(文字, 起始下标) 若干。"""
    return Segment(idx, text, [OcrLine(text, Box(0, idx * 30, 300, 20))],
                   pills=tuple(Pill(t, Box(0, 0, 10, 10), s) for t, s in pills))


def swap_order(svc, jobs, on):
    """把「A ⟨1⟩ B ⟨2⟩」译成「⟨2⟩ 和 ⟨1⟩」：译文里胶囊顺序和原文相反。"""
    for j in jobs:
        on(j.idx, "⟨2⟩ 和 ⟨1⟩")


# ---------- protect / restore_spans ----------

def test_protect_records_which_literal_each_placeholder_is():
    # 胶囊按 pills 的顺序给（第 0 个在后面、第 1 个在前面）；占位符按位置编号：⟨1⟩ 是第 1 个胶囊
    p = protect("aa and bb", (), (("bb", 7), ("aa", 0)))
    assert p.text == "⟨1⟩ and ⟨2⟩"
    assert p.pills == {"⟨1⟩": 1, "⟨2⟩": 0}


def test_protect_records_literal_index_without_positions():
    # 没给位置、按文字找的胶囊也记下是第几项：第 0 项 bb 在后面，第 1 项 aa 在前面
    p = protect("aa and bb", (), ("bb", "aa"))
    assert p.text == "⟨1⟩ and ⟨2⟩" and p.pills == {"⟨1⟩": 1, "⟨2⟩": 0}


def test_protect_pills_exclude_other_placeholders():
    # URL 也换成占位符，但它不是胶囊
    p = protect("see https://a.org and now.md", (), (("now.md", 22),))
    assert p.slots == {"⟨1⟩": "now.md", "⟨2⟩": "https://a.org"} and p.pills == {"⟨1⟩": 0}


def test_restore_spans_basic():
    assert restore_spans("按 ⟨1⟩ 开始", {"⟨1⟩": "Ctrl+K"}, {"⟨1⟩": 0}) == ("按 Ctrl+K 开始", ((2, 8, 0),))


def test_restore_spans_counts_restored_length_of_earlier_slots():
    # 前面的 URL 占位符换回后变长：胶囊的位置按换回后的译文算（见 13 个字的 https://a.org）
    text, spans = restore_spans("见 ⟨2⟩ 和 ⟨1⟩", {"⟨1⟩": "now.md", "⟨2⟩": "https://a.org"}, {"⟨1⟩": 0})
    assert text == "见 https://a.org 和 now.md" and spans == ((18, 24, 0),)


def test_restore_spans_uses_normalised_placeholders():
    # 模型写成「⟨ 1 ⟩」：位置按归一后的文字算
    assert restore_spans("按 ⟨ 1 ⟩ 开始", {"⟨1⟩": "Ctrl+K"}, {"⟨1⟩": 0}) == ("按 Ctrl+K 开始", ((2, 8, 0),))


def test_restore_spans_leaves_unknown_placeholder_alone():
    # 模型多写了一个不存在的 ⟨9⟩：和 restore 一样原样留着，不影响胶囊位置
    assert restore_spans("⟨9⟩ ⟨1⟩", {"⟨1⟩": "ab"}, {"⟨1⟩": 0}) == ("⟨9⟩ ab", ((4, 6, 0),))


def test_restore_spans_drops_pill_whose_placeholder_repeats():
    # ⟨1⟩ 出现两次：不知道胶囊是哪一处，这个胶囊不给位置；⟨2⟩ 照常
    text, spans = restore_spans("⟨1⟩ ⟨1⟩ ⟨2⟩", {"⟨1⟩": "ab", "⟨2⟩": "cd"}, {"⟨1⟩": 0, "⟨2⟩": 1})
    assert text == "ab ab cd" and spans == ((6, 8, 1),)


def test_restore_spans_gives_up_when_sequential_restore_differs():
    # 保留原样的内容里本身带着像占位符的字：restore 逐个替换会把它也换掉，逐处还原的结果对不上——一个位置也不给
    text, spans = restore_spans("⟨1⟩ x ⟨2⟩", {"⟨1⟩": "⟨2⟩", "⟨2⟩": "y"}, {"⟨2⟩": 0})
    assert text == "y x y" and spans == ()


def test_restore_spans_control_without_nested_placeholder():
    # 对照：同样的结构，内容里没有像占位符的字——照常给位置
    assert restore_spans("⟨1⟩ x ⟨2⟩", {"⟨1⟩": "z", "⟨2⟩": "y"}, {"⟨2⟩": 0}) == ("z x y", ((4, 5, 0),))


# ---------- 路由 ----------

def test_router_result_carries_pill_spans_in_pill_order():
    router, v, _ = make(swap_order)
    res, _ = run(router, [pseg(1, "aa and bb", ("bb", 7), ("aa", 0))])
    # ⟨2⟩ 是 bb（第 0 个胶囊），⟨1⟩ 是 aa（第 1 个）
    assert res[1].text == "bb 和 aa" and res[1].pill_spans == ((0, 2, 0), (5, 7, 1))


def test_router_cache_hit_keeps_pill_spans():
    router, v, _ = make(swap_order)
    s = pseg(1, "aa and bb", ("bb", 7), ("aa", 0))
    run(router, [s])
    res, _ = run(router, [s])
    assert len(v.calls) == 1                     # 第二次读的缓存
    assert res[1].pill_spans == ((0, 2, 0), (5, 7, 1))


def test_retranslate_one_carries_pill_spans():
    router, v, m = make(echo("V:"), echo("M:"))
    out = router.retranslate_one(pseg(1, "run now.md ok", ("now.md", 4)), "en2zh", "vllm")
    assert (out.text, out.service, out.pill_spans) == ("M:run now.md ok", "fallback", ((6, 12, 0),))


def test_tencent_with_placeholders_carries_pill_spans():
    r, v, m, t = make3(None, None, echo("T:"))
    res, _ = run(r, [pseg(1, "run now.md ok", ("now.md", 4))])
    assert (res[1].service, res[1].pill_spans) == ("tencent", ((6, 12, 0),))


def test_tencent_unprotected_retry_has_no_pill_spans():
    # 腾讯两次丢占位符、改翻不带占位符的原文：胶囊没受保护，位置拿不到——空（绘制时当文字画）
    def script(svc, jobs, on):
        for j in jobs:
            on(j.idx, "丢了" if "⟨" in j.text else "T:" + j.text)

    r, v, m, t = make3(None, None, script)
    res, _ = run(r, [pseg(1, "run now.md ok", ("now.md", 4))])
    assert res[1].text == "T:run now.md ok" and res[1].note and res[1].pill_spans == ()


def test_segment_without_pills_has_empty_spans():
    router, v, _ = make(echo("V:"))
    res, _ = run(router, [pseg(1, "see https://a.org")])
    assert res[1].text == "V:see https://a.org" and res[1].pill_spans == ()

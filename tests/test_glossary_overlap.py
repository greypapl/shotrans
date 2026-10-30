"""relevant()：一个命中的词条若每一处都落在另一个更长的命中词条里面，就不给它（「无监督学习」不再带出「监督学习」）。
它在别处还单独出现过，就照旧给。mappings 和 keep 放在一起比较。"""
import random
import string
import time

from shotrans import glossary as G
from shotrans.glossary import Glossary, parse_glossary, relevant
from shotrans.protect import protect


def _zh(*pairs):
    return Glossary(mappings=tuple(pairs))


def test_zh_contained_term_dropped():
    g = _zh(("supervised learning", "监督学习"), ("unsupervised learning", "无监督学习"))
    assert relevant(g, "无监督学习的方法", "zh2en") == ([("无监督学习", "unsupervised learning")], [])


def test_zh_two_contained_terms_dropped():
    g = _zh(("gross margin", "毛利率"), ("interest rate", "利率"), ("gross profit", "毛利"))
    assert relevant(g, "本季度毛利率上升", "zh2en") == ([("毛利率", "gross margin")], [])


def test_zh_contained_term_dropped_regardless_of_order():
    g = _zh(("epinephrine", "肾上腺素"), ("norepinephrine", "去甲肾上腺素"))
    assert relevant(g, "去甲肾上腺素水平", "zh2en") == ([("去甲肾上腺素", "norepinephrine")], [])


def test_zh_standalone_occurrence_kept():
    g = _zh(("supervised learning", "监督学习"), ("unsupervised learning", "无监督学习"))
    maps, _ = relevant(g, "无监督学习和监督学习", "zh2en")
    assert maps == [("监督学习", "supervised learning"), ("无监督学习", "unsupervised learning")]


def test_zh_standalone_before_contained_kept():
    g = _zh(("interest rate", "利率"), ("gross margin", "毛利率"))
    maps, _ = relevant(g, "利率和毛利率", "zh2en")
    assert maps == [("利率", "interest rate"), ("毛利率", "gross margin")]


def test_en_contained_phrase_dropped():
    g = Glossary(mappings=(("attention mechanism", "注意力机制"), ("self-attention mechanism", "自注意力机制")))
    assert relevant(g, "a self-attention mechanism here", "en2zh") == (
        [("self-attention mechanism", "自注意力机制")], [])


def test_en_standalone_phrase_kept():
    g = Glossary(mappings=(("attention mechanism", "注意力机制"), ("self-attention mechanism", "自注意力机制")))
    maps, _ = relevant(g, "self-attention mechanism vs attention mechanism", "en2zh")
    assert maps == [("attention mechanism", "注意力机制"), ("self-attention mechanism", "自注意力机制")]


def test_mapping_inside_keep_term_dropped():
    g = Glossary(mappings=(("DAIC", "戴克"),), keep=("DAIC-WOZ",))
    assert relevant(g, "the DAIC-WOZ set", "en2zh") == ([], ["DAIC-WOZ"])


def test_keep_term_inside_mapping_still_returned_and_protected():
    """keep 词不进提示词，只用来换占位符：包含过滤只管映射，keep 照旧全给 protect()（免费翻译层层不认映射，全靠占位符）。"""
    g = Glossary(mappings=(("PHQ-8 score", "PHQ-8 得分"),), keep=("PHQ-8",))
    maps, keep = relevant(g, "the PHQ-8 score", "en2zh")
    assert (maps, keep) == ([("PHQ-8 score", "PHQ-8 得分")], ["PHQ-8"])
    assert list(protect("the PHQ-8 score", keep).slots.values()) == ["PHQ-8"]


def test_keep_term_inside_keep_term_both_returned():
    g = Glossary(keep=("DAIC", "DAIC-WOZ"))
    assert relevant(g, "the DAIC-WOZ set", "en2zh") == ([], ["DAIC", "DAIC-WOZ"])


def test_free_layer_keeps_placeholder_for_keep_inside_mapping():
    from tests.router_fakes import echo, make3, run, seg
    g = Glossary(mappings=(("API key", "API 密钥"),), keep=("API",))
    r, _, _, t = make3(None, None, echo("T"), glossary=g)
    res, _ = run(r, [seg(1, "Rotate the API key now")])
    sent = t.calls[0]["jobs"][0].text
    assert "⟨1⟩" in sent and "API" not in sent, sent
    assert res[1].text == "TRotate the API key now" and res[1].service == "microsoft"


def test_same_span_both_kept():
    """一样长、同一处：谁也不比谁长，都给（原文相同的两条由编辑器的重复检查管）。"""
    g = Glossary(mappings=(("gpu", "显卡"),), keep=("GPU",))
    assert relevant(g, "a GPU", "en2zh") == ([("gpu", "显卡")], ["GPU"])


def test_partial_overlap_both_kept():
    g = _zh(("deep learning", "深度学习"), ("learning rate", "学习率"))
    maps, _ = relevant(g, "深度学习率", "zh2en")
    assert maps == [("深度学习", "deep learning"), ("学习率", "learning rate")]


def test_contained_at_end_and_start_dropped():
    g = _zh(("a", "监督"), ("b", "学习"), ("c", "监督学习"))
    assert relevant(g, "监督学习", "zh2en") == ([("监督学习", "c")], [])


# ---------- 速度 ----------
def _w(rnd, k):
    return "".join(rnd.choice(string.ascii_lowercase) for _ in range(k))


def test_overlap_heavy_text_fast():
    """很多命中、而且层层包含：每个长词条里都套着短词条。"""
    rnd = random.Random(11)
    lines, words = [], []
    for i in range(700):
        a, b, c = _w(rnd, 5), _w(rnd, 6), _w(rnd, 7)
        lines += [f"{a} {b} {c} => 长{i}", f"{a} {b} => 中{i}", f"{b} => 短{i}"]
        words.append(f"{a} {b} {c}")
    g = parse_glossary("\n".join(lines[:2000]))
    text = ". ".join(words[:68])          # 约 1500 字，不截断半个词
    assert 1300 < len(text) < 1700
    start = time.perf_counter()
    maps, _ = relevant(g, text, "en2zh")
    cold = time.perf_counter() - start
    start = time.perf_counter()
    for _ in range(5):
        relevant(g, text, "en2zh")
    warm = (time.perf_counter() - start) / 5
    assert len(maps) == 68 and all(d.startswith("长") for _, d in maps)
    assert cold < 0.2 and warm < 0.2, (cold, warm)


def test_warm_call_runs_regex_only_for_prefilter_hits(monkeypatch):
    """热调用不能对每个词条都跑正则（那是没预筛的慢路径）：数 term_pattern 被调了几次，不靠计时。"""
    rnd = random.Random(3)
    lines = [f"{_w(rnd, 8)} {_w(rnd, 8)} => 译{i}" for i in range(1000)]
    lines += [_w(rnd, 7).upper() for _ in range(1000)]
    g = parse_glossary("\n".join(lines))
    text = "数字 0123456789 与中文，没有任何词条。" * 40
    relevant(g, text, "en2zh")
    calls = []
    real = G.term_pattern
    monkeypatch.setattr(G, "term_pattern", lambda t: (calls.append(t), real(t))[1])
    for d in ("en2zh", "zh2en"):
        assert relevant(g, text, d) == ([], [])
    assert len(calls) == 0, len(calls)

def test_several_nested_terms_all_dropped():
    """「监督」「学习」都在「无监督学习」里：后面那个的起点在「监督」之后，仍要认出它被最长那条包住。"""
    g = _zh(("u", "无监督学习"), ("s", "监督"), ("l", "学习"))
    assert relevant(g, "无监督学习", "zh2en") == ([("无监督学习", "u")], [])

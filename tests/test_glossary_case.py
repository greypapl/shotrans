"""术语匹配的大小写规则（智能大小写）和大术语表下的速度。

规则：英文词条全小写 → 不分大小写；含任何大写字母 → 只认这个大小写（US 不碰正文里的 us）。
中文词条照旧原样匹配。relevant()、占位符保护用同一条规则。"""
import random
import re
import string
import time
import uuid

from shotrans import protect as P
from shotrans.glossary import Glossary, parse_glossary, relevant
from shotrans.protect import protect, term_pattern


# ---------- term_pattern ----------
def test_lowercase_term_matches_any_case():
    pat = term_pattern("depression")
    for s in ("depression", "Depression", "DEPRESSION", "dEpReSsIoN"):
        assert pat.search(f"about {s} here"), s


def test_uppercase_term_matches_only_exact_case():
    pat = term_pattern("GPU")
    assert pat.search("a GPU here")
    for s in ("gpu", "Gpu", "gPU"):
        assert not pat.search(f"a {s} here"), s


def test_mixed_case_term_matches_only_exact_case():
    pat = term_pattern("PyTorch")
    assert pat.search("use PyTorch now")
    for s in ("pytorch", "Pytorch", "PYTORCH"):
        assert not pat.search(f"use {s} now"), s


def test_case_sensitive_term_still_whole_word():
    assert not term_pattern("IT").search("ITEM")
    assert term_pattern("IT").search("the IT team")


def test_lowercase_term_still_whole_word():
    assert not term_pattern("id").search("the identifier")


def test_chinese_term_unchanged():
    assert term_pattern("抑郁").search("重度抑郁障碍")


# ---------- 含大写的词条：小写开头的词，首字母也可大写（句首、标题） ----------
def test_cap_naive_bayes():
    pat = term_pattern("naive Bayes")
    assert pat.search("Naive Bayes works") and pat.search("a naive Bayes model")
    for s in ("naive bayes", "Naive bayes", "NAIVE Bayes", "nAive Bayes"):
        assert not pat.search(s), s


def test_cap_generative_ai():
    pat = term_pattern("generative AI")
    assert pat.search("Generative AI has grown")
    assert not pat.search("generative ai") and not pat.search("Generative Ai")


def test_cap_api_gateway_title_case_only():
    pat = term_pattern("API gateway")
    assert pat.search("the API Gateway service") and pat.search("an API gateway")
    for s in ("api gateway", "Api Gateway", "API GATEWAY", "API gAteway"):
        assert not pat.search(s), s


def test_cap_type_i_error():
    pat = term_pattern("type I error")
    assert pat.search("Type I Error rates") and pat.search("Type I error") and pat.search("a type I error")
    assert not pat.search("type i error") and not pat.search("TYPE I ERROR")


def test_cap_long_covid():
    pat = term_pattern("long COVID")
    assert pat.search("Long COVID patients")
    assert not pat.search("long covid") and not pat.search("Long Covid")


def test_cap_word_after_hyphen():
    assert term_pattern("deep Q-network").search("Deep Q-Network (DQN)")


def test_cap_not_mid_word():
    assert not term_pattern("deep Q-network").search("deep Q-neTwork")


def test_cap_rule_does_not_loosen_uppercase_initial_terms():
    assert not term_pattern("PyTorch").search("pytorch")
    assert not term_pattern("US").search("us") and not term_pattern("US").search("Us")


def test_cap_rule_still_whole_word():
    assert not term_pattern("naive Bayes").search("Naive Bayesian")


def test_cap_rule_in_relevant_and_protect():
    g = Glossary(mappings=(("naive Bayes", "朴素贝叶斯"),), keep=("generative AI",))
    text = "Naive Bayes vs. Generative AI"
    assert relevant(g, text, "en2zh") == ([("naive Bayes", "朴素贝叶斯")], ["generative AI"])
    assert list(protect(text, ["generative AI"]).slots.values()) == ["Generative AI"]


def test_cap_rule_relevant_negative():
    g = Glossary(mappings=(("API gateway", "API 网关"),))
    assert relevant(g, "an api gateway", "en2zh") == ([], [])


# ---------- 占位符保护 ----------
def test_keep_us_does_not_touch_lowercase_us():
    p = protect("Tell us about it. Us too.", ["US"])
    assert p.slots == {}
    assert p.text == "Tell us about it. Us too."


def test_keep_us_protects_exact_us_only():
    p = protect("the US and us", ["US"])
    assert p.text == "the ⟨1⟩ and us"
    assert p.slots == {"⟨1⟩": "US"}


def test_keep_lowercase_term_protects_any_case():
    p = protect("The DAIC-WOZ corpus", ["daic-woz"])
    assert p.slots == {"⟨1⟩": "DAIC-WOZ"}


# ---------- relevant() ----------
def test_relevant_uppercase_keep_not_triggered_by_lowercase():
    g = Glossary(keep=("US", "IT"))
    assert relevant(g, "tell us about it", "en2zh") == ([], [])
    assert relevant(g, "the US IT team", "en2zh") == ([], ["US", "IT"])


def test_relevant_uppercase_mapping_exact_case_only():
    g = Glossary(mappings=(("GPU", "图形处理器"),))
    assert relevant(g, "a gpu", "en2zh") == ([], [])
    assert relevant(g, "a GPU", "en2zh") == ([("GPU", "图形处理器")], [])


def test_relevant_lowercase_mapping_any_case():
    g = Glossary(mappings=(("attention", "注意力"),))
    assert relevant(g, "ATTENTION is all", "en2zh") == ([("attention", "注意力")], [])


def test_relevant_lowercase_term_whole_word_only():
    g = Glossary(mappings=(("id", "编号"),))
    assert relevant(g, "the identifier", "en2zh") == ([], [])


def test_relevant_case_sensitive_term_whole_word_only():
    g = Glossary(keep=("IT",))
    assert relevant(g, "ITEM list", "en2zh") == ([], [])


def test_relevant_chinese_term_en2zh_unchanged():
    g = Glossary(mappings=(("抑郁", "depression"),))
    assert relevant(g, "重度抑郁障碍", "en2zh") == ([("抑郁", "depression")], [])


def test_relevant_zh2en_reversed_unchanged():
    g = Glossary(mappings=(("GPU", "图形处理器"), ("depression", "抑郁")))
    assert relevant(g, "图形处理器上的抑郁研究", "zh2en") == (
        [("图形处理器", "GPU"), ("抑郁", "depression")], [])


def test_relevant_lowercase_term_matches_unicode_case_variants_like_regex():
    """re 的不分大小写会把 ſ 当成 s、İ/ı 当成 i；快速预筛不能比正则本身少认。"""
    g = Glossary(keep=("sit", "bit"))
    text = "ſit and bİt"
    assert term_pattern("sit").search(text) and term_pattern("bit").search(text)
    assert relevant(g, text, "en2zh") == ([], ["sit", "bit"])


def test_relevant_lowercase_term_matches_dotless_i():
    g = Glossary(keep=("bit",))
    assert term_pattern("bit").search("a bıt")
    assert relevant(g, "a bıt", "en2zh") == ([], ["bit"])


def _naive(g, text, direction):
    pairs = g.mappings if direction == "en2zh" else tuple((d, s) for s, d in g.mappings)
    return ([(s, d) for s, d in pairs if term_pattern(s).search(text)],
            [k for k in g.keep if term_pattern(k).search(text)])


def test_relevant_agrees_with_regex_on_random_glossary():
    rnd = random.Random(7)
    pool = ["us", "US", "Us", "it", "IT", "gpu", "GPU", "PyTorch", "pytorch", "id", "ID",
            "deep learning", "Deep Learning", "抑郁", "焦虑", "p-value", "P-value", "sit", "bit"]
    texts = ["Tell US about it, IT and GPU in PyTorch.", "deep LEARNING gives a p-VALUE",
             "ſit bİt bıt identifier ID", "抑郁和焦虑 with Deep Learning", "nothing here"]
    for _ in range(30):
        keep = tuple(rnd.sample(pool, 6))
        maps = tuple((t, f"译{i}") for i, t in enumerate(rnd.sample(pool, 6)))
        g = Glossary(maps, keep)
        for text in texts:
            for d in ("en2zh", "zh2en"):
                assert relevant(g, text, d) == _naive(g, text, d), (g, text, d)


# ---------- 编译次数与速度 ----------
def test_each_term_pattern_compiled_once(monkeypatch):
    tag = uuid.uuid4().hex[:8]
    terms = [f"Zq{tag}{i}" for i in range(5)] + [f"zq{tag}x{i}" for i in range(5)]
    g = Glossary(mappings=tuple((t, "译") for t in terms[:5]), keep=tuple(terms[5:]))
    text = " ".join(terms)
    seen: list[str] = []
    real = re.compile

    def counting(pattern, flags=0):
        if isinstance(pattern, str) and tag in pattern:
            seen.append(pattern)
        return real(pattern, flags)

    monkeypatch.setattr(P.re, "compile", counting)
    for _ in range(3):
        maps, keep = relevant(g, text, "en2zh")
        assert len(maps) == 5 and len(keep) == 5
        protect(text, keep)
    assert len(seen) == len(terms), seen


def _big_glossary(n: int = 2000) -> Glossary:
    rnd = random.Random(3)

    def w(k):
        return "".join(rnd.choice(string.ascii_lowercase) for _ in range(k))

    lines = []
    for i in range(n // 2):
        lines.append(f"{w(rnd.randint(4, 10))} {w(5)} => 术语{i}")
    for i in range(n // 4):
        lines.append(w(rnd.randint(3, 8)).upper())
    for i in range(n - n // 2 - n // 4):
        lines.append(f"{w(6)}{i} => 译{i}")
    return parse_glossary("\n".join(lines))


def _paragraph(n: int = 1500) -> str:
    rnd = random.Random(5)
    words = ["".join(rnd.choice(string.ascii_letters) for _ in range(rnd.randint(2, 9))) for _ in range(400)]
    return " ".join(words)[:n]


def test_relevant_fast_on_2000_entry_glossary():
    g = _big_glossary()
    assert len(g.mappings) + len(g.keep) == 2000
    text = _paragraph()
    for d in ("en2zh", "zh2en"):
        start = time.perf_counter()     # 第一次（冷）也算：截图时就是这一次
        relevant(g, text, d)
        cold = time.perf_counter() - start
        start = time.perf_counter()
        for _ in range(5):
            relevant(g, text, d)
        warm = (time.perf_counter() - start) / 5
        assert cold < 0.2 and warm < 0.2, (d, cold, warm)

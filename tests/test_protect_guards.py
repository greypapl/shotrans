"""protect.py 每条正则分支/守卫的专属测试：每条只被它自己的数据触发。"""
import pytest

from shotrans.protect import MissingPlaceholder, protect, restore


# ---------- URL ----------
def test_url_www_branch():
    p = protect("visit www.example.org now")
    assert p.text == "visit ⟨1⟩ now"
    assert p.slots == {"⟨1⟩": "www.example.org"}


def test_url_http_not_https():
    assert protect("go http://a.org/x ok").slots == {"⟨1⟩": "http://a.org/x"}


def test_url_trailing_comma_excluded():
    p = protect("see https://example.org/a, and more")
    assert p.text == "see ⟨1⟩, and more"
    assert p.slots == {"⟨1⟩": "https://example.org/a"}


def test_url_www_trailing_period_excluded():
    p = protect("at www.example.org/a.")
    assert p.text == "at ⟨1⟩."
    assert p.slots == {"⟨1⟩": "www.example.org/a"}


def test_url_stops_at_close_paren():
    p = protect("(https://x.org/a)b")
    assert p.text == "(⟨1⟩)b"
    assert p.slots == {"⟨1⟩": "https://x.org/a"}


def test_url_stops_at_close_bracket():
    p = protect("[https://x.org/a]b")
    assert p.text == "[⟨1⟩]b"
    assert p.slots == {"⟨1⟩": "https://x.org/a"}


def test_url_does_not_swallow_following_chinese():
    p = protect("详见https://example.org/a，更多内容请阅读本文。")
    assert p.text == "详见⟨1⟩，更多内容请阅读本文。"
    assert p.slots == {"⟨1⟩": "https://example.org/a"}


def test_url_stops_before_chinese_even_when_ascii_follows():
    p = protect("详见https://example.org/a请看abc")
    assert p.text == "详见⟨1⟩请看abc"
    assert p.slots == {"⟨1⟩": "https://example.org/a"}


def test_url_www_does_not_swallow_following_chinese():
    p = protect("访问www.example.org/a获取")
    assert p.text == "访问⟨1⟩获取"
    assert p.slots == {"⟨1⟩": "www.example.org/a"}


# ---------- EMAIL ----------
def test_email_does_not_swallow_adjacent_chinese():
    p = protect("请联系zhang@uni.edu获取数据")
    assert p.text == "请联系⟨1⟩获取数据"
    assert p.slots == {"⟨1⟩": "zhang@uni.edu"}


def test_email_plus_in_local_part():
    assert protect("write a+b@uni.edu now").slots == {"⟨1⟩": "a+b@uni.edu"}


def test_email_needs_dotted_domain():
    p = protect("user@localhost only")
    assert p.text == "user@localhost only" and p.slots == {}


# ---------- 引用编号 ----------
def test_citation_comma_with_space():
    assert protect("see [1, 2] here").slots == {"⟨1⟩": "[1, 2]"}


def test_citation_comma_without_space():
    assert protect("see [1,2] here").slots == {"⟨1⟩": "[1,2]"}


def test_citation_hyphen():
    assert protect("see [3-5] here").slots == {"⟨1⟩": "[3-5]"}


def test_citation_spaced_endash():
    assert protect("see [3 – 5] here").slots == {"⟨1⟩": "[3 – 5]"}


def test_citation_multiple_commas():
    assert protect("see [1, 2, 3] here").slots == {"⟨1⟩": "[1, 2, 3]"}


# ---------- 作者年份 ----------
def test_author_year_needs_capital():
    p = protect("later (see 2020) it")
    assert p.slots == {}


def test_author_year_1990s():
    assert protect("old (Smith, 1999) it").slots == {"⟨1⟩": "(Smith, 1999)"}


def test_author_year_letter_suffix():
    assert protect("it (Smith, 2020a) was").slots == {"⟨1⟩": "(Smith, 2020a)"}


def test_author_year_plain_parenthetical_not_matched():
    t = "(Participants were recruited between 2018 and 2020)"
    assert protect(t).slots == {}


def test_author_year_table_cohort_not_matched():
    assert protect("(see Table 2, 2020 cohort)").slots == {}


def test_author_year_capitalised_sentence_not_matched():
    assert protect("(Results improved in 2020)").slots == {}


@pytest.mark.parametrize("s", [
    "(Smith et al., 2020)", "(Smith, 2020)", "(Smith & Jones, 2019a)",
    "(Smith and Jones 2019)", "(Smith et al., 2020; Lee, 2018)", "(O'Brien-Lee, 2017)",
])
def test_author_year_citation_shapes(s):
    p = protect(f"as shown {s} here")
    assert p.text == "as shown ⟨1⟩ here"
    assert p.slots == {"⟨1⟩": s}


def test_author_year_semicolon_items_all_must_be_citations():
    assert protect("(Smith, 2020; see Table 2)").slots == {}

def test_author_year_does_not_span_nested_parens():
    p = protect("(Smith and (Jones, 2020)")
    assert p.slots == {"⟨1⟩": "(Jones, 2020)"}
    assert p.text == "(Smith and ⟨1⟩"


def test_author_year_year_needs_word_boundary():
    assert protect("(Smith 12020)").slots == {}


# ---------- p 值 ----------
@pytest.mark.parametrize("s", ["P < 0.05", "p > 0.1", "p = .03", "p ≤ 0.05", "p ≥ 0.05", "p<0.05", "p <0.05"])
def test_p_value_variants(s):
    p = protect(f"was {s} here")
    assert p.text == "was ⟨1⟩ here"
    assert p.slots == {"⟨1⟩": s}


def test_p_value_needs_word_boundary():
    p = protect("the step < 0.5 mark")
    assert p.slots == {}


# ---------- r/t/F/z/β ----------
@pytest.mark.parametrize("s", [
    "t(29) = 2.1", "t (29) = 2.1", "F(2, 40) = 3.5", "F(2,40) = 3.5",
    "z = 1.96", "β = -0.32", "t = 2.1", "r=.43", "r = .43",
])
def test_stat_variants(s):
    p = protect(f"was {s} here")
    assert p.text == "was ⟨1⟩ here"
    assert p.slots == {"⟨1⟩": s}


def test_stat_needs_word_boundary():
    assert protect("the bar = .5 mark").slots == {}


# ---------- CI ----------
@pytest.mark.parametrize("s", ["90% CI", "99% CI", "95%CI"])
def test_ci_variants(s):
    assert protect(f"the {s} was").slots == {"⟨1⟩": s}


def test_ci_needs_word_boundary_before():
    assert protect("the 195% CI was").slots == {}


def test_ci_needs_word_boundary_after():
    assert protect("the 95% CIs were").slots == {}


# ---------- keep 词条 ----------
def test_keep_longest_first():
    p = protect("the DAIC-WOZ set", ["DAIC", "DAIC-WOZ"])
    assert p.text == "the ⟨1⟩ set"
    assert p.slots == {"⟨1⟩": "DAIC-WOZ"}


def test_url_slot_wins_over_keep_term_inside_url():
    p = protect("see https://example.org/a now", ["example"])
    assert p.text == "see ⟨1⟩ now"
    assert p.slots == {"⟨1⟩": "https://example.org/a"}


def test_keep_beats_builtin_stat_pattern():
    p = protect("the p < 0.05 threshold", ["p < 0.05 threshold"])
    assert p.text == "the ⟨1⟩"
    assert p.slots == {"⟨1⟩": "p < 0.05 threshold"}


def test_keep_ascii_not_inside_longer_word_before():
    p = protect("xPHQ and PHQ", ["PHQ"])
    assert p.text == "xPHQ and ⟨1⟩"


def test_keep_ascii_not_before_digit():
    p = protect("PHQ9 and PHQ", ["PHQ"])
    assert p.text == "PHQ9 and ⟨1⟩"


def test_keep_ascii_not_after_digit():
    p = protect("9PHQ and PHQ", ["PHQ"])
    assert p.text == "9PHQ and ⟨1⟩"


def test_keep_chinese_inside_ascii_neighbours():
    p = protect("使用abc抑郁def量表", ["抑郁"])
    assert p.text == "使用abc⟨1⟩def量表"


def test_keep_term_regex_chars_literal():
    p = protect("v1x0 and v1.0", ["v1.0"])
    assert p.text == "v1x0 and ⟨1⟩"


def test_keep_term_plus_signs_literal():
    p = protect("C++ lang", ["C++"])
    assert p.text == "⟨1⟩ lang"
    assert p.slots == {"⟨1⟩": "C++"}


def test_keep_empty_and_blank_terms_ignored():
    p = protect("a b  c", ["", "  ", " "])
    assert p.text == "a b  c" and p.slots == {}


# ---------- restore ----------
def test_restore_space_before_only():
    assert restore("a ⟨ 1⟩ b", {"⟨1⟩": "X"}) == "a X b"


def test_restore_space_after_only():
    assert restore("a ⟨1 ⟩ b", {"⟨1⟩": "X"}) == "a X b"


def test_restore_multi_digit_with_spaces():
    assert restore("a ⟨ 10 ⟩ b", {"⟨10⟩": "X"}) == "a X b"


def test_restore_missing_lists_only_missing():
    with pytest.raises(MissingPlaceholder) as ei:
        restore("⟨1⟩ only", {"⟨1⟩": "A", "⟨2⟩": "B"})
    assert ei.value.args[0] == ["⟨2⟩"]

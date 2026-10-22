import pytest

from tools.metrics import bag_cer, bag_errors, cer, levenshtein, normalize


def test_levenshtein():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "abc") == 0


def test_normalize_hyphen_ligature_space():
    assert normalize("depres-\nsion   ﬁt\n") == "depression fit"


def test_normalize_ligature_only():
    assert normalize("ﬁt") == "fit"


def test_normalize_hyphen_only():
    assert normalize("depres-\nsion") == "depression"


def test_normalize_keeps_real_hyphen():
    assert normalize("PHQ-8 score") == "PHQ-8 score"


def test_normalize_keeps_word_hyphen_without_newline():
    assert normalize("well-being") == "well-being"


def test_cer():
    assert cer("abcd", "abcd") == 0.0
    assert cer("abcd", "abxd") == pytest.approx(0.25)
    assert cer("depres-\nsion", "depression") == 0.0
    assert cer("", "") == 0.0


def test_cer_divides_by_reference_length():
    assert cer("abcd", "ab") == pytest.approx(0.5)
    assert cer("ab", "abcd") == pytest.approx(1.0)


def test_cer_empty_ref_nonempty_hyp():
    assert cer("", "abc") == 1.0


# ---------- Task 7b：与顺序无关的字符袋错字率 ----------

def test_bag_cer_ignores_order():
    assert bag_cer("left column\nright column", "right column\nleft column") == 0.0
    assert cer("left column\nright column", "right column\nleft column") > 0.3   # 对照：顺序敏感的那个很高


def test_bag_cer_substitution_counts_once():
    # 一个错字 = 缺一个 + 多一个，按 max(缺, 多) 计 1 次，不是 2 次
    assert bag_cer("abcd", "abxd") == pytest.approx(0.25)


def test_bag_cer_divides_by_reference_length():
    assert bag_cer("abcd", "ab") == pytest.approx(0.5)
    assert bag_cer("ab", "abcd") == pytest.approx(1.0)


def test_bag_cer_counts_multiplicity():
    assert bag_cer("aab", "ab") == pytest.approx(1 / 3)


def test_bag_cer_ignores_whitespace():
    assert bag_cer("抑郁 PHQ 量表", "抑郁PHQ量表") == 0.0
    assert bag_cer("a b", "ab") == 0.0          # 分母也不算空白：真值 2 个字符


def test_bag_cer_normalizes_first():
    assert bag_cer("depres-\nsion ﬁt", "depression fit") == 0.0


def test_bag_cer_empty_reference():
    assert bag_cer("", "") == 0.0
    assert bag_cer(" \n", "") == 0.0
    assert bag_cer("", "a") == 1.0


def test_bag_errors_missing_and_extra():
    assert bag_errors("abcd", "abxxd") == (1, 2)      # 缺 c；多两个 x

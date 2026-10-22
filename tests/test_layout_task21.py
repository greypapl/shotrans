"""Task 21：引用标号不让整行被判成公式；论文常见缩写不被当成 logo 碎片。期望值写死。"""
import pytest

from shotrans.layout import is_formula, is_noise


@pytest.mark.parametrize("text", [
    "pain.[17]",           # 上一轮终审：句末引用
    "word[3,4]",
    "word [12–15]",
    "the results,[1, 2]",
    "word[3-5]",
    "word [3—5]",
    "aa[12–15] bb",           # 引用后面空一格接着是文字（不去掉的话 6/11 是公式字符）
    "aa[12,15]. b",           # 引用后面是句读
])
def test_citation_after_word_is_not_formula(text):
    assert not is_formula(text)


@pytest.mark.parametrize("text", [
    "x = [1, 2] + y",       # 方括号前是 = ，不是词
    "x[1] + y[2]",          # 单个字母后的方括号是下标，不当引用去掉
    "y = x[12]",            # 行尾的单字母下标也不去
    "f(x)[3] = 2",
    "x = (a + b)^2 / 2",
    # 评审 I3：方括号后面紧跟运算符或右括号——代码、公式，不是引用
    "arr[10] = arr[9] + 1",
    "tab[1] = val[2] + 3",
    "data[0] = 1",
    "list[3] == x",
    "ab[1] ≤ cd[2]",
    "f(arr[1])",
])
def test_real_formula_still_formula(text):
    assert is_formula(text)


@pytest.mark.parametrize("abbr", "fig eq tab ref sec vol pp et al cf vs etc".split())
def test_paper_abbreviation_is_word_like(abbr):
    # 「Fig. b」：两块字母，后一块单个字母不像词——前一块是常见缩写，整段就不是噪声
    assert not is_noise(f"{abbr.capitalize()}. b")


def test_real_fragments_are_not_noise():
    assert not is_noise("et al.")
    assert not is_noise("Fig. 3a")
    assert not is_noise("cf. Eq. 5")


def test_logo_fragments_still_noise():
    assert is_noise("ar iv")
    assert is_noise("xq zt")

"""Task 20：按键组合的 OCR 纠错 + 整个组合作为占位符保护。每个守卫用自己的数据；期望值写死。"""
import pytest

from shotrans.protect import fix_key_combos, protect, restore


@pytest.mark.parametrize("src, want", [
    ("按Ctr1+A1t+Q，框选", "按Ctrl+Alt+Q，框选"),          # 用户截图里的真实误识别
    ("press CtrI + AIt + Del now", "press Ctrl + Alt + Del now"),
    ("Shlft+Tab", "Shift+Tab"),
    ("Sh1ft+F5", "Shift+F5"),
    ("W1n+R", "Win+R"),
    ("Ctr|+C", "Ctrl+C"),
])
def test_misread_modifiers_are_fixed_in_combos(src, want):
    assert fix_key_combos(src) == want


@pytest.mark.parametrize("text", [
    "Ctrl+Alt+Q", "CTRL+SHIFT+ESC", "ctrl+c", "Cmd+Shift+4",       # 本来就对：大小写也不动
    "A1 grade", "cell A1t", "A1t is not a key", "Shlft happens",    # 普通文字里的 A1、A1t、Shlft 不改
    "Ctr1 alone", "Ctrl+Click the link",                            # 没有「+ 键」/ 加号后面不是一个键
])
def test_text_outside_combos_is_untouched(text):
    assert fix_key_combos(text) == text


def test_combo_is_protected_as_one_placeholder_with_fixed_spelling():
    p = protect("按Ctr1+A1t+Q，框选区域")
    assert p.text == "按⟨1⟩，框选区域"
    assert p.slots == {"⟨1⟩": "Ctrl+Alt+Q"}
    assert restore("Press ⟨1⟩ and select an area", p.slots) == "Press Ctrl+Alt+Q and select an area"


def test_plain_a1_is_not_protected():
    p = protect("Cell A1 holds the total")
    assert p.text == "Cell A1 holds the total" and p.slots == {}


def test_modifier_plus_word_is_not_protected():
    # Ctrl+Click：加号后面是个词，不是一个键——不能只把「Ctrl+C」换掉，留下「⟨1⟩lick」
    p = protect("Ctrl+Click the link")
    assert p.text == "Ctrl+Click the link" and p.slots == {}


def test_spaced_combo_is_protected_whole():
    p = protect("Use Ctrl + Shift + T to reopen")
    assert p.text == "Use ⟨1⟩ to reopen" and p.slots == {"⟨1⟩": "Ctrl + Shift + T"}

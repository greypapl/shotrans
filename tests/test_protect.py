import pytest

from shotrans.protect import MissingPlaceholder, protect, restore


def test_citation_numbers():
    p = protect("as shown [12] and [3–5].")
    assert p.text == "as shown ⟨1⟩ and ⟨2⟩."
    assert p.slots == {"⟨1⟩": "[12]", "⟨2⟩": "[3–5]"}


def test_author_year():
    assert protect("Prior work (Smith et al., 2020) found").text == "Prior work ⟨1⟩ found"


def test_url_and_email():
    p = protect("See https://example.org/a.pdf or mail a.b@uni.edu.")
    assert p.text == "See ⟨1⟩ or mail ⟨2⟩."
    assert p.slots["⟨1⟩"] == "https://example.org/a.pdf"
    assert p.slots["⟨2⟩"] == "a.b@uni.edu"


def test_p_value():
    assert protect("significant (p < 0.05) here").text == "significant (⟨1⟩) here"


def test_r_value():
    assert protect("correlated, r = .43, with").text == "correlated, ⟨1⟩, with"


def test_confidence_interval():
    assert protect("the 95% CI was wide").text == "the ⟨1⟩ was wide"


def test_keep_terms_case_insensitive_whole_word():
    p = protect("The daic-woz corpus and DAIC-WOZish", ["DAIC-WOZ"])
    assert p.text == "The ⟨1⟩ corpus and DAIC-WOZish"
    assert p.slots == {"⟨1⟩": "daic-woz"}


def test_chinese_keep_term():
    assert protect("使用抑郁量表", ["抑郁"]).text == "使用⟨1⟩量表"


def test_plain_text_untouched():
    p = protect("Nothing special here.")
    assert p.text == "Nothing special here." and p.slots == {}


def test_restore_roundtrip_tolerates_spaces():
    assert restore("见 ⟨ 1 ⟩ 和⟨2⟩", {"⟨1⟩": "[12]", "⟨2⟩": "[3]"}) == "见 [12] 和[3]"


def test_restore_missing_raises():
    with pytest.raises(MissingPlaceholder):
        restore("见", {"⟨1⟩": "[12]"})


def test_slot_10_not_confused_with_1():
    slots = {f"⟨{i}⟩": f"v{i}" for i in range(1, 11)}
    text = " ".join(f"⟨{i}⟩" for i in range(1, 11))
    assert restore(text, slots) == " ".join(f"v{i}" for i in range(1, 11))

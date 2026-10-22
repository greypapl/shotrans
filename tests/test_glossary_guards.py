"""glossary.py 各守卫的专属测试。"""
from shotrans.glossary import parse_glossary, relevant


def test_indented_comment_is_skipped():
    g = parse_glossary("   # indented comment\nPHQ-8\n")
    assert g.keep == ("PHQ-8",) and g.mappings == ()


def test_keep_line_whitespace_stripped():
    assert parse_glossary("   PHQ-8   \n").keep == ("PHQ-8",)


def test_mapping_parts_stripped():
    assert parse_glossary("  a  =>  b  \n").mappings == (("a", "b"),)


def test_mapping_splits_on_first_arrow_only():
    assert parse_glossary("a => b => c\n").mappings == (("a", "b => c"),)


def test_empty_source_dropped():
    g = parse_glossary("=> x\n")
    assert g.mappings == () and g.keep == ()


def test_empty_target_dropped_and_not_kept():
    g = parse_glossary("a =>\n")
    assert g.mappings == () and g.keep == ()


def test_relevant_keeps_glossary_order():
    g = parse_glossary("depression => 抑郁\nanxiety => 焦虑\nDAIC-WOZ\nPHQ-8\n")
    maps, keep = relevant(g, "PHQ-8, anxiety, DAIC-WOZ and depression", "en2zh")
    assert maps == [("depression", "抑郁"), ("anxiety", "焦虑")]
    assert keep == ["DAIC-WOZ", "PHQ-8"]


def test_relevant_zh2en_keep_still_filtered_by_presence():
    g = parse_glossary("PHQ-8\nDAIC-WOZ\n")
    assert relevant(g, "只提到 PHQ-8 量表", "zh2en") == ([], ["PHQ-8"])


def test_relevant_zh2en_absent_terms_dropped():
    g = parse_glossary("depression => 抑郁\nanxiety => 焦虑\n")
    maps, _ = relevant(g, "只有焦虑", "zh2en")
    assert maps == [("焦虑", "anxiety")]

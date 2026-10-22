from shotrans.glossary import Glossary, load_glossary, parse_glossary, relevant

SAMPLE = """# 注释
depression => 抑郁
major depressive disorder => 重度抑郁障碍
DAIC-WOZ
PHQ-8

bad line =>
"""


def test_parse():
    g = parse_glossary(SAMPLE)
    assert g.mappings == (("depression", "抑郁"), ("major depressive disorder", "重度抑郁障碍"))
    assert g.keep == ("DAIC-WOZ", "PHQ-8")


def test_missing_file_is_empty(tmp_path):
    assert load_glossary(tmp_path / "无.txt") == Glossary()


def test_load_utf8_bom(tmp_path):
    p = tmp_path / "术语表.txt"
    p.write_text("PHQ-8\n", encoding="utf-8-sig")
    assert load_glossary(p).keep == ("PHQ-8",)


def test_relevant_en2zh_only_present_terms():
    maps, keep = relevant(parse_glossary(SAMPLE), "Depression and PHQ-8 scores", "en2zh")
    assert maps == [("depression", "抑郁")]
    assert keep == ["PHQ-8"]


def test_relevant_zh2en_reversed():
    maps, keep = relevant(parse_glossary(SAMPLE), "重度抑郁障碍患者", "zh2en")
    assert ("重度抑郁障碍", "major depressive disorder") in maps
    assert ("抑郁", "depression") in maps
    assert keep == []

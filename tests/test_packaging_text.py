"""随包发出去的 使用说明.txt 要让老版本记事本也能正确打开：UTF-8 带 BOM、CRLF 换行。改文档时很容易被编辑器悄悄去掉。"""
from pathlib import Path

GUIDE = Path(__file__).resolve().parents[1] / "packaging" / "使用说明.txt"


def test_guide_starts_with_utf8_bom():
    assert GUIDE.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_guide_uses_crlf_only():
    data = GUIDE.read_bytes()
    assert b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n"), "有不带 \\r 的换行"
    assert data.count(b"\r") == data.count(b"\r\n"), "有单独的 \\r"

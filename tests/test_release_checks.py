"""发布检查：个人数据扫描（正例/反例、容器内、UTF-16、不泄露值）、体积上限、zip。"""
import hashlib
import marshal
import zipfile

import pytest

from tools import release_checks as rc

SECRET = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1"


def _rules(tmp_path, cfg_text=None):
    cfg = None
    if cfg_text is not None:
        cfg = tmp_path / "config.local.toml"
        cfg.write_text(cfg_text, encoding="utf-8")
    return rc.default_rules(cfg)


def test_clean_tree_has_no_hits(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.txt").write_text("hello 192.168.1.1 sk-short C:\\Users\\someone", encoding="utf-8")
    hits, n = rc.scan_tree(tmp_path / "pkg", _rules(tmp_path))
    assert hits == [] and n == 1


def test_each_fixed_rule_fires(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "ip.txt").write_bytes(b"url=http://172.25.0.1:8000")
    (root / "key.bin").write_bytes(b"\x00" + SECRET.encode() + b"\x00")
    (root / "path.txt").write_text("c:/users/GREYPAPL/x", encoding="utf-8")
    hits, _ = rc.scan_tree(root, _rules(tmp_path))
    assert {(h.path, h.rule) for h in hits} == {("ip.txt", "lan_ip"), ("key.bin", "api_key"),
                                                ("path.txt", "author_path")}


def test_utf16_and_inside_zip(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "w.dat").write_bytes("C:\\Users\\greypapl\\x".encode("utf-16-le"))
    with zipfile.ZipFile(root / "base_library.zip", "w") as z:
        z.writestr("m.pyc", b"..172.25.1.2..")
    hits, n = rc.scan_tree(root, _rules(tmp_path))
    assert {(h.path, h.rule) for h in hits} == {("w.dat", "author_path"), ("base_library.zip!m.pyc", "lan_ip")}
    assert n == 2


def test_config_values_become_rules_and_report_hides_value(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "x.txt").write_text("…抑郁症与人工智能研究领域…", encoding="utf-8")
    rules = _rules(tmp_path, '[translate]\ndomain_hint = "抑郁症与人工智能研究领域"\n[vllm]\nmodel = "m"\n')
    hits, _ = rc.scan_tree(root, rules)
    assert [h.rule for h in hits] == ["config_value_translate.domain_hint"]
    report = rc.format_hits(hits)
    assert "抑郁症与人工智能研究领域" not in report and "x.txt" in report


def test_secret_never_in_report(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "k.txt").write_text(SECRET, encoding="utf-8")
    report = rc.format_hits(rc.scan_tree(root, _rules(tmp_path))[0])
    assert SECRET not in report and SECRET[3:] not in report and "api_key" in report


def test_extractor_used_for_matching_files(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "app.exe").write_bytes(b"compressed-garbage")
    ext = [(lambda p: p.suffix == ".exe", lambda p: iter([("PYZ!shotrans.app", b"172.25.0.1")]))]
    hits, n = rc.scan_tree(root, _rules(tmp_path), extractors=ext)
    assert [(h.path, h.rule) for h in hits] == [("app.exe!PYZ!shotrans.app", "lan_ip")]
    assert n == 2      # 外层文件本身也扫一遍 + 1 个条目


def test_missing_config_gives_fixed_rules_only(tmp_path):
    names = {n for n, _ in rc.default_rules(tmp_path / "nope.toml")}
    assert names == {"lan_ip", "api_key", "author_path"}


def test_size_check_and_largest(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    for i, size in enumerate([10, 3000, 200]):
        (root / f"f{i}.bin").write_bytes(b"x" * size)
    assert rc.largest_files(root, 2) == [("f1.bin", 3000), ("f2.bin", 200)]
    z = tmp_path / "a.zip"
    z.write_bytes(b"y" * 100)
    assert rc.check_size(z, 100, root) is None
    msg = rc.check_size(z, 99, root)
    assert msg is not None and "f1.bin" in msg and "100" in msg


def test_make_zip_has_top_folder_and_sha(tmp_path):
    src = tmp_path / "dist"
    (src / "_internal").mkdir(parents=True)
    (src / "截图翻译.exe").write_bytes(b"MZ")
    (src / "_internal" / "a.dll").write_bytes(b"d")
    z = tmp_path / "截图翻译-1.0.0-win64.zip"
    rc.make_zip(src, z, "截图翻译")
    with zipfile.ZipFile(z) as f:
        assert sorted(f.namelist()) == ["截图翻译/_internal/a.dll", "截图翻译/截图翻译.exe"]
    assert rc.sha256(z) == hashlib.sha256(z.read_bytes()).hexdigest()


# ---- 以下为补强用例：每个守卫各有一条只有它能触发的夹具 ----
def _hits(tmp_path, files, cfg_text=None, **kw):
    root = tmp_path / "pkg"
    root.mkdir()
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return rc.scan_tree(root, _rules(tmp_path, cfg_text), **kw)


def test_utf16_lan_ip_alone(tmp_path):
    hits, _ = _hits(tmp_path, {"a.dat": "x172.25.1.1".encode("utf-16-le")})
    assert [(h.path, h.rule) for h in hits] == [("a.dat", "lan_ip")]


def test_utf16_api_key_alone(tmp_path):
    hits, _ = _hits(tmp_path, {"a.dat": SECRET.encode("utf-16-le")})
    assert [(h.path, h.rule) for h in hits] == [("a.dat", "api_key")]


def test_utf16_author_path_is_case_insensitive_and_slash_agnostic(tmp_path):
    hits, _ = _hits(tmp_path, {"a.dat": "c:/USERS//GreyPapl".encode("utf-16-le")})
    assert [(h.path, h.rule) for h in hits] == [("a.dat", "author_path")]


def test_api_key_length_boundary(tmp_path):
    body = "A1b2C3d4E5f6G7h8I9j0"
    hits, _ = _hits(tmp_path, {"19.txt": ("sk-" + body[:19]).encode(), "20.txt": ("sk-" + body).encode(),
                               "19w.dat": ("sk-" + body[:19]).encode("utf-16-le"),
                               "20w.dat": ("sk-" + body).encode("utf-16-le")})
    assert sorted(h.path for h in hits) == ["20.txt", "20w.dat"]


def test_config_value_utf16_and_length_boundary(tmp_path):
    cfg = '[a]\nseven = "1234567"\neight = "abcdefgh"\n[b]\nk = "ZZZZZZZZ"\n'
    hits, _ = _hits(tmp_path, {"u8.txt": b"..1234567..", "w.dat": "..abcdefgh..".encode("utf-16-le"),
                               "ok.txt": b"abcdefg"}, cfg)
    assert [(h.path, h.rule) for h in hits] == [("w.dat", "config_value_a.eight")]


def test_config_top_level_and_non_string_values_ignored(tmp_path):
    cfg = '[a]\nn = 123456789\nflag = true\nshort = ["abc"]\n[[b]]\nn = 1.5\n'
    names = {n for n, _ in _rules(tmp_path, cfg)}
    assert names == {"lan_ip", "api_key", "author_path"}


def test_config_value_with_regex_metachars_is_literal(tmp_path):
    hits, _ = _hits(tmp_path, {"a.txt": b"a.b(c)+d*", "b.txt": b"aXb(c)+d*"}, '[s]\nk = "a.b(c)+d*"\n')
    assert [(h.path, h.rule) for h in hits] == [("a.txt", "config_value_s.k")]


def test_nested_path_zip_dir_entries_and_uppercase_suffix(tmp_path):
    root = tmp_path / "pkg"
    (root / "sub" / "deep").mkdir(parents=True)
    with zipfile.ZipFile(root / "sub" / "deep" / "L.ZIP", "w") as z:
        z.mkdir("d")
        z.writestr("d/e.txt", b"172.25.9.9")
    hits, n = rc.scan_tree(root, _rules(tmp_path))
    assert [(h.path, h.rule) for h in hits] == [("sub/deep/L.ZIP!d/e.txt", "lan_ip")] and n == 1


def test_same_rule_reported_once_and_hits_sorted(tmp_path):
    hits, n = _hits(tmp_path, {"z.txt": b"172.25.1.1 172.25.2.2 " + "172.25.3.3".encode("utf-16-le"),
                               "a.txt": b"172.25.1.1 " + SECRET.encode()})
    assert [(h.path, h.rule) for h in hits] == [("a.txt", "api_key"), ("a.txt", "lan_ip"), ("z.txt", "lan_ip")]


def test_extractor_not_matching_leaves_file_scanned_normally(tmp_path):
    ext = [(lambda p: p.suffix == ".exe", lambda p: iter([("E", b"172.25.0.1")]))]
    hits, n = _hits(tmp_path, {"a.dll": b"172.25.0.1"}, extractors=ext)
    assert [(h.path, h.rule) for h in hits] == [("a.dll", "lan_ip")] and n == 1


def test_extractor_outer_file_itself_is_scanned(tmp_path):
    ext = [(lambda p: p.suffix == ".exe", lambda p: iter([]))]
    hits, n = _hits(tmp_path, {"a.exe": b"172.25.0.1"}, extractors=ext)
    assert [(h.path, h.rule) for h in hits] == [("a.exe", "lan_ip")] and n == 1


def test_extractor_takes_precedence_over_zip_suffix(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    with zipfile.ZipFile(root / "x.zip", "w") as z:
        z.writestr("in.txt", b"172.25.0.1")
    ext = [(lambda p: p.suffix == ".zip", lambda p: iter([("only", b"clean")]))]
    hits, n = rc.scan_tree(root, _rules(tmp_path), extractors=ext)
    assert [(h.path, h.rule) for h in hits] == [("x.zip", "lan_ip")] and n == 2


def test_scan_read_failure_is_raised(tmp_path):
    import pytest
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "bad.zip").write_bytes(b"not a zip")
    with pytest.raises(zipfile.BadZipFile):
        rc.scan_tree(root, _rules(tmp_path))


def test_size_message_lists_only_top_20_sorted(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    for i in range(25):
        (root / f"f{i:02d}.bin").write_bytes(b"x" * (i + 1))
    z = tmp_path / "a.zip"
    z.write_bytes(b"y" * 10)
    msg = rc.check_size(z, 5, root)
    assert "f24.bin" in msg and "f05.bin" in msg and "f04.bin" not in msg
    assert msg.index("f24.bin") < msg.index("f23.bin")
    assert rc.largest_files(root)[0] == ("f24.bin", 25) and len(rc.largest_files(root)) == 20


def test_largest_files_ties_sorted_by_name_and_nested(tmp_path):
    root = tmp_path / "pkg"
    (root / "d").mkdir(parents=True)
    (root / "b.bin").write_bytes(b"1")
    (root / "d" / "a.bin").write_bytes(b"1")
    (root / "a.bin").write_bytes(b"1")
    assert rc.largest_files(root) == [("a.bin", 1), ("b.bin", 1), ("d/a.bin", 1)]


def test_make_zip_is_deflated_and_creates_parent(tmp_path):
    src = tmp_path / "dist"
    src.mkdir()
    (src / "a.txt").write_bytes(b"a" * 5000)
    z = tmp_path / "out" / "sub" / "r.zip"
    rc.make_zip(src, z, "T")
    with zipfile.ZipFile(z) as f:
        assert f.infolist()[0].compress_type == zipfile.ZIP_DEFLATED
        assert f.infolist()[0].compress_size < 100
        assert f.infolist()[0].flag_bits & 0x800 == 0
    z2 = tmp_path / "r2.zip"
    rc.make_zip(tmp_path / "dist", z2, "名")
    with zipfile.ZipFile(z2) as f:
        assert f.namelist() == ["名/a.txt"] and f.infolist()[0].flag_bits & 0x800


def test_sha256_multichunk(tmp_path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"ab" * (1 << 20) + b"c")
    assert rc.sha256(p) == hashlib.sha256(p.read_bytes()).hexdigest()


def test_format_hits_exact_layout():
    assert rc.format_hits([rc.Hit("a/b.txt", "lan_ip"), rc.Hit("c.zip!d", "api_key")]) == \
        "lan_ip  a/b.txt\napi_key  c.zip!d"
    assert rc.format_hits([]) == ""


def test_limit_is_50_mib():
    assert rc.LIMIT_BYTES == 52428800


def test_many_hits_come_out_sorted_by_path_then_rule(tmp_path):
    files = {f"f{i:02d}.txt": b"172.25.1.1 " + SECRET.encode() for i in range(10)}
    hits, _ = _hits(tmp_path, files)
    expected = [(f"f{i:02d}.txt", r) for i in range(10) for r in ("api_key", "lan_ip")]
    assert [(h.path, h.rule) for h in hits] == expected


def _zip_bytes(entries):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_with_other_extension_is_a_container(tmp_path):
    hits, n = _hits(tmp_path, {"pkg.whl": _zip_bytes({"a/m.py": b"x = '172.25.1.1'", "b.txt": b"ok"})})
    assert [(h.path, h.rule) for h in hits] == [("pkg.whl!a/m.py", "lan_ip")] and n == 2


def test_nested_zip_is_recursed_even_when_deflated(tmp_path):
    inner = _zip_bytes({"deep.txt": b"172.25.1.1" + b" " * 200})
    hits, n = _hits(tmp_path, {"o.zip": _zip_bytes({"inner.zip": inner, "other.txt": b"ok"})})
    assert [(h.path, h.rule) for h in hits] == [("o.zip!inner.zip!deep.txt", "lan_ip")] and n == 2


def test_nested_zip_with_non_zip_extension_inside_zip(tmp_path):
    inner = _zip_bytes({"d.txt": b"172.25.1.1"})
    hits, _ = _hits(tmp_path, {"o.zip": _zip_bytes({"inner.bin": inner})})
    assert [h.path for h in hits] == ["o.zip!inner.bin!d.txt"]


def _nest(levels):
    data = _zip_bytes({"leaf.txt": b"172.25.1.1"})
    for _ in range(levels - 1):
        data = _zip_bytes({"n.zip": data})
    return data


def test_zip_depth_cap_allows_5_and_raises_beyond(tmp_path):
    import pytest
    hits, _ = _hits(tmp_path, {"a.zip": _nest(5)})
    assert [h.rule for h in hits] == ["lan_ip"]
    root = tmp_path / "pkg"
    (root / "a.zip").write_bytes(_nest(6))
    with pytest.raises(RuntimeError):
        rc.scan_tree(root, _rules(tmp_path))


def test_depth_error_message_has_no_content(tmp_path):
    import pytest
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.zip").write_bytes(_nest(6))
    with pytest.raises(RuntimeError) as e:
        rc.scan_tree(root, _rules(tmp_path))
    assert "172.25" not in str(e.value)


def test_non_zip_with_pk_prefix_is_scanned_raw(tmp_path):
    hits, n = _hits(tmp_path, {"f.dat": b"PK\x03\x04 garbage 172.25.1.1"})
    assert [(h.path, h.rule) for h in hits] == [("f.dat", "lan_ip")] and n == 1


def test_zip_appended_after_other_bytes_is_not_a_container(tmp_path):
    hits, n = _hits(tmp_path, {"s.exe": b"MZ" + _zip_bytes({"a.txt": b"172.25.1.1" + b" " * 200})})
    assert hits == [] and n == 1


def test_config_nested_table_list_and_toplevel_values(tmp_path):
    cfg = ('top = "toplevelvalue"\n[a.b]\nk = "nestedvalue1"\n[c]\nlst = ["listvalue1", "x"]\n'
           '[[d]]\nk = "arrayoftables"\n')
    names = {n for n, _ in _rules(tmp_path, cfg)} - {"lan_ip", "api_key", "author_path"}
    assert names == {"config_value_top", "config_value_a.b.k", "config_value_c.lst[0]", "config_value_d[0].k"}


def test_config_nested_value_is_found_in_file(tmp_path):
    hits, _ = _hits(tmp_path, {"x.txt": b"..nestedvalue1.."}, '[a.b]\nk = "nestedvalue1"\n')
    assert [(h.path, h.rule) for h in hits] == [("x.txt", "config_value_a.b.k")]


# ---- 裁决 A：sk- 紧跟在 ASCII 字母/数字后面不算 key（Qt6Core.dll 里连写的 CLDR 时区名 "Irkoetsk-tyd…" 误报）----
_TAIL = "A1b2C3d4E5f6G7h8I9j0K1"


def test_api_key_after_ascii_letter_or_digit_is_still_hit_utf8(tmp_path):
    # UTF-8 形式不加前界：marshal 里短字符串前一个字节是长度，可能正好是字母/数字
    hits, _ = _hits(tmp_path, {"letter.txt": ("Irkoetsk-" + _TAIL).encode(),
                               "digit.txt": ("9sk-" + _TAIL).encode()})
    assert sorted(h.path for h in hits if h.rule == "api_key") == ["digit.txt", "letter.txt"]


@pytest.mark.parametrize("length", [27, 35, 48, 51, 57, 65, 90, 97, 122, 164])
def test_api_key_constant_in_marshalled_code_is_hit(tmp_path, length):
    # 与 PYZ 里的内容同形：编译一段代码再 marshal；长度 48–57/65–90/97–122 时长度字节是 ASCII 数字/字母
    key = "sk-" + ("Ab1" * 60)[:length - 3]
    data = marshal.dumps(compile(f"KEY = {key!r}\n", "m.py", "exec"))
    hits, _ = _hits(tmp_path, {"m.pyc": data})
    assert [(h.path, h.rule) for h in hits] == [("m.pyc", "api_key")]


@pytest.mark.parametrize("length", [48, 51, 65, 97])
def test_api_key_marshalled_str_constant_is_hit(tmp_path, length):
    key = "sk-" + ("Z9y" * 60)[:length - 3]
    data = marshal.dumps(key)
    assert data[1:2] == bytes([length])   # 确认夹具确实是「长度字节紧挨内容」的形状
    hits, _ = _hits(tmp_path, {"s.bin": data})
    assert [(h.path, h.rule) for h in hits] == [("s.bin", "api_key")]


def test_api_key_after_ascii_letter_is_not_hit_utf16(tmp_path):
    hits, _ = _hits(tmp_path, {"w.dat": ("Omsk-" + _TAIL).encode("utf-16-le"),
                               "d.dat": ("7sk-" + _TAIL).encode("utf-16-le")})
    assert hits == []


def test_api_key_at_boundary_still_hit_both_forms(tmp_path):
    files = {"start.txt": ("sk-" + _TAIL).encode(), "space.txt": (" sk-" + _TAIL).encode(),
             "quote.txt": ('key = "sk-' + _TAIL + '"').encode(), "under.txt": ("_sk-" + _TAIL).encode(),
             "nonascii.txt": ("ésk-" + _TAIL).encode(),
             "start.dat": ("sk-" + _TAIL).encode("utf-16-le"), "space.dat": (" sk-" + _TAIL).encode("utf-16-le"),
             "quote.dat": ('"sk-' + _TAIL).encode("utf-16-le"), "nonascii.dat": ("ésk-" + _TAIL).encode("utf-16-le")}
    hits, _ = _hits(tmp_path, files)
    assert sorted(h.path for h in hits if h.rule == "api_key") == sorted(files)


def test_api_key_tail_length_rule_kept_after_boundary(tmp_path):
    hits, _ = _hits(tmp_path, {"19.txt": (" sk-" + _TAIL[:19]).encode(), "20.txt": (" sk-" + _TAIL[:20]).encode()})
    assert [h.path for h in hits] == ["20.txt"]


# ---- 裁决 B：旧配置里等于对应默认值的值不成规则；对不上的路径一律照扫 ----
_DEFAULTS = {"hotkey": {"capture": "Ctrl+Alt+Q"}, "translate": {"domain_hint": "", "chunk_chars": 900},
             "primary": {"base_url": "", "model": "default-model-x"}}


def _cfg_names(tmp_path, text, defaults=_DEFAULTS):
    cfg = tmp_path / "c.toml"
    cfg.write_text(text, encoding="utf-8")
    return {n for n, _ in rc.default_rules(cfg, defaults)} - {"lan_ip", "api_key", "author_path"}


def test_default_valued_config_key_is_skipped(tmp_path):
    assert _cfg_names(tmp_path, '[hotkey]\ncapture = "Ctrl+Alt+Q"\n') == set()


def test_nondefault_value_under_same_key_is_still_a_rule(tmp_path):
    assert _cfg_names(tmp_path, '[hotkey]\ncapture = "Ctrl+Alt+W"\n') == {"config_value_hotkey.capture"}


def test_default_comparison_is_exact_case_sensitive(tmp_path):
    assert _cfg_names(tmp_path, '[hotkey]\ncapture = "ctrl+alt+q"\n') == {"config_value_hotkey.capture"}


def test_value_equal_to_a_default_elsewhere_is_still_a_rule(tmp_path):
    # 值等于别处的默认值，但这个路径的默认值不是它：照扫（只按路径比，不按值比）
    assert _cfg_names(tmp_path, '[translate]\ndomain_hint = "Ctrl+Alt+Q"\n') == {"config_value_translate.domain_hint"}


def test_unmappable_paths_are_still_rules(tmp_path):
    text = ('[vllm]\napi_key = "sk-' + _TAIL + '"\nbase_url = "Ctrl+Alt+Q"\n'   # 默认值树里没有 [vllm]
            '[hotkey]\nother = "Ctrl+Alt+Q"\n'                                    # 节在、键不在
            '[translate.chunk_chars]\nx = "Ctrl+Alt+Q"\n'                         # 默认值那里是数字不是表
            '[[hotkey2]]\ncapture = "Ctrl+Alt+Q"\n')                              # 表数组，带下标
    names = _cfg_names(tmp_path, 'top = "Ctrl+Alt+Q"\n' + text)
    assert names == {"config_value_top", "config_value_vllm.api_key", "config_value_vllm.base_url",
                     "config_value_hotkey.other", "config_value_translate.chunk_chars.x",
                     "config_value_hotkey2[0].capture"}


def test_list_item_equal_to_default_is_still_a_rule(tmp_path):
    assert _cfg_names(tmp_path, '[hotkey]\ncapture = ["Ctrl+Alt+Q"]\n') == {"config_value_hotkey.capture[0]"}


def test_no_defaults_keeps_every_value(tmp_path):
    assert _cfg_names(tmp_path, '[hotkey]\ncapture = "Ctrl+Alt+Q"\n', None) == {"config_value_hotkey.capture"}


@pytest.mark.parametrize("value", ["sk-" + _TAIL, "http://10.1.2.3:8000/v1", "Ctrl+Alt+W", "my long hint"])
def test_nondefault_value_is_always_a_rule(tmp_path, value):
    # 作者的真 key 不是默认值，所以裁决 B 永远丢不掉它
    for section, key in (("hotkey", "capture"), ("primary", "model"), ("primary", "base_url"),
                         ("translate", "domain_hint"), ("vllm", "api_key")):
        names = _cfg_names(tmp_path, f'[{section}]\n{key} = "{value}"\n')
        assert names == {f"config_value_{section}.{key}"}, (section, key, value)


def test_default_skip_does_not_affect_scanning_of_real_secret(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[hotkey]\ncapture = "Ctrl+Alt+Q"\n[vllm]\napi_key = "zz-private-value-123"\n', encoding="utf-8")
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.txt").write_bytes(b"Ctrl+Alt+Q ... zz-private-value-123")
    hits, _ = rc.scan_tree(root, rc.default_rules(cfg, _DEFAULTS))
    assert [(h.path, h.rule) for h in hits] == [("a.txt", "config_value_vllm.api_key")]

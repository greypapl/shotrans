"""构建脚本里可单测的部分：干净环境变量、自测结果判定、旧配置缺失时的退路、exe 内 PKG/PYZ 解包与自证。
真构建由 python -m tools.build_release 本身验证。"""
from pathlib import Path

import pytest

from tools import build_release as br
from tools import release_checks as rc

SECRET = "sk-" + "Z9y8X7w6V5u4T3s2R1q0P9o8"


def test_clean_env_drops_conda_and_python_vars(tmp_path):
    base = {"PATH": r"C:\Windows\system32;C:\Users\x\anaconda3\envs\shotrans;C:\Users\x\anaconda3\Library\bin;C:\Windows",
            "PYTHONPATH": ".", "PYTHONHOME": "x", "CONDA_PREFIX": "y", "QT_PLUGIN_PATH": "z",
            "QT_QPA_PLATFORM": "offscreen", "SystemRoot": r"C:\Windows", "APPDATA": r"C:\Users\x\AppData\Roaming",
            "SHOTRANS_LEGACY_DIR": "q"}
    env = br.clean_env(base, tmp_path / "home")
    assert env["PATH"] == r"C:\Windows\system32;C:\Windows"
    for k in ("PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM", "SHOTRANS_LEGACY_DIR"):
        assert k not in env
    assert env["SHOTRANS_HOME"] == str(tmp_path / "home") and env["SystemRoot"] == r"C:\Windows"


def test_clean_env_drops_each_var_alone(tmp_path):
    # 每个变量单独一份夹具，免得一条规则替另一条顶着
    for k in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM", "QT_QPA_PLATFORM_PLUGIN_PATH",
              "SHOTRANS_LEGACY_DIR", "CONDA_DEFAULT_ENV", "conda_prefix_1"):
        env = br.clean_env({k: "v", "PATH": ""}, tmp_path)
        assert k not in env, k


def test_clean_env_overrides_existing_home(tmp_path):
    env = br.clean_env({"SHOTRANS_HOME": r"C:\real", "PATH": ""}, tmp_path / "h")
    assert env["SHOTRANS_HOME"] == str(tmp_path / "h")


# ---- 自测结果判定 ----

def _report(**kw):
    rep = {"ok": True, "frozen": True, "msvcp140": "x", "checks": [{"name": "imports", "ok": True, "detail": ""}]}
    rep.update(kw)
    return rep


def test_judge_accepts_clean_report():
    assert br.judge_selftest(_report(), 0) == []


def test_judge_rejects_nonzero_exit():
    assert br.judge_selftest(_report(), 1)


def test_judge_rejects_report_not_ok():
    assert br.judge_selftest(_report(ok=False), 0)


def test_judge_rejects_not_frozen():
    assert br.judge_selftest(_report(frozen=False), 0)


def test_judge_rejects_missing_frozen():
    rep = _report()
    del rep["frozen"]
    assert br.judge_selftest(rep, 0)


def test_judge_rejects_any_failed_row_even_if_top_ok():
    # 顶层 ok 说 true、退出码 0，但有一行 setup:qapp 失败 —— 仍要判失败，并列出这一行
    rep = _report(checks=[{"name": "imports", "ok": True, "detail": ""},
                          {"name": "setup:qapp", "ok": False, "detail": "boom"}])
    problems = br.judge_selftest(rep, 0)
    assert any("setup:qapp" in p and "boom" in p for p in problems)


def test_judge_rejects_empty_checks():
    assert br.judge_selftest(_report(checks=[]), 0)


# ---- 参数与旧配置退路 ----

def test_parse_args_defaults():
    a = br.parse_args([])
    assert a.skip_tests is False and a.legacy_config == br.ROOT / "config.local.toml"


def test_parse_args_values(tmp_path):
    a = br.parse_args(["--skip-tests", "--legacy-config", str(tmp_path / "c.toml")])
    assert a.skip_tests is True and a.legacy_config == tmp_path / "c.toml"


def test_scan_rules_missing_legacy_config_warns_and_uses_fixed_rules(tmp_path, capsys):
    rules = br.scan_rules(tmp_path / "nope.toml")
    out = capsys.readouterr().out
    assert len([ln for ln in out.splitlines() if ln.strip()]) == 1 and "nope.toml" in out
    assert [n for n, _ in rules] == [n for n, _ in rc.default_rules(None)]


def test_scan_rules_existing_legacy_config_adds_values_silently(tmp_path, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[vllm]\nbase_url = "http://example.internal:1234"\n', encoding="utf-8")
    rules = br.scan_rules(cfg)
    assert capsys.readouterr().out == ""
    assert "config_value_vllm.base_url" in {n for n, _ in rules}


def test_scan_rules_skips_values_equal_to_program_defaults(tmp_path):
    # 程序默认快捷键是 Ctrl+Alt+Q（写死在这里，不从被测代码读）；旧格式的 [vllm] 对不上默认值树，照扫
    cfg = tmp_path / "c.toml"
    cfg.write_text('[hotkey]\ncapture = "Ctrl+Alt+Q"\n[vllm]\napi_key = "' + SECRET + '"\n'
                   'base_url = "http://10.9.8.7:8000/v1"\n[translate]\ndomain_hint = "a long domain hint"\n',
                   encoding="utf-8")
    names = {n for n, _ in br.scan_rules(cfg)}
    assert "config_value_hotkey.capture" not in names
    assert {"config_value_vllm.api_key", "config_value_vllm.base_url", "config_value_translate.domain_hint"} <= names


def test_scan_rules_nondefault_hotkey_is_a_rule(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[hotkey]\ncapture = "Ctrl+Shift+F8"\n', encoding="utf-8")
    assert "config_value_hotkey.capture" in {n for n, _ in br.scan_rules(cfg)}

# ---- exe 内 PKG / PYZ 解包 ----

def _fake_exe(path: Path, pyz_modules: dict[str, str], data_entries: dict[str, bytes], work: Path) -> Path:
    """用 PyInstaller 自己的写入器造一个「PE 头 + CArchive（含 PYZ）」的假 exe。中间文件放 work（要在被扫目录外）；
    返回中间的 PYZ 文件路径。"""
    from PyInstaller.archive.writers import CArchiveWriter, ZlibArchiveWriter
    work.mkdir()
    entries, code = [], {}
    for name, src in pyz_modules.items():
        f = work / (name.split(".")[-1] + ".py")
        f.write_text(src, encoding="utf-8")
        code[name] = compile(src, str(f), "exec")
        entries.append((name, str(f), "PYMODULE"))
    pyz = work / "PYZ.pyz"
    ZlibArchiveWriter(str(pyz), entries, code)
    pkg_entries = [("PYZ.pyz", str(pyz), False, "z")]
    for name, data in data_entries.items():
        f = work / name
        f.write_bytes(data)
        pkg_entries.append((name, str(f), True, "x"))
    pkg = work / "pkg"
    CArchiveWriter(str(pkg), pkg_entries, "python312.dll")
    path.write_bytes(b"MZ" + b"\0" * 200 + pkg.read_bytes())
    return pyz


def test_extractor_matches_exe_and_pyz_only(tmp_path):
    match, _ = br.pyinstaller_extractor()
    assert match(Path("a.EXE")) and match(Path("x/b.pyz"))
    assert not match(Path("a.dll")) and not match(Path("a.zip"))


def test_extractor_yields_decompressed_pkg_and_pyz_entries(tmp_path):
    exe = tmp_path / "app.exe"
    _fake_exe(exe, {"shotrans.app": f"KEY = {SECRET!r}\n"}, {"notes.txt": b"hello"}, tmp_path / "w")
    _, it = br.pyinstaller_extractor()
    got = dict(it(exe))
    assert got["PKG!notes.txt"] == b"hello"
    assert SECRET.encode() in got["PYZ!shotrans.app"]   # 解压后的 marshal 字节里有原文
    assert SECRET.encode() not in exe.read_bytes()       # 原始 exe 字节里是压缩的，直接扫不到


def test_extractor_reads_standalone_pyz(tmp_path):
    exe = tmp_path / "app.exe"
    pyz = _fake_exe(exe, {"m": "X = 'standalone-marker'\n"}, {}, tmp_path / "w")
    _, it = br.pyinstaller_extractor()
    got = dict(it(pyz))
    assert b"standalone-marker" in got["PYZ!m"]


def test_scan_package_finds_secret_inside_pyz_and_proves_pyz(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    _fake_exe(app / "截图翻译.exe", {"shotrans.app": f"KEY = {SECRET!r}\n"}, {}, tmp_path / "w")
    hits, n, names = br.scan_package(app, rc.default_rules(None))
    assert rc.Hit("截图翻译.exe!PYZ!shotrans.app", "api_key") in hits
    assert "PYZ!shotrans.app" in names
    br.require_pyz(names)   # 不抛


def test_scan_package_clean(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    _fake_exe(app / "截图翻译.exe", {"shotrans.app": "X = 1\n"}, {"a.txt": b"fine"}, tmp_path / "w")
    hits, n, names = br.scan_package(app, rc.default_rules(None))
    assert hits == [] and n == 1 + 2 + 1   # 外层 exe + PKG 两条 + PYZ 一条


def test_require_pyz_fails_without_app_module(tmp_path):
    with pytest.raises(br.BuildError, match="PYZ"):
        br.require_pyz(["PKG!PYZ.pyz", "PYZ!shotrans.other"])


def test_scan_package_without_pyz_fails_self_check(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    _fake_exe(app / "截图翻译.exe", {"other": "X = 1\n"}, {}, tmp_path / "w")
    _, _, names = br.scan_package(app, rc.default_rules(None))
    with pytest.raises(br.BuildError):
        br.require_pyz(names)


# ---- 构建用的 PATH：只认构建解释器自己的 conda 环境 ----

PREFIX = Path(r"C:\Users\x\anaconda3\envs\shotrans")


def test_build_env_puts_prefix_dirs_first():
    env = br.build_env({"PATH": r"C:\Windows\system32;C:\Windows"}, PREFIX)
    assert env["PATH"].split(";") == [str(PREFIX / "Library" / "bin"), str(PREFIX), str(PREFIX / "DLLs"),
                                      r"C:\Windows\system32", r"C:\Windows"]


def test_build_env_drops_base_conda_library_bin():
    # 就是这一条让 PyInstaller 拿了 base 的 OpenSSL 3.0：单独一份夹具
    env = br.build_env({"PATH": r"C:\Users\x\anaconda3\Library\bin;C:\Windows"}, PREFIX)
    assert r"C:\Users\x\anaconda3\Library\bin" not in env["PATH"].split(";")
    assert env["PATH"].split(";")[-1] == r"C:\Windows"


def test_build_env_drops_other_conda_env_and_miniconda():
    env = br.build_env({"PATH": r"C:\Users\x\anaconda3\envs\other\Library\bin;D:\Miniconda3\Scripts;C:\Windows"},
                       PREFIX)
    assert env["PATH"].split(";")[3:] == [r"C:\Windows"]


def test_build_env_keeps_entries_under_prefix():
    # 构建环境自己的目录（如 Scripts）不算「别的 conda」；大小写不同也认得
    env = br.build_env({"PATH": r"c:\users\x\ANACONDA3\envs\shotrans\Scripts;C:\Windows"}, PREFIX)
    assert env["PATH"].split(";")[3:] == [r"c:\users\x\ANACONDA3\envs\shotrans\Scripts", r"C:\Windows"]


def test_build_env_sibling_prefix_is_not_under_prefix():
    # envs\shotrans2 只是名字前缀相同，不在 envs\shotrans 下面
    env = br.build_env({"PATH": r"C:\Users\x\anaconda3\envs\shotrans2\Library\bin;C:\Windows"}, PREFIX)
    assert env["PATH"].split(";")[3:] == [r"C:\Windows"]


def test_build_env_keeps_other_vars_and_does_not_mutate_base():
    base = {"PATH": r"C:\Windows", "SystemRoot": r"C:\Windows"}
    env = br.build_env(base, PREFIX)
    assert env["SystemRoot"] == r"C:\Windows" and base["PATH"] == r"C:\Windows"


# ---- 包里 DLL 的来源：与构建环境里同名的那份必须逐字节相同 ----

def _dirs(tmp_path):
    prefix = tmp_path / "env"
    for d in (prefix / "Library" / "bin", prefix / "DLLs"):
        d.mkdir(parents=True)
    internal = tmp_path / "app" / "_internal"
    internal.mkdir(parents=True)
    return prefix, internal


def test_env_dll_dirs():
    assert br.env_dll_dirs(PREFIX) == [PREFIX / "Library" / "bin", PREFIX, PREFIX / "DLLs"]


def test_provenance_all_match(tmp_path):
    prefix, internal = _dirs(tmp_path)
    (prefix / "Library" / "bin" / "libssl-3-x64.dll").write_bytes(b"ssl35")
    (internal / "libssl-3-x64.dll").write_bytes(b"ssl35")
    assert br.dll_provenance(internal, br.env_dll_dirs(prefix)) == ([], 1)


def test_provenance_flags_library_bin_mismatch(tmp_path):
    prefix, internal = _dirs(tmp_path)
    (prefix / "Library" / "bin" / "libssl-3-x64.dll").write_bytes(b"ssl35")
    (internal / "libssl-3-x64.dll").write_bytes(b"ssl30")
    assert br.dll_provenance(internal, br.env_dll_dirs(prefix)) == (["libssl-3-x64.dll"], 1)


def test_provenance_flags_prefix_root_mismatch(tmp_path):
    prefix, internal = _dirs(tmp_path)
    (prefix / "python312.dll").write_bytes(b"a")
    (internal / "python312.dll").write_bytes(b"b")
    assert br.dll_provenance(internal, br.env_dll_dirs(prefix))[0] == ["python312.dll"]


def test_provenance_flags_dlls_dir_mismatch(tmp_path):
    prefix, internal = _dirs(tmp_path)
    (prefix / "DLLs" / "libffi-8.dll").write_bytes(b"a")
    (internal / "libffi-8.dll").write_bytes(b"b")
    assert br.dll_provenance(internal, br.env_dll_dirs(prefix))[0] == ["libffi-8.dll"]


def test_provenance_any_env_copy_is_enough(tmp_path):
    # 实际情况：zlib.dll 在环境根和 Library\bin 各有一份、内容不同；包里是其中一份就算对
    prefix, internal = _dirs(tmp_path)
    (prefix / "zlib.dll").write_bytes(b"root")
    (prefix / "Library" / "bin" / "zlib.dll").write_bytes(b"lib")
    (internal / "zlib.dll").write_bytes(b"root")
    assert br.dll_provenance(internal, br.env_dll_dirs(prefix)) == ([], 1)


def test_provenance_ignores_dll_not_in_env_and_non_dll(tmp_path):
    prefix, internal = _dirs(tmp_path)
    (internal / "qt6core.dll").write_bytes(b"x")
    (prefix / "Library" / "bin" / "notes.txt").write_bytes(b"a")
    (internal / "notes.txt").write_bytes(b"b")
    assert br.dll_provenance(internal, br.env_dll_dirs(prefix)) == ([], 0)


def test_provenance_matches_name_case_insensitively(tmp_path):
    prefix, internal = _dirs(tmp_path)
    (prefix / "Library" / "bin" / "libbz2.dll").write_bytes(b"a")
    (internal / "LIBBZ2.DLL").write_bytes(b"b")
    assert br.dll_provenance(internal, br.env_dll_dirs(prefix))[0] == ["LIBBZ2.DLL"]


def test_provenance_only_top_level(tmp_path):
    prefix, internal = _dirs(tmp_path)
    (prefix / "Library" / "bin" / "a.dll").write_bytes(b"a")
    (internal / "sub").mkdir()
    (internal / "sub" / "a.dll").write_bytes(b"b")
    assert br.dll_provenance(internal, br.env_dll_dirs(prefix)) == ([], 0)


def test_require_provenance_raises_with_names(tmp_path):
    prefix, internal = _dirs(tmp_path)
    for n in ("libssl-3-x64.dll", "liblzma.dll"):
        (prefix / "Library" / "bin" / n).write_bytes(b"env")
        (internal / n).write_bytes(b"base")
    with pytest.raises(br.BuildError) as e:
        br.require_provenance(internal, prefix)
    assert "libssl-3-x64.dll" in str(e.value) and "liblzma.dll" in str(e.value)


def test_require_provenance_raises_when_nothing_compared(tmp_path):
    # 一个都没比上（比如 _internal 路径错了）不能算通过
    prefix, internal = _dirs(tmp_path)
    with pytest.raises(br.BuildError):
        br.require_provenance(internal, prefix)


def test_require_provenance_passes_and_returns_count(tmp_path):
    prefix, internal = _dirs(tmp_path)
    (prefix / "python312.dll").write_bytes(b"p")
    (internal / "python312.dll").write_bytes(b"p")
    assert br.require_provenance(internal, prefix) == 1


def test_build_env_keeps_prefix_itself():
    env = br.build_env({"PATH": str(PREFIX) + "\\;C:\\Windows"}, PREFIX)
    assert env["PATH"].split(";")[3:] == [str(PREFIX) + "\\", r"C:\Windows"]


def test_provenance_skips_directory_named_like_dll(tmp_path):
    prefix, internal = _dirs(tmp_path)
    (prefix / "Library" / "bin" / "odd.dll").write_bytes(b"a")
    (internal / "odd.dll").mkdir()
    assert br.dll_provenance(internal, br.env_dll_dirs(prefix)) == ([], 0)


# ---- main 的接线：PyInstaller 用 build_env，紧接着做来源检查 ----

def _stub_main(monkeypatch, tmp_path, calls):
    app = tmp_path / "app"
    app.mkdir()
    (app / "截图翻译.exe").write_bytes(b"MZ")
    monkeypatch.setattr(br, "APP", app)
    monkeypatch.setattr(br, "EXE", app / "截图翻译.exe")
    monkeypatch.setattr(br.shutil, "rmtree", lambda *a, **k: None)
    monkeypatch.setattr(br, "_run", lambda cmd, **kw: calls.append((cmd, kw)))
    return app


def test_main_runs_pyinstaller_with_build_env(monkeypatch, tmp_path):
    import sys
    calls = []
    _stub_main(monkeypatch, tmp_path, calls)

    def stop(*a):
        raise br.BuildError("停")
    monkeypatch.setattr(br, "require_provenance", stop)
    assert br.main(["--skip-tests"]) == 1
    pyi = [kw for cmd, kw in calls if "PyInstaller" in cmd]
    assert len(pyi) == 1
    first = pyi[0]["env"]["PATH"].split(";")[0]
    assert first == str(Path(sys.prefix) / "Library" / "bin")


def test_main_checks_provenance_after_pyinstaller(monkeypatch, tmp_path):
    import sys
    calls, seen = [], []
    app = _stub_main(monkeypatch, tmp_path, calls)

    def record(internal, prefix):
        seen.append((internal, prefix, len(calls)))
        raise br.BuildError("停")
    monkeypatch.setattr(br, "require_provenance", record)
    assert br.main(["--skip-tests"]) == 1
    assert seen == [(app / "_internal", Path(sys.prefix), len(calls))]
    assert "PyInstaller" in calls[-1][0]


def test_zip_name_is_ascii_for_github_releases():
    """GitHub Releases 会删掉附件名里的非 ASCII 字符（「截图翻译-…zip」上传后变成「-…zip」）。"""
    import shotrans
    assert br.ZIP.name == f"ShotTrans-{shotrans.__version__}-win64.zip"
    assert br.ZIP.name.isascii()

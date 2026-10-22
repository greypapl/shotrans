"""--selftest：不建托盘、不注册快捷键、不受单实例拦；在临时数据目录里查导入、文字、五种窗口、OCR。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shotrans import ocr, selftest
from shotrans.runtime import SingleInstance

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "shotrans"
NAMES = ["imports", "stdlib-native", "texts", "window:settings", "window:wizard", "window:doctor",
         "window:history", "window:about", "notice", "ocr"]


def _on_disk_modules():
    out = []
    for p in PKG.rglob("*.py"):
        rel = p.relative_to(ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        if name != "shotrans.__main__":
            out.append(name)
    return sorted(out)


def test_module_list_matches_directory():
    assert list(selftest.MODULES) == _on_disk_modules()


def test_run_in_process_all_ok(qapp, tmp_path):
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 0
    r = json.loads(out.read_text(encoding="utf-8"))
    assert r["ok"] is True and r["version"] and r["frozen"] is False
    assert [c["name"] for c in r["checks"]] == NAMES
    assert all(c["ok"] for c in r["checks"]), r["checks"]


def test_no_ocr_language_is_skip_not_failure(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "available_languages", lambda: [])
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 0
    c = {c["name"]: c for c in json.loads(out.read_text(encoding="utf-8"))["checks"]}["ocr"]
    assert c["ok"] is True and "skip" in c["detail"]


def test_failure_gives_exit_1_and_reason(qapp, tmp_path, monkeypatch):
    def boom(_):
        raise RuntimeError("坏了")
    monkeypatch.setattr(ocr, "recognize", boom)
    monkeypatch.setattr(ocr, "available_languages", lambda: ["en-US"])
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 1
    r = json.loads(out.read_text(encoding="utf-8"))
    c = {c["name"]: c for c in r["checks"]}["ocr"]
    assert r["ok"] is False and c["ok"] is False and "坏了" in c["detail"]


def _rows(out):
    return {c["name"]: c for c in json.loads(out.read_text(encoding="utf-8"))["checks"]}


def test_stdlib_native_reports_openssl_version(qapp, tmp_path):
    import ssl
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 0
    c = _rows(out)["stdlib-native"]
    assert c["ok"] is True and ssl.OPENSSL_VERSION in c["detail"]


# 每个原生扩展单独一条夹具：让它的导入失败，只看这一行变红（一条不替另一条顶着）
@pytest.mark.parametrize("mod", ["_ssl", "_hashlib", "_lzma", "_bz2", "pyexpat", "_ctypes"])
def test_stdlib_native_fails_when_extension_import_fails(qapp, tmp_path, monkeypatch, mod):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == mod:
            raise ImportError(f"DLL load failed while importing {mod}: 假的")
        return real(name, *a, **k)
    for m in [m for m in sys.modules if m == mod or m in _DEPENDENTS.get(mod, ())]:
        monkeypatch.delitem(sys.modules, m)
    monkeypatch.setattr(builtins, "__import__", fake)
    out = tmp_path / "r.json"
    rc_ = selftest.run_selftest(out, home=tmp_path / "home")
    monkeypatch.setattr(builtins, "__import__", real)
    c = _rows(out)["stdlib-native"]
    assert rc_ == 1 and c["ok"] is False and mod in c["detail"]


# 导入了这些扩展的标准库模块：要从 sys.modules 里拿掉，才会真的重新导入扩展
_DEPENDENTS = {"_ssl": ("ssl",), "_hashlib": ("hashlib",), "_lzma": ("lzma",), "_bz2": ("bz2",),
               "pyexpat": ("xml.parsers.expat",), "_ctypes": ("ctypes",)}


def test_stdlib_native_fails_when_ca_bundle_missing(qapp, tmp_path, monkeypatch):
    import certifi
    import httpx

    class Fine:   # httpx.Client 自己也读 certifi.where()：换成无害的，免得它替 cafile 那一行顶着
        def close(self):
            pass
    monkeypatch.setattr(httpx, "Client", Fine)
    monkeypatch.setattr(certifi, "where", lambda: str(tmp_path / "no-cacert.pem"))
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 1
    assert _rows(out)["stdlib-native"]["ok"] is False


def test_stdlib_native_fails_when_httpx_client_fails(qapp, tmp_path, monkeypatch):
    import httpx

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("httpx 坏了")
    monkeypatch.setattr(httpx, "Client", Broken)
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 1
    c = _rows(out)["stdlib-native"]
    assert c["ok"] is False and "httpx 坏了" in c["detail"]


# 每个往返守卫单独一条夹具：让结果出错（不是导入失败），只有那个守卫能抓到
@pytest.mark.parametrize("target,value,word", [
    ("_hashlib.new", lambda name, data: __import__("hashlib").md5(data), "_hashlib"),
    ("lzma.decompress", lambda b: b"", "lzma"),
    ("bz2.decompress", lambda b: b"", "bz2"),
    ("ctypes.sizeof", lambda t: 8, "ctypes"),
])
def test_stdlib_native_roundtrip_guards(qapp, tmp_path, monkeypatch, target, value, word):
    import importlib as il
    mod, attr = target.split(".")
    monkeypatch.setattr(il.import_module(mod), attr, value)
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 1
    c = _rows(out)["stdlib-native"]
    assert c["ok"] is False and word in c["detail"]


def test_stdlib_native_expat_guard(qapp, tmp_path, monkeypatch):
    import xml.parsers.expat as ex

    class Deaf:
        StartElementHandler = None

        def Parse(self, data, final):
            pass
    monkeypatch.setattr(ex, "ParserCreate", lambda *a, **k: Deaf())
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 1
    c = _rows(out)["stdlib-native"]
    assert c["ok"] is False and "expat" in c["detail"]


def test_stdlib_native_reports_every_broken_step(qapp, tmp_path, monkeypatch):
    # 前一步坏了不挡后一步：两处同时坏，两处都要列出来
    import bz2
    import lzma
    monkeypatch.setattr(lzma, "decompress", lambda b: b"")
    monkeypatch.setattr(bz2, "decompress", lambda b: b"")
    out = tmp_path / "r.json"
    selftest.run_selftest(out, home=tmp_path / "home")
    d = _rows(out)["stdlib-native"]["detail"]
    assert "_native_lzma" in d and "_native_bz2" in d

def test_selftest_main_requires_path(capsys):
    assert selftest.selftest_main(["prog", "--selftest"]) == 2


def test_subprocess_runs_even_when_instance_is_held(tmp_path):
    """真实单实例互斥量被占着（本机托盘在跑，或这里自己占住）也照常自测：自测在单实例检查之前分派。"""
    holder = SingleInstance("Local\\shotrans-single-instance")
    holder.acquire()   # 托盘已在跑时这里拿不到也没关系：反正互斥量已存在
    try:
        env = dict(os.environ, SHOTRANS_HOME=str(tmp_path / "home"))
        out = tmp_path / "r.json"
        r = subprocess.run([sys.executable, "-m", "shotrans", "--selftest", str(out)],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        assert json.loads(out.read_text(encoding="utf-8"))["ok"] is True
    finally:
        holder.release()


def test_without_home_env_uses_fresh_temp_dir(tmp_path, monkeypatch):
    """没设 SHOTRANS_HOME 时绝不用真实 %APPDATA%\\截图翻译：自己建一个新临时目录。"""
    fake_appdata = tmp_path / "appdata"
    fake_appdata.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "SHOTRANS_HOME"}
    env["APPDATA"] = str(fake_appdata)
    out = tmp_path / "r.json"
    r = subprocess.run([sys.executable, "-m", "shotrans", "--selftest", str(out)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    home = Path(json.loads(out.read_text(encoding="utf-8"))["home"])
    assert not (fake_appdata / "截图翻译").exists()
    assert "shotrans-selftest-" in home.name


def test_no_selftest_flag_unchanged(monkeypatch):
    """没有 --selftest 时 main 不进自测分支。"""
    from shotrans import app as app_mod
    called = []
    monkeypatch.setattr(selftest, "selftest_main", lambda argv: called.append(argv) or 0)
    monkeypatch.setattr(app_mod, "_early_language", lambda home: (_ for _ in ()).throw(SystemExit(7)))
    with pytest.raises(SystemExit):
        app_mod.main(["prog"])
    assert called == []


def test_port_failure_is_failed_report_not_exception(qapp, tmp_path, monkeypatch):
    """准备工作（端口）出错：不抛异常，写出 ok=false 的结果，五个窗口记为跳过失败，退出码 1。"""
    def boom(home):
        raise RuntimeError("端口坏了")
    monkeypatch.setattr(selftest, "_Port", boom)
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 1
    r = json.loads(out.read_text(encoding="utf-8"))
    names = [c["name"] for c in r["checks"]]
    assert r["ok"] is False
    assert "setup:port" in names and all(f"window:{k}" in names for k in ("settings", "wizard", "doctor", "history", "about"))
    assert not any(c["ok"] for c in r["checks"] if c["name"].startswith("window:"))


def test_qapp_failure_is_failed_report_not_exception(tmp_path, monkeypatch):
    """QApplication 建不出来（如 QT_QPA_PLATFORM 指向不存在的插件）：记 setup:qapp 失败，窗口记跳过失败，退出码 1。"""
    class Broken:
        @staticmethod
        def instance():
            return None

        def __init__(self, *a, **k):
            raise RuntimeError("没有平台插件")
    monkeypatch.setattr("PySide6.QtWidgets.QApplication", Broken)
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=tmp_path / "home") == 1
    names = {c["name"]: c for c in json.loads(out.read_text(encoding="utf-8"))["checks"]}
    assert names["setup:qapp"]["ok"] is False and "没有平台插件" in names["setup:qapp"]["detail"]
    assert names["window:about"]["ok"] is False
    assert names["notice"]["ok"] is False  # 提示小窗也要 QApplication


def test_home_unusable_is_failed_report_not_exception(qapp, tmp_path):
    """数据目录建不出来（路径被一个文件占着）：记 setup:home 失败，仍写出结果，退出码 1。"""
    blocker = tmp_path / "file"
    blocker.write_text("x")
    out = tmp_path / "r.json"
    assert selftest.run_selftest(out, home=blocker / "sub") == 1
    r = json.loads(out.read_text(encoding="utf-8"))
    assert r["ok"] is False and "setup:home" in [c["name"] for c in r["checks"]]


def test_unwritable_result_path_returns_1(qapp, tmp_path):
    """结果文件写不出去（父路径是个文件）：返回 1，不抛异常。"""
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert selftest.run_selftest(blocker / "r.json", home=tmp_path / "home") == 1


def test_selftest_main_swallows_unexpected_exception(monkeypatch, tmp_path):
    def boom(out):
        raise RuntimeError("意外")
    monkeypatch.setattr(selftest, "run_selftest", boom)
    assert selftest.selftest_main(["prog", "--selftest", str(tmp_path / "r.json")]) == 1


"""许可证收集：只用本机已装包的元数据，不联网。"""
import importlib.util
from pathlib import Path

from tools import licenses

ROOT = Path(__file__).resolve().parents[1]


def _names():
    return {d.metadata["Name"].lower().replace("_", "-") for d in licenses.runtime_dists()}


def test_closure_has_runtime_deps_but_not_dev_tools():
    n = _names()
    assert {"pyside6", "shiboken6", "pillow", "numpy", "httpx", "httpcore", "h11", "certifi",
            "idna", "anyio", "winrt-runtime", "winrt-windows.media.ocr"} <= n
    assert not n & {"pytest", "pytest-qt", "pymupdf", "pyinstaller"}
    assert "pygments" not in n   # httpx[cli] 的可选依赖：带 extra 标记的要求不进闭包


def test_collect_copies_files_and_notes_missing(tmp_path):
    rows = licenses.collect(tmp_path)
    by = {r["name"].lower().replace("_", "-"): r for r in rows}
    assert any(f.lower().startswith("license") for f in by["httpx"]["files"])
    assert (tmp_path / "third_party" / "httpx").is_dir()
    note = tmp_path / "third_party" / "winrt-runtime" / "NO_LICENSE_FILE.txt"
    assert note.exists() and "MIT" in note.read_text(encoding="utf-8")
    assert "github.com/pywinrt/pywinrt" in note.read_text(encoding="utf-8")   # winrt 总是补官方仓库地址
    assert not (tmp_path / "third_party" / "httpx" / "NO_LICENSE_FILE.txt").exists()   # 有许可文件的不写占位说明
    assert (tmp_path / "third_party" / "python" / "LICENSE.txt").exists()
    if importlib.util.find_spec("PyInstaller"):
        assert (tmp_path / "third_party" / "pyinstaller" / "COPYING.txt").exists()
    listing = (tmp_path / "组件清单.txt").read_text(encoding="utf-8")
    assert "PySide6" in listing and "httpx" in listing


def test_repo_ships_lgpl_and_gpl_full_text():
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (ROOT / "licenses" / "LGPL-3.0.txt").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in (ROOT / "licenses" / "GPL-3.0.txt").read_text(encoding="utf-8")


def test_mit_license_names_author():
    text = (ROOT / "packaging" / "LICENSE.txt").read_text(encoding="utf-8")
    assert text.startswith("MIT License") and "Copyright (c) 2026 greypapl" in text
    assert (ROOT / "LICENSE").read_text(encoding="utf-8") == text


def test_usage_doc_trial_and_relink_promises():
    """试用模式不动开机自启、移动文件夹后点一次「应用」：中英两段都要写到。"""
    text = (ROOT / "packaging" / "使用说明.txt").read_text(encoding="utf-8")
    for must in ("也不会建立或删除开机自启的快捷方式", "没法在试用模式里测开机自启", "点一次「应用」",
                 "never creates or deletes the", "it can't be tested there", "press Apply once"):
        assert must in text, must
    assert "重新勾选" not in text and "tick it again" not in text  # 旧说法（重新勾选不会重写快捷方式）


def test_usage_doc_covers_required_topics():
    text = (ROOT / "packaging" / "使用说明.txt").read_text(encoding="utf-8")
    for must in ("Ctrl+Alt+Q", "%APPDATA%\\截图翻译", "更多信息", "仍要运行", "More info", "Run anyway",
                 "开机自启", "卸载", "Uninstall", "误报", "false positive", "用临时数据目录启动.bat"):
        assert must in text, must


def test_trial_bat_is_ascii_and_sets_home():
    raw = (ROOT / "packaging" / "用临时数据目录启动.bat").read_bytes()
    raw.decode("ascii")                       # 不含中文：cmd 默认代码页下也能正确执行
    text = raw.decode("ascii")
    assert "SHOTRANS_HOME" in text and "%TEMP%" in text and "*.exe" in text
    assert 'set "SHOTRANS_TRIAL=1"' in text   # 试用模式：程序不建、不删「启动」文件夹里的快捷方式
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")  # bat 用 CRLF，没有单独的 LF


class _FakeDist:
    """最小的 Distribution 替身：只给 collect() 用到的属性，文件放在 tmp_path 下。"""

    def __init__(self, root, name, files, extra_meta=None):
        from email.message import Message
        self._root, self.version, self.requires = root, "1.0", []
        self.metadata = Message()
        self.metadata["Name"] = name
        for k, v in (extra_meta or {}).items():
            self.metadata[k] = v
        self.files = list(files)
        for f in files:
            (root / f).parent.mkdir(parents=True, exist_ok=True)
            (root / f).write_text(f"text of {f}", encoding="utf-8")

    def locate_file(self, f):
        return self._root / str(f)


def test_license_file_selection_rules(tmp_path):
    files = ["x-1.0.dist-info/LICENSE", "x-1.0.dist-info/COPYING.txt", "x-1.0.dist-info/NOTICE",
             "x-1.0.dist-info/AUTHORS", "x-1.0.dist-info/licenses/deep/Third-Party-Terms.txt",
             "x-1.0.dist-info/METADATA", "x-1.0.dist-info/RECORD",
             "x/LICENSE", "x/data/COPYING"]            # 包目录里的同名文件不是该包的许可文本
    d = _FakeDist(tmp_path, "x", files)
    got = {str(p.relative_to(tmp_path)).replace("\\", "/") for p in licenses._license_files(d)}
    assert got == {"x-1.0.dist-info/LICENSE", "x-1.0.dist-info/COPYING.txt", "x-1.0.dist-info/NOTICE",
                   "x-1.0.dist-info/AUTHORS", "x-1.0.dist-info/licenses/deep/Third-Party-Terms.txt"}


def test_winrt_without_license_file_always_gets_pywinrt_note(tmp_path, monkeypatch):
    d = _FakeDist(tmp_path / "site", "winrt-fake", ["winrt_fake-1.0.dist-info/METADATA"])   # 元数据里没有任何 URL/许可名
    monkeypatch.setattr(licenses, "runtime_dists", lambda: [d])
    licenses.collect(tmp_path / "out")
    note = (tmp_path / "out" / "third_party" / "winrt-fake" / "NO_LICENSE_FILE.txt").read_text(encoding="utf-8")
    assert "pywinrt: MIT" in note and "github.com/pywinrt/pywinrt" in note
    d2 = _FakeDist(tmp_path / "site2", "other", ["other-1.0.dist-info/METADATA"])
    monkeypatch.setattr(licenses, "runtime_dists", lambda: [d2])
    licenses.collect(tmp_path / "out2")
    assert "pywinrt" not in (tmp_path / "out2" / "third_party" / "other" / "NO_LICENSE_FILE.txt").read_text(encoding="utf-8")
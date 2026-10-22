"""自测：截图翻译.exe --selftest <结果文件>。构建脚本在干净环境里跑它，验证打包版能导入、能开窗、能 OCR。

不建托盘、不注册快捷键、不检查单实例；数据目录用 SHOTRANS_HOME（没设就新建临时目录，绝不碰真实 %APPDATA%）。
不联网：配置里腾讯关闭、主备服务都不填；自检窗口不跑检查项。
窗口用真实平台插件（打包版只带 qwindows），设 WA_DontShowOnScreen，屏幕上看不到。"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

# 全部模块（不含 __main__）。打包版里扫不了目录，所以显式列出；tests/test_selftest.py 保证与目录一致。
MODULES = (
    "shotrans",
    "shotrans.app",
    "shotrans.autostart",
    "shotrans.capture",
    "shotrans.config",
    "shotrans.doctor",
    "shotrans.glossary",
    "shotrans.glossary_edit",
    "shotrans.history",
    "shotrans.hotkey",
    "shotrans.i18n",
    "shotrans.iconart",
    "shotrans.layout",
    "shotrans.migrate",
    "shotrans.models",
    "shotrans.ocr",
    "shotrans.overlay",
    "shotrans.paths",
    "shotrans.pills",
    "shotrans.protect",
    "shotrans.qtimage",
    "shotrans.render",
    "shotrans.runtime",
    "shotrans.secret",
    "shotrans.selftest",
    "shotrans.services",
    "shotrans.session",
    "shotrans.snapshot",
    "shotrans.texts",
    "shotrans.texts.about",
    "shotrans.texts.core",
    "shotrans.texts.doctor",
    "shotrans.texts.glossary",
    "shotrans.texts.history",
    "shotrans.texts.settings",
    "shotrans.texts.wizard",
    "shotrans.tomlw",
    "shotrans.translate",
    "shotrans.translate.base",
    "shotrans.translate.factory",
    "shotrans.translate.router",
    "shotrans.translate.tencent",
    "shotrans.translate.vllm",
    "shotrans.ui",
    "shotrans.ui.about_window",
    "shotrans.ui.doctor_window",
    "shotrans.ui.glossary_page",
    "shotrans.ui.history_window",
    "shotrans.ui.hotkey_edit",
    "shotrans.ui.notice",
    "shotrans.ui.port",
    "shotrans.ui.service_form",
    "shotrans.ui.settings_window",
    "shotrans.ui.windows",
    "shotrans.ui.wizard",
)


class _Port:
    """窗口需要的托盘程序接口（shotrans.ui.port.AppPort）的最小实现：什么都不做。"""

    def __init__(self, home: Path):
        from .config import Config
        from .history import History
        self.home = home
        self.config = Config(tencent_enabled=False)
        self.history = History(home / "history.json", self.config.history_limit)
        self.log_file = home / "logs" / "shotrans.log"

    def save_config(self, new):
        return []

    def open_window(self, kind, page=None):
        return None

    def start_capture(self):
        pass

    def hotkey_free(self, spec):
        return True

    def own_hotkey(self):
        return None

    def reload_config(self):
        return True

    def on_wizard_closed(self, completed):
        pass


def _check(results: list, name: str, fn) -> None:
    try:
        detail = fn() or ""
        results.append({"name": name, "ok": True, "detail": str(detail)})
    except Exception as e:
        results.append({"name": name, "ok": False,
                        "detail": f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=8)}"})


def _imports() -> str:
    for m in MODULES:
        importlib.import_module(m)
    # 复现 MSVCP140 冲突的顺序：winrt 先于 QApplication 创建（见 shotrans/__init__.py）
    from winrt.windows.media.ocr import OcrEngine  # noqa: F401
    return f"{len(MODULES)} modules"


# 下面每一步碰一个原生扩展：它们依赖 conda Library\bin 里的 DLL（libssl/libcrypto、liblzma、libbz2、libexpat、ffi）。
# 打包时混进别的 conda 环境的同名 DLL，就会在这里报「找不到指定的程序」，而不是等用户点「测试连接」。都不联网。

def _native_ssl() -> str:
    import ssl

    import certifi
    import httpx
    ssl.create_default_context(cafile=certifi.where())   # 加载 _ssl 与打包进来的 CA 证书包
    httpx.Client().close()
    return ssl.OPENSSL_VERSION


def _native_hashlib() -> None:
    import _hashlib   # 不走 hashlib：它导入 _hashlib 失败时会悄悄退回内置实现
    if _hashlib.new("sha256", b"x").hexdigest()[:8] != "2d711642":
        raise AssertionError("_hashlib sha256 结果不对")


_DATA = b"shotrans " * 50


def _native_lzma() -> None:
    import lzma
    if lzma.decompress(lzma.compress(_DATA)) != _DATA:
        raise AssertionError("lzma 往返不一致")


def _native_bz2() -> None:
    import bz2
    if bz2.decompress(bz2.compress(_DATA)) != _DATA:
        raise AssertionError("bz2 往返不一致")


def _native_expat() -> None:
    import xml.parsers.expat
    seen = []
    p = xml.parsers.expat.ParserCreate()
    p.StartElementHandler = lambda name, attrs: seen.append(name)
    p.Parse("<a/>", True)
    if seen != ["a"]:
        raise AssertionError(f"expat 解析结果不对：{seen!r}")


def _native_ctypes() -> None:
    import _ctypes  # noqa: F401
    import ctypes
    if ctypes.sizeof(ctypes.c_int) != 4:
        raise AssertionError("ctypes c_int 不是 4 字节")


NATIVE_STEPS = (
    _native_ssl,
    _native_hashlib,
    _native_lzma,
    _native_bz2,
    _native_expat,
    _native_ctypes,
)


def _stdlib_native() -> str:
    """每一步都跑（一步坏了不挡后面的），坏的全部列出来；通过时详情是 OpenSSL 版本。"""
    errors, detail = [], []
    for step in NATIVE_STEPS:
        try:
            r = step()
            if r:
                detail.append(str(r))
        except Exception as e:
            errors.append(f"{step.__name__}: {type(e).__name__}: {e}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return " ".join(detail)

def _texts() -> str:
    from . import i18n
    tb = i18n.tables()
    if not tb["zh"] or set(tb["zh"]) != set(tb["en"]):
        raise AssertionError("中英文字表键不一致或为空")
    return f"{len(tb['zh'])} keys"


def _window(kind: str, port: _Port):
    def run() -> str:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        if kind == "settings":
            from .ui.settings_window import SettingsWindow
            w = SettingsWindow(port, autostart_state=lambda: False, open_path=lambda p: None,
                               open_url=lambda u: None)
        elif kind == "wizard":
            from .ui.wizard import Wizard
            w = Wizard(port, open_url=lambda u: None, autostart_state=lambda: False)
        elif kind == "doctor":
            from .ui.doctor_window import DoctorWindow
            w = DoctorWindow(port, items_factory=lambda: [], open_url=lambda u: None)
        elif kind == "history":
            from .ui.history_window import HistoryWindow
            w = HistoryWindow(port)
        else:
            from .ui.about_window import AboutWindow
            w = AboutWindow(port, open_path=lambda p: None)
        w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        w.show()
        QApplication.processEvents()
        title = w.windowTitle()
        w.close()
        QApplication.processEvents()
        return title
    return run


def _notice() -> str:
    """右下角的提示小窗（所有提醒都靠它，不走 Windows 通知）：能建、能排版、能「显示」（不上屏）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from .i18n import t
    from .ui.notice import NoticePopup
    w = NoticePopup()
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.show_notice(t("app.name"), t("notice.reloaded"), QSystemTrayIcon.MessageIcon.Warning)
    QApplication.processEvents()
    title = w.title_label.text()
    w.close()
    QApplication.processEvents()
    return title


def _ocr() -> str:
    from PIL import Image, ImageDraw, ImageFont

    from . import ocr
    langs = ocr.available_languages()
    if not langs:
        return "skip: no OCR language installed"
    img = Image.new("RGB", (420, 90), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except OSError:
        font = ImageFont.load_default(size=44)
    ImageDraw.Draw(img).text((16, 16), "Hello 2026", fill="black", font=font)
    lines = ocr.recognize(img)
    text = " ".join(ln.text for ln in lines)
    if "Hello" not in text and "2026" not in text:
        raise AssertionError(f"OCR 没认出测试图：{text!r}")
    return f"langs={','.join(langs)} text={text!r}"


def _fail_row(name: str, e: BaseException) -> dict:
    return {"name": name, "ok": False,
            "detail": f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=8)}"}


def run_selftest(out: Path, home: Path | None = None) -> int:
    """跑全部检查并写结果文件；返回 0 全过 / 1 有失败。准备工作（建目录、QApplication、端口）出错也记成
    失败行，结果文件写不出去就返回 1：打包版是窗口程序，异常逃出去会弹阻塞的「Unhandled exception」框。"""
    import shotrans
    results: list = []
    if home is None:
        env = os.environ.get("SHOTRANS_HOME", "").strip()
        try:
            home = Path(env) if env else Path(tempfile.mkdtemp(prefix="shotrans-selftest-"))
        except Exception as e:
            results.append(_fail_row("setup:home", e))
            home = Path(tempfile.gettempdir())
    try:
        home.mkdir(parents=True, exist_ok=True)
        os.environ["SHOTRANS_HOME"] = str(home)   # 窗口里间接用到 paths.data_dir() 的地方也落到这里
    except Exception as e:
        results.append(_fail_row("setup:home", e))
    _check(results, "imports", _imports)
    _check(results, "stdlib-native", _stdlib_native)
    _check(results, "texts", _texts)
    qapp = None
    try:
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance() or QApplication([sys.argv[0] if sys.argv else "shotrans"])
    except Exception as e:
        results.append(_fail_row("setup:qapp", e))
    port = None
    try:
        port = _Port(home)
    except Exception as e:
        results.append(_fail_row("setup:port", e))
    for kind in ("settings", "wizard", "doctor", "history", "about"):
        if qapp is None or port is None:
            results.append({"name": f"window:{kind}", "ok": False, "detail": "skipped: setup failed"})
        else:
            _check(results, f"window:{kind}", _window(kind, port))
    if qapp is None:
        results.append({"name": "notice", "ok": False, "detail": "skipped: setup failed"})
    else:
        _check(results, "notice", _notice)
    _check(results, "ocr", _ocr)
    loaded = getattr(shotrans, "LOADED_MSVCP140", None)
    report = {"ok": all(r["ok"] for r in results), "version": shotrans.__version__,
              "frozen": bool(getattr(sys, "frozen", False)), "python": sys.version.split()[0],
              "msvcp140": None if loaded is None else str(loaded), "home": str(home), "checks": results}
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        _warn("截图翻译 selftest：结果文件写不出去\n" + traceback.format_exc(limit=4))
        return 1
    return 0 if report["ok"] else 1   # qapp 留给进程退出时回收


def _warn(msg: str) -> None:
    if sys.stderr is not None:   # 窗口程序没有 stderr
        print(msg, file=sys.stderr)


def selftest_main(argv: list[str]) -> int:
    i = argv.index("--selftest")
    if i + 1 >= len(argv):
        _warn("用法：截图翻译.exe --selftest <结果文件.json>")
        return 2
    try:
        return run_selftest(Path(argv[i + 1]))
    except Exception:   # 兜底：任何漏网异常都变成退出码 1，不让它逃成阻塞弹框
        _warn("截图翻译 selftest：未预期的异常\n" + traceback.format_exc(limit=6))
        return 1

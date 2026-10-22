"""方案 B 体积过滤（截图翻译.spec 调用）：只留程序实际用到的 Qt Core/Gui/Widgets 及其插件；
去掉 Qt 翻译、软件 OpenGL、其他 Qt 模块、numpy/Pillow 的测试、Pillow 用不到的格式。
程序只 import PySide6.QtCore/QtGui/QtWidgets（grep 可证）。改动这里后必须跑真构建的 --selftest。"""
from __future__ import annotations

import re

EXCLUDES = [
    "tkinter", "_tkinter", "unittest", "pydoc_data", "lib2to3", "test", "idlelib", "turtledemo",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.QtPdf",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtNetwork", "PySide6.QtSvg", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDBus", "PySide6.QtPrintSupport",
    "PIL.ImageTk", "PIL._imagingtk", "PIL.ImageQt", "PIL.AvifImagePlugin", "PIL._avif", "PIL.WebPImagePlugin",
    "PIL._webp",
]
_QT_KEEP_DLL = re.compile(r"^qt6(core|gui|widgets)\.dll$", re.I)
# styles: None 表示整个目录都留；其余目录只留列出的文件；没列出的插件目录整个去掉
_PLUGIN_KEEP = {"platforms": {"qwindows.dll"}, "styles": None, "imageformats": {"qico.dll", "qjpeg.dll"}}
# 任何包下的 tests/、test/ 目录，Qt 翻译、qml 目录
_DROP_PARTS = re.compile(r"(^|/)(tests?|translations|qml)(/|$)", re.I)
_PIL_DROP = re.compile(r"^pil/(_avif|_webp|_imagingtk)\.", re.I)


def keep(dest: str) -> bool:
    """dest 是 PyInstaller TOC 里的目标路径（\\ 或 / 分隔都认）。"""
    low = dest.replace("\\", "/").lower()
    if _DROP_PARTS.search(low) or low.endswith("opengl32sw.dll") or _PIL_DROP.match(low):
        return False
    if low.startswith("pyside6/plugins/"):
        parts = low.split("/")
        if len(parts) < 4:
            return False
        allowed = _PLUGIN_KEEP.get(parts[2], set())
        return allowed is None or parts[3] in allowed
    if low.startswith("pyside6/qt6") and low.endswith(".dll"):
        return bool(_QT_KEEP_DLL.match(low.split("/")[-1]))
    return True

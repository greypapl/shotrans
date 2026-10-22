"""方案 B 的体积过滤：只留 Core/Gui/Widgets 及其依赖；去掉翻译、软件 OpenGL、无用插件、测试。"""
import pytest

from tools.bundle_filter import EXCLUDES, keep

KEEP = [
    "PySide6/Qt6Core.dll", "PySide6/Qt6Gui.dll", "PySide6/Qt6Widgets.dll", "PySide6/QtCore.pyd",
    "PySide6/msvcp140.dll", "PySide6/plugins/platforms/qwindows.dll", "PySide6/plugins/styles/qmodernwindowsstyle.dll",
    "PySide6/plugins/imageformats/qico.dll", "PySide6/plugins/imageformats/qjpeg.dll",
    "shiboken6/shiboken6.abi3.dll", "numpy/_core/_multiarray_umath.cp312-win_amd64.pyd",
    "numpy.libs/libscipy_openblas64_-abc.dll", "PIL/_imaging.cp312-win_amd64.pyd", "PIL/_imagingft.cp312-win_amd64.pyd",
    "winrt/_winrt.pyd", "certifi/cacert.pem", "python312.dll", "shotrans/assets/icon.ico",
]
DROP = [
    "PySide6/translations/qtbase_zh_CN.qm", "PySide6/opengl32sw.dll", "PySide6/Qt6WebEngineCore.dll",
    "PySide6/Qt6Qml.dll", "PySide6/Qt6Quick.dll", "PySide6/Qt6Pdf.dll", "PySide6/Qt6Multimedia.dll",
    "PySide6/Qt63DCore.dll", "PySide6/Qt6Charts.dll", "PySide6/Qt6Network.dll", "PySide6/Qt6Svg.dll",
    "PySide6/qml/QtQuick/x.dll", "PySide6/plugins/imageformats/qpdf.dll", "PySide6/plugins/imageformats/qsvg.dll",
    "PySide6/plugins/iconengines/qsvgicon.dll", "PySide6/plugins/platforms/qoffscreen.dll",
    "PySide6/plugins/tls/qschannelbackend.dll", "PySide6/plugins/networkinformation/qnetworklistmanager.dll",
    "PySide6/plugins/generic/qtuiotouchplugin.dll", "numpy/_core/tests/test_x.py", "PIL/tests/x.py",
    "PIL/_avif.cp312-win_amd64.pyd", "PIL/_webp.cp312-win_amd64.pyd", "PIL/_imagingtk.cp312-win_amd64.pyd",
]


@pytest.mark.parametrize("dest", KEEP)
def test_keeps(dest):
    assert keep(dest) and keep(dest.replace("/", "\\"))


@pytest.mark.parametrize("dest", DROP)
def test_drops(dest):
    assert not keep(dest) and not keep(dest.replace("/", "\\"))


def test_excludes_cover_stdlib_and_qt_modules():
    for m in ("tkinter", "unittest", "pydoc_data", "lib2to3", "test", "PySide6.QtWebEngineCore",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtNetwork", "PIL.ImageTk", "PIL.AvifImagePlugin",
              "PIL.WebPImagePlugin"):
        assert m in EXCLUDES
    for m in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PIL.PngImagePlugin", "PIL.ImageFont"):
        assert m not in EXCLUDES

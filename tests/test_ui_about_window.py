import pytest

import shotrans
from shotrans import i18n, paths
from shotrans.ui.about_window import LICENSES, AboutWindow
from tests.ui_fakes import FakeApp


def test_version():
    assert shotrans.__version__ == "1.0.0"


@pytest.fixture
def make(qtbot, tmp_path, track):
    opened = []

    def _make():
        app = FakeApp(tmp_path)
        w = AboutWindow(app, open_path=opened.append)
        track(w)
        w.show()
        return w, app
    _make.opened = opened
    return _make


def test_shows_version(make):
    w, _ = make()
    assert "1.0.0" in w.version_label.text()


def test_license_text_lists_components(make):
    w, _ = make()
    text = w.license_view.toPlainText()
    for name, lic in [("PySide6", "LGPL-3.0"), ("Pillow", "MIT-CMU"), ("numpy", "BSD-3-Clause"),
                      ("httpx", "BSD-3-Clause"), ("pywinrt", "MIT")]:
        assert name in text and lic in text
    assert "download.qt.io" in text  # LGPL 要求的源码获取说明
    assert "https://www.gnu.org/licenses/lgpl-3.0.html" in text  # 许可证官方网址


def test_licenses_table():
    assert LICENSES == [("PySide6 / Qt", "LGPL-3.0"), ("Pillow", "MIT-CMU"), ("numpy", "BSD-3-Clause"),
                        ("httpx", "BSD-3-Clause"), ("pywinrt (winrt-*)", "MIT")]


def test_open_data_folder(make):
    w, app = make()
    w.btn_data.click()
    assert make.opened == [app.home]


def test_open_log_falls_back_to_folder(make):
    w, app = make()
    w.btn_log.click()
    assert make.opened == [app.home]  # 日志和日志目录都还没有：打开数据目录
    app.log_file.parent.mkdir(parents=True)
    w.btn_log.click()
    assert make.opened[-1] == app.log_file.parent
    app.log_file.write_text("x", encoding="utf-8")
    w.btn_log.click()
    assert make.opened[-1] == app.log_file


def test_open_license_notes(make):
    w, _ = make()
    w.btn_lgpl.click()
    assert make.opened == [paths.program_dir() / "licenses" / "README.md"]


def test_license_readme_present():
    """许可证全文随打包轮附带；这一轮 licenses 目录只放说明（组件、许可名、官方网址）。"""
    text = (paths.program_dir() / "licenses" / "README.md").read_text(encoding="utf-8")
    for needle in ["PySide6", "LGPL-3.0", "Pillow", "MIT-CMU", "numpy", "httpx", "BSD-3-Clause", "pywinrt",
                   "https://www.gnu.org/licenses/lgpl-3.0.html", "https://download.qt.io"]:
        assert needle in text


def test_language_switch(make):
    w, _ = make()
    i18n.set_language("en")
    assert w.windowTitle() == "About ShotTrans"


def test_urls_clickable_without_opening(make):
    w, _ = make()
    assert w.license_view.openExternalLinks() is True
    html_text = w.license_view.toHtml()
    assert 'href="https://download.qt.io/official_releases/QtForPython/"' in html_text
    assert 'href="https://www.gnu.org/licenses/lgpl-3.0.html"' in html_text
    assert 'href="https://code.qt.io/"' in html_text

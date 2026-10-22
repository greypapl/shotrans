"""关于窗口：版本、简短用法、打开数据文件夹 / 日志、第三方组件许可说明。"""
from __future__ import annotations

import html
import os
import re
from typing import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

import shotrans

from .. import i18n, paths
from ..i18n import t

URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]+")


def linkify(text: str) -> str:
    """纯文字转 HTML：转义后把网址包成可点的链接，换行保留。"""
    body = URL_RE.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', html.escape(text, quote=False))
    return body.replace("\n", "<br>")


LICENSES = [("PySide6 / Qt", "LGPL-3.0"), ("Pillow", "MIT-CMU"), ("numpy", "BSD-3-Clause"),
            ("httpx", "BSD-3-Clause"), ("pywinrt (winrt-*)", "MIT")]


class AboutWindow(QWidget):
    def __init__(self, app, open_path: Callable[[object], None] | None = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._open = open_path or (lambda p: os.startfile(p))
        self.version_label = QLabel(self)
        self.usage_label = QLabel(self)
        self.usage_label.setWordWrap(True)
        self.license_title = QLabel(self)
        self.license_view = QTextBrowser(self)
        self.license_view.setOpenExternalLinks(True)
        self.btn_data, self.btn_log, self.btn_lgpl = QPushButton(self), QPushButton(self), QPushButton(self)
        self.btn_data.clicked.connect(lambda: self._open(self._app.home))
        self.btn_log.clicked.connect(self._open_log)
        self.btn_lgpl.clicked.connect(lambda: self._open(paths.program_dir() / "licenses" / "README.md"))
        bar = QHBoxLayout()
        for b in (self.btn_data, self.btn_log, self.btn_lgpl):
            bar.addWidget(b)
        bar.addStretch(1)
        lay = QVBoxLayout(self)
        for w in (self.version_label, self.usage_label):
            lay.addWidget(w)
        lay.addLayout(bar)
        lay.addWidget(self.license_title)
        lay.addWidget(self.license_view, 1)
        self.resize(560, 420)
        i18n.hub().language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self, *_) -> None:
        self.setWindowTitle(t("about.title"))
        self.version_label.setText(t("about.version", version=shotrans.__version__))
        self.usage_label.setText(t("about.usage"))
        self.btn_data.setText(t("about.open_data"))
        self.btn_log.setText(t("about.open_log"))
        self.btn_lgpl.setText(t("about.open_lgpl"))
        self.license_title.setText(t("about.licenses"))
        lines = [t("about.license_line", name=n, license=lic) for n, lic in LICENSES]
        self.license_view.setHtml(linkify("\n".join(lines) + "\n\n" + t("about.lgpl_note")))

    def _open_log(self) -> None:
        f = self._app.log_file
        self._open(f if f.exists() else f.parent if f.parent.exists() else self._app.home)

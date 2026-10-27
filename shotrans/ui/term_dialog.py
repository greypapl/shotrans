"""浮窗右键「添加术语…」的对话框：上面是这一段的原文（选中一个词自动填进英文/中文框），下面填词条，
确定时检查、问一下（已有同一英文 / 译法重复 / 单个汉字），通过后写进术语表文件。写成功才 accept，saved = (英文, 中文)。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from ..glossary_edit import load_doc, save_doc
from ..i18n import N_, t
from ..term_add import check_term, put_term, side_of


ERROR_KEYS = {"empty_en": N_("term.err.empty_en"), "empty_zh": N_("term.err.empty_zh"),
              "bad_newline": N_("term.err.bad_newline"), "bad_src": N_("term.err.bad_src")}
WARN_KEYS = {"dup_dst": N_("term.warn.dup_dst"), "single_hanzi": N_("term.warn.single_hanzi")}


class TermDialog(QDialog):
    def __init__(self, source_text: str, path: Path, ask: Callable[[str, str], bool] | None = None,
                 warn: Callable[[str, str], None] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)  # 浮窗是置顶的：对话框不能被它盖住
        self.path = Path(path)
        self._ask = ask or (lambda title, text: QMessageBox.question(self, title, text)
                            == QMessageBox.StandardButton.Yes)
        self._warn = warn or (lambda title, text: QMessageBox.warning(self, title, text))
        self.saved: tuple[str, str] | None = None
        self.setWindowTitle(t("term.title"))
        self.source = QPlainTextEdit(source_text, self)
        self.source.setReadOnly(True)
        self.source.setMaximumHeight(110)
        self.source.selectionChanged.connect(self._on_selection)
        self.en, self.zh = QLineEdit(self), QLineEdit(self)
        self.keep = QCheckBox(t("term.keep"), self)
        self.keep.toggled.connect(lambda on: self.zh.setEnabled(not on))
        tip = QLabel(t("term.case_tip"), self)
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#6b7280")
        self.error = QLabel(self)
        self.error.setStyleSheet("color:#ef4444")
        self.btn_ok, self.btn_cancel = QPushButton(t("term.ok"), self), QPushButton(t("term.cancel"), self)
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        form = QFormLayout()
        form.addRow(t("term.en"), self.en)
        form.addRow(t("term.zh"), self.zh)
        form.addRow("", self.keep)
        buttons = QHBoxLayout()
        buttons.addWidget(self.error, 1)
        buttons.addWidget(self.btn_ok)
        buttons.addWidget(self.btn_cancel)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(t("term.source_label"), self))
        lay.addWidget(self.source)
        lay.addLayout(form)
        lay.addWidget(tip)
        lay.addLayout(buttons)
        self.resize(480, 0)

    def _on_selection(self) -> None:
        text = self.source.textCursor().selectedText().replace(" ", " ").strip()  # 跨行选中时 Qt 用 U+2029 当换行
        if text:
            (self.zh if side_of(text) == "zh" else self.en).setText(text)

    def accept(self) -> None:
        en, zh = self.en.text().strip(), "" if self.keep.isChecked() else self.zh.text().strip()
        try:
            doc = load_doc(self.path)
        except (OSError, UnicodeDecodeError) as e:
            self._warn(t("term.save_failed_title"), t("term.save_failed", error=e))
            return
        c = check_term(doc, en, zh)
        code = c.error or ("empty_zh" if en and not zh and not self.keep.isChecked() else None)
        if code:
            self.error.setText(t(ERROR_KEYS[code]))
            return
        self.error.setText("")
        if c.existing is not None and (c.existing[1], c.existing[2]) != (en, zh):
            if not self._ask(t("term.replace_title"),
                             t("term.replace_text", old=_shown(c.existing[1], c.existing[2]), new=_shown(en, zh))):
                return
        if c.warnings:
            text = "\n".join(t(WARN_KEYS[w], zh=zh) for w in c.warnings) + "\n" + t("term.warn_tail")
            if not self._ask(t("term.warn_title"), text):
                return
        put_term(doc, en, zh)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            save_doc(doc, self.path)
        except OSError as e:
            self._warn(t("term.save_failed_title"), t("term.save_failed", error=e))
            return
        self.saved = (en, zh)
        super().accept()


def _shown(en: str, zh: str) -> str:
    return f"{en} => {zh}" if zh else t("term.keep_shown", en=en)

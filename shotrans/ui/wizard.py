"""首次运行引导：欢迎（选语言）→ 环境检查（OCR 语言包）→ 翻译服务（测试连接成功前不能下一步）→ 完成（快捷键、开机自启、用法）。
中途关掉：托盘程序提示「还没配置翻译服务」，之后按快捷键 / 点托盘会重新打开引导（见 app.on_wizard_closed）。"""
from __future__ import annotations

import dataclasses
import os
from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QRadioButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import autostart, i18n
from ..config import ConfigError
from ..doctor import LANG_SETTINGS_URL, check_free, ocr_install_command
from ..i18n import t
from ..ocr import LANG_EN, LANG_ZH, available_languages
from ..hotkey import same_combo
from .free_test import FreeTest
from .hotkey_edit import HotkeyEdit
from .service_form import ServiceForm

DEFAULT_HOTKEY = "Ctrl+Alt+Q"


class Wizard(QWidget):
    PAGES = ("welcome", "env", "service", "finish")

    def __init__(self, app, languages_fn: Callable[[], list[str]] | None = None, tester=None, lister=None,
                 open_url: Callable[[str], object] | None = None, warn: Callable[[str], None] | None = None,
                 autostart_state: Callable[[], bool] | None = None, clipboard=None,
                 free_tester: Callable[[], tuple[bool, str]] | None = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._languages_fn = languages_fn or available_languages
        self._open_url = open_url or (lambda url: os.startfile(url))
        self._warn = warn or (lambda text: QMessageBox.warning(self, t("wizard.title"), text))
        self._completed = False
        self._langs: set[str] = set()
        self.stack = QStackedWidget(self)

        # 欢迎
        self.welcome_title, self.welcome_text, self.lbl_language = QLabel(), QLabel(), QLabel()
        self.welcome_text.setWordWrap(True)
        self.language = QComboBox()
        self.language.addItem(t("lang.native.zh"), "zh")
        self.language.addItem(t("lang.native.en"), "en")
        self.language.setCurrentIndex(max(0, self.language.findData(i18n.language())))
        self.language.currentIndexChanged.connect(lambda _i: i18n.set_language(self.language.currentData()))
        p0 = QWidget()
        l0 = QVBoxLayout(p0)
        for w in (self.welcome_title, self.welcome_text, self.lbl_language, self.language):
            l0.addWidget(w)
        l0.addStretch(1)

        # 环境检查
        self.env_title, self.lbl_en, self.lbl_zh, self.env_steps = QLabel(), QLabel(), QLabel(), QLabel()
        self.env_steps.setWordWrap(True)
        self.btn_lang_settings, self.btn_recheck = QPushButton(), QPushButton()
        self.btn_lang_settings.clicked.connect(lambda: self._open_url(LANG_SETTINGS_URL))
        self.btn_recheck.clicked.connect(self.recheck)
        self.chk_continue = QCheckBox()
        self.chk_continue.toggled.connect(lambda _on: self._update_nav())
        # 「设置」里下载失败（拒绝访问等）时的备用办法：管理员 PowerShell 命令，只列没装的
        self.cmd_box = QWidget()
        self.env_cmd_note, self.env_denied = QLabel(), QLabel()
        self.env_cmd_note.setWordWrap(True)
        self.env_denied.setWordWrap(True)
        self.cmd_text = QPlainTextEdit()
        self.cmd_text.setReadOnly(True)
        self.cmd_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.cmd_text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.cmd_text.setFixedHeight(self.cmd_text.fontMetrics().lineSpacing() * 2 + 16)
        self.btn_copy_cmd = QPushButton()
        self.btn_copy_cmd.clicked.connect(self.copy_command)
        self._clipboard = clipboard
        lc = QVBoxLayout(self.cmd_box)
        lc.setContentsMargins(0, 8, 0, 0)
        lc.addWidget(self.env_cmd_note)
        lc.addWidget(self.cmd_text)
        row_c = QHBoxLayout()
        row_c.addWidget(self.btn_copy_cmd)
        row_c.addStretch(1)
        lc.addLayout(row_c)
        lc.addWidget(self.env_denied)
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        for w in (self.env_title, self.lbl_en, self.lbl_zh, self.env_steps):
            l1.addWidget(w)
        row = QHBoxLayout()
        row.addWidget(self.btn_lang_settings)
        row.addWidget(self.btn_recheck)
        row.addStretch(1)
        l1.addLayout(row)
        l1.addWidget(self.cmd_box)
        l1.addWidget(self.chk_continue)
        l1.addStretch(1)

        # 翻译服务：两种用法二选一。大模型服务（推荐；预设默认智谱，免费）在上、默认选中；
        # 免费的微软翻译（不用注册、不填 key）在下，给不想注册的人。新用户没有旧 key，ServiceForm 的「清除」按钮不会出现
        self.service_title, self.free_desc, self.service_note = QLabel(), QLabel(), QLabel()
        self.free_desc.setWordWrap(True)
        self.service_note.setWordWrap(True)
        self.mode_free, self.mode_llm = QRadioButton(), QRadioButton()
        self.mode_llm.setChecked(True)
        self.mode_free.toggled.connect(lambda _on: self._show_mode())
        self.free_test = FreeTest(free_tester or (lambda: check_free(dataclasses.replace(app.config,
                                                                                         free_enabled=True))))
        self.free_test.tested.connect(lambda _ok: self._update_nav())
        self.form = ServiceForm(False, tester, lister, open_url)
        self.form.preset.setCurrentIndex(self.form.preset.findData("zhipu"))
        self.form.tested.connect(lambda _ok: self._update_nav())
        self.form.edited.connect(self._update_nav)
        # 大模型连不上时用微软兜底：初值随程序现有配置（新装就是不勾，不勾就不会把文字发给第三方）
        self.free_backup = QCheckBox()
        self.free_backup.setChecked(app.config.free_enabled)
        self.free_box, self.llm_box = QWidget(), QWidget()
        lf = QVBoxLayout(self.free_box)
        lf.setContentsMargins(24, 0, 0, 0)
        lf.addWidget(self.free_desc)
        lf.addWidget(self.free_test)
        ll = QVBoxLayout(self.llm_box)
        ll.setContentsMargins(24, 0, 0, 0)
        for w in (self.service_note, self.form, self.free_backup):
            ll.addWidget(w)
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        for w in (self.service_title, self.mode_llm, self.llm_box, self.mode_free, self.free_box):
            l2.addWidget(w)
        l2.addStretch(1)

        # 完成
        self.finish_title, self.lbl_hotkey, self.usage = QLabel(), QLabel(), QLabel()
        self.usage.setWordWrap(True)
        self.hotkey_edit = HotkeyEdit(app.hotkey_free)
        self.hotkey_edit.set_spec(DEFAULT_HOTKEY)
        self.hotkey_edit.changed.connect(lambda _s: self._update_nav())
        self.autostart = QCheckBox()
        # 初值取快捷方式的真实状态：默认不勾会在完成时把已有的开机自启删掉
        self.autostart.setChecked(bool((autostart_state or autostart.is_enabled)()))
        self.autostart_trial = QLabel()  # 试用模式：框禁用，说明为什么
        self.autostart_trial.setWordWrap(True)
        trial = autostart.trial_mode()
        self.autostart.setEnabled(not trial)
        self.autostart_trial.setVisible(trial)
        self.btn_try = QPushButton()
        self.btn_try.clicked.connect(lambda: self.finish(try_now=True))
        p3 = QWidget()
        l3 = QVBoxLayout(p3)
        for w in (self.finish_title, self.lbl_hotkey, self.hotkey_edit, self.autostart, self.autostart_trial,
                  self.usage, self.btn_try):
            l3.addWidget(w)
        l3.addStretch(1)

        for p in (p0, p1, p2, p3):
            self.stack.addWidget(p)
        self.btn_back, self.btn_next, self.btn_finish = QPushButton(self), QPushButton(self), QPushButton(self)
        self.btn_back.clicked.connect(self.back)
        self.btn_next.clicked.connect(self.next)
        self.btn_finish.clicked.connect(lambda: self.finish())
        nav = QHBoxLayout()
        nav.addStretch(1)
        for b in (self.btn_back, self.btn_next, self.btn_finish):
            nav.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.stack, 1)
        lay.addLayout(nav)
        self.resize(640, 560)

        i18n.hub().language_changed.connect(self.retranslate)
        self._show_mode()
        self.recheck()
        self.retranslate()

    # ---------- 文字 ----------
    def retranslate(self, *_) -> None:
        self.setWindowTitle(t("wizard.title"))
        self.welcome_title.setText(t("wizard.welcome.title"))
        self.welcome_text.setText(t("wizard.welcome.text"))
        self.lbl_language.setText(t("wizard.welcome.language"))
        self.env_title.setText(t("wizard.env.title"))
        self.env_steps.setText(t("wizard.env.steps"))
        self.btn_lang_settings.setText(t("wizard.env.open_settings"))
        self.btn_recheck.setText(t("wizard.env.recheck"))
        self.chk_continue.setText(t("wizard.env.continue_anyway"))
        self.env_cmd_note.setText(t("wizard.env.cmd_note"))
        self.env_denied.setText(t("wizard.env.denied"))
        self.btn_copy_cmd.setText(t("wizard.env.copy_cmd"))
        self.service_title.setText(t("wizard.service.title"))
        self.mode_free.setText(t("wizard.service.mode_free"))
        self.free_desc.setText(t("wizard.service.free_desc"))
        self.mode_llm.setText(t("wizard.service.mode_llm"))
        self.service_note.setText(t("wizard.service.note"))
        self.free_backup.setText(t("wizard.service.free_backup"))
        self.free_test.retranslate()
        self.finish_title.setText(t("wizard.finish.title"))
        self.lbl_hotkey.setText(t("wizard.finish.hotkey"))
        self.autostart.setText(t("wizard.finish.autostart"))
        self.autostart_trial.setText(t("wizard.finish.autostart_trial"))
        self.usage.setText(t("wizard.finish.usage"))
        self.btn_try.setText(t("wizard.try_now"))
        self.btn_back.setText(t("wizard.back"))
        self.btn_next.setText(t("wizard.next"))
        self.btn_finish.setText(t("wizard.finish"))
        self._show_langs()

    # ---------- 环境 ----------
    def recheck(self) -> None:
        try:
            self._langs = set(self._languages_fn())
        except Exception:  # 查不到语言包按「都没装」处理，用户仍可勾「仍然继续」
            self._langs = set()
        self._show_langs()
        self._update_nav()

    def _show_langs(self) -> None:
        for label, tag, key in ((self.lbl_en, LANG_EN, "wizard.env.ocr_en"), (self.lbl_zh, LANG_ZH, "wizard.env.ocr_zh")):
            label.setText(t("wizard.env.have" if tag in self._langs else "wizard.env.missing", name=t(key)))
        self.chk_continue.setVisible(not self._ocr_any())
        cmd = ocr_install_command(tag for tag in (LANG_EN, LANG_ZH) if tag not in self._langs)
        if cmd != self.cmd_text.toPlainText():
            self.cmd_text.setPlainText(cmd)
            self.btn_copy_cmd.setText(t("wizard.env.copy_cmd"))
        self.cmd_box.setVisible(bool(cmd))

    def _ocr_any(self) -> bool:
        return bool(self._langs & {LANG_EN, LANG_ZH})

    def copy_command(self) -> None:
        (self._clipboard or QGuiApplication.clipboard()).setText(self.cmd_text.toPlainText())
        self.btn_copy_cmd.setText(t("wizard.env.copied"))

    # ---------- 翻译服务 ----------
    def free_mode(self) -> bool:
        return self.mode_free.isChecked()

    def _show_mode(self) -> None:
        self.free_box.setVisible(self.free_mode())
        self.llm_box.setVisible(not self.free_mode())
        self._update_nav()

    def _service_ok(self) -> bool:
        """选的那种用法测通了（两种各测各的，切过去再切回来不用重测）。"""
        return self.free_test.test_ok if self.free_mode() else self.form.test_ok

    # ---------- 导航 ----------
    def page(self) -> str:
        return self.PAGES[self.stack.currentIndex()]

    def can_next(self) -> bool:
        p = self.page()
        if p == "env":
            return self._ocr_any() or self.chk_continue.isChecked()
        if p == "service":
            return self._service_ok()
        return p != "finish"

    def _update_nav(self) -> None:
        last = self.page() == "finish"
        self.btn_back.setEnabled(self.stack.currentIndex() > 0)
        self.btn_next.setVisible(not last)
        self.btn_next.setEnabled(self.can_next())
        self.btn_finish.setVisible(last)
        self.btn_finish.setEnabled(last and self.hotkey_edit.is_valid())
        self.btn_try.setEnabled(self.hotkey_edit.is_valid())  # 「现在试一下」也是完成，规则同「完成」

    def next(self) -> None:
        if self.can_next():
            self.stack.setCurrentIndex(self.stack.currentIndex() + 1)
            if self.page() == "finish":  # 打开引导后别的程序可能放开或占了这个键：进完成页时重新试注册
                self.hotkey_edit.set_spec(self.hotkey_edit.spec())
        self._update_nav()

    def back(self) -> None:
        if self.stack.currentIndex() > 0:
            self.stack.setCurrentIndex(self.stack.currentIndex() - 1)
        self._update_nav()

    # ---------- 完成 ----------
    def finish(self, try_now: bool = False) -> bool:
        if self.page() != "finish" or not self._service_ok():  # 没走完前几步、或测试通过后又改了服务：不能保存
            return False
        if not self.hotkey_edit.is_valid():
            return False
        # 以程序当前配置为底（首次运行时就是默认配置），只改引导里填的几项，不丢掉别处已有的设置。
        # 选免费：主服务不动（新用户就是空的），只开免费翻译；选大模型：填主服务，兜底按勾选
        if self.free_mode():
            service = {"free_enabled": True}
        else:
            service = {"primary": self.form.service(), "free_enabled": self.free_backup.isChecked()}
        new = dataclasses.replace(self._app.config, language=i18n.language(), autostart=self.autostart.isChecked(),
                                  hotkey=self.hotkey_edit.spec(), **service)
        if same_combo(new.hotkey, new.clip_hotkey):  # 截图键选了「复制后翻译」的键：让给截图，那个功能先关着
            new = dataclasses.replace(new, clip_hotkey="")
        try:
            self._app.save_config(new)  # 没生效的项（如开机自启写不进）由托盘程序自己汇总成一条提醒
        except ConfigError as e:
            self._warn(t("wizard.save_failed", error=e))
            return False
        self._completed = True
        self.close()
        if try_now:
            QTimer.singleShot(0, self._app.start_capture)
        return True

    def closeEvent(self, e) -> None:
        self.form.shutdown()
        self.free_test.shutdown()
        self._app.on_wizard_closed(self._completed)
        super().closeEvent(e)

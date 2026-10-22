"""设置窗口：翻译服务 / 快捷键 / 术语表 / 常规 / 高级，底部「确定 / 取消 / 应用」。
「应用」「确定」先校验，错项就地标红并跳到那一页，不保存；通过后术语表（改过才写）、配置一起保存，
配置经 app.save_config 写盘并立即生效。「取消」丢弃改动。"""
from __future__ import annotations

import dataclasses
import os
from typing import Callable

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import autostart, config, i18n, paths
from ..config import Config, ConfigError, ServiceConfig
from ..glossary_edit import has_blocking
from ..i18n import N_, t
from .glossary_page import GlossaryPage
from .hotkey_edit import HotkeyEdit
from .service_form import ServiceForm

PAGES = ("service", "hotkey", "glossary", "general", "advanced")
TAB_KEYS = (N_("settings.tab.service"), N_("settings.tab.hotkey"), N_("settings.tab.glossary"),
            N_("settings.tab.general"), N_("settings.tab.advanced"))
DEFAULT_HOTKEY = "Ctrl+Alt+Q"
SECTION_PAGES = {"[primary]": "service", "[backup]": "service", "[hotkey]": "hotkey", "[general]": "general",
                 "[history]": "general"}
# (Config 字段, 名称键, 说明键, 整数?, 最小, 最大, 步长)
ADVANCED = [
    ("chunk_chars", N_("settings.adv.chunk_chars"), N_("settings.adv.chunk_chars_tip"), True, 50, 20000, 50),
    ("chunk_max_segments", N_("settings.adv.chunk_max_segments"), N_("settings.adv.chunk_max_segments_tip"),
     True, 1, 100, 1),
    ("max_concurrency", N_("settings.adv.max_concurrency"), N_("settings.adv.max_concurrency_tip"), True, 1, 16, 1),
    ("connect_timeout", N_("settings.adv.connect_timeout"), N_("settings.adv.connect_timeout_tip"),
     False, 0.5, 120.0, 0.5),
    ("first_token_timeout", N_("settings.adv.first_token_timeout"), N_("settings.adv.first_token_timeout_tip"),
     False, 0.5, 300.0, 0.5),
    ("stall_timeout", N_("settings.adv.stall_timeout"), N_("settings.adv.stall_timeout_tip"), False, 0.5, 300.0, 0.5),
    ("backup_connect_timeout", N_("settings.adv.backup_connect_timeout"),
     N_("settings.adv.backup_connect_timeout_tip"), False, 0.5, 120.0, 0.5),
    ("backup_first_token_timeout", N_("settings.adv.backup_first_token_timeout"),
     N_("settings.adv.backup_first_token_timeout_tip"), False, 0.5, 300.0, 0.5),
    ("backup_stall_timeout", N_("settings.adv.backup_stall_timeout"), N_("settings.adv.backup_stall_timeout_tip"),
     False, 0.5, 300.0, 0.5),
    ("breaker_seconds", N_("settings.adv.breaker_seconds"), N_("settings.adv.breaker_seconds_tip"),
     False, 1.0, 3600.0, 1.0),
    ("tencent_connect_timeout", N_("settings.adv.tencent_connect_timeout"),
     N_("settings.adv.tencent_connect_timeout_tip"), False, 0.5, 120.0, 0.5),
]


def _page_of(field: str) -> str:
    return SECTION_PAGES.get(field.split("]")[0] + "]", "advanced")


class SettingsWindow(QWidget):
    def __init__(self, app, page: str | None = None, tester=None, lister=None,
                 confirm=None, warn: Callable[[str], None] | None = None,
                 open_path: Callable[[object], object] | None = None, open_url=None,
                 autostart_state: Callable[[], bool] | None = None, parent=None):
        super().__init__(parent)
        self._app = app
        self._warn = warn or (lambda text: QMessageBox.warning(self, t("settings.title"), text))
        self._open_path = open_path or (lambda p: os.startfile(p))
        self._autostart_state = autostart_state or autostart.is_enabled
        self.tabs = QTabWidget(self)

        # ① 翻译服务
        self.primary = ServiceForm(False, tester, lister, open_url)
        self.backup = ServiceForm(True, tester, lister, open_url)
        self.box_primary, self.box_backup = QGroupBox(), QGroupBox()
        QVBoxLayout(self.box_primary).addWidget(self.primary)
        QVBoxLayout(self.box_backup).addWidget(self.backup)
        self.tencent = QCheckBox()
        self.domain = QLineEdit()
        self.lbl_domain = QLabel()
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.addWidget(self.box_primary)
        l1.addWidget(self.box_backup)
        l1.addWidget(self.tencent)
        row = QHBoxLayout()
        row.addWidget(self.lbl_domain)
        row.addWidget(self.domain, 1)
        l1.addLayout(row)
        l1.addStretch(1)

        # ② 快捷键
        self.hotkey_edit = HotkeyEdit(app.hotkey_free)
        self.lbl_hotkey = QLabel()
        self.btn_hotkey_default = QPushButton()
        self.btn_hotkey_default.clicked.connect(lambda: self.hotkey_edit.set_spec(DEFAULT_HOTKEY))
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.addWidget(self.lbl_hotkey)
        l2.addWidget(self.hotkey_edit)
        l2.addWidget(self.btn_hotkey_default)
        l2.addStretch(1)

        # ③ 术语表
        self.glossary = GlossaryPage(paths.glossary_path(app.home), confirm=confirm)

        # ④ 常规
        self.language = QComboBox()
        self.language.addItem(t("lang.native.zh"), "zh")
        self.language.addItem(t("lang.native.en"), "en")
        self.autostart = QCheckBox()
        self.autostart_trial = QLabel()  # 试用模式：框禁用，说明为什么
        self.autostart_trial.setWordWrap(True)
        trial = autostart.trial_mode()
        self.autostart.setEnabled(not trial)
        self.autostart_trial.setVisible(trial)
        self.history_limit = QSpinBox()
        self.history_limit.setRange(1, 10000)
        self.history_images = QCheckBox()
        self.btn_open_data = QPushButton()
        self.btn_open_data.clicked.connect(lambda: self._open_path(self._app.home))
        self.lbl_language, self.lbl_history = QLabel(), QLabel()
        p4 = QWidget()
        l4 = QFormLayout(p4)
        l4.addRow(self.lbl_language, self.language)
        l4.addRow("", self.autostart)
        l4.addRow("", self.autostart_trial)
        l4.addRow(self.lbl_history, self.history_limit)
        l4.addRow("", self.history_images)
        l4.addRow("", self.btn_open_data)

        # ⑤ 高级
        self.adv: dict[str, QSpinBox | QDoubleSpinBox] = {}
        self.adv_labels: dict[str, QLabel] = {}
        self.adv_tips: dict[str, QLabel] = {}
        p5 = QWidget()
        l5 = QFormLayout(p5)
        for field, _name, _tip, is_int, lo, hi, step in ADVANCED:
            spin = QSpinBox() if is_int else QDoubleSpinBox()
            if not is_int:
                spin.setDecimals(1)
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            self.adv[field], self.adv_labels[field], self.adv_tips[field] = spin, QLabel(), QLabel()
            self.adv_tips[field].setStyleSheet("color:#6b7280")
            col = QVBoxLayout()
            col.addWidget(spin)
            col.addWidget(self.adv_tips[field])
            l5.addRow(self.adv_labels[field], col)
        self.btn_adv_default = QPushButton()
        self.btn_adv_default.clicked.connect(self._reset_advanced)
        l5.addRow("", self.btn_adv_default)

        for w in (p1, p2, self.glossary, p4, p5):
            self.tabs.addTab(w, "")

        # 底部
        self.error_label = QLabel(self)
        self.error_label.setStyleSheet("color:#ef4444")
        self.btn_ok, self.btn_cancel, self.btn_apply = QPushButton(self), QPushButton(self), QPushButton(self)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.close)
        self.btn_apply.clicked.connect(self.apply)
        bottom = QHBoxLayout()
        bottom.addWidget(self.error_label, 1)
        for b in (self.btn_ok, self.btn_cancel, self.btn_apply):
            bottom.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs, 1)
        lay.addLayout(bottom)
        self.resize(720, 640)

        i18n.hub().language_changed.connect(self.retranslate)
        self.retranslate()
        self.load_from(app.config)
        self.show_page(page)

    # ---------- 文字 ----------
    def retranslate(self, *_) -> None:
        self.setWindowTitle(t("settings.title"))
        for i, key in enumerate(TAB_KEYS):
            self.tabs.setTabText(i, t(key))
        self.box_primary.setTitle(t("settings.primary"))
        self.box_backup.setTitle(t("settings.backup"))
        self.tencent.setText(t("settings.tencent"))
        self.lbl_domain.setText(t("settings.domain"))
        self.domain.setPlaceholderText(t("settings.domain_placeholder"))
        self.lbl_hotkey.setText(t("settings.hotkey_label"))
        self.btn_hotkey_default.setText(t("settings.hotkey_default"))
        self.lbl_language.setText(t("settings.language"))
        self.autostart.setText(t("settings.autostart"))
        self.autostart_trial.setText(t("settings.autostart_trial"))
        self.lbl_history.setText(t("settings.history_limit"))
        self.history_images.setText(t("settings.history_save_images"))
        self.history_images.setToolTip(t("settings.history_save_images_tip"))
        self.btn_open_data.setText(t("settings.open_data"))
        for field, name, tip, *_ in ADVANCED:
            self.adv_labels[field].setText(t(name))
            self.adv_tips[field].setText(t(tip))
        self.btn_adv_default.setText(t("settings.adv.reset"))
        self.btn_ok.setText(t("settings.ok"))
        self.btn_cancel.setText(t("settings.cancel"))
        self.btn_apply.setText(t("settings.apply"))

    # ---------- 读写 ----------
    def show_page(self, page: str | None) -> None:
        self.tabs.setCurrentIndex(PAGES.index(page) if page in PAGES else 0)

    def load_from(self, c: Config) -> None:
        self.primary.set_service(c.primary)
        self.backup.set_service(c.backup)
        self.tencent.setChecked(c.tencent_enabled)
        self.domain.setText(c.domain_hint)
        self._loaded_hotkey = c.hotkey
        self.hotkey_edit.set_spec(c.hotkey)
        self.language.setCurrentIndex(max(0, self.language.findData(c.language)))
        self.autostart.setChecked(c.autostart if c.autostart is not None else bool(self._autostart_state()))
        self.history_limit.setValue(c.history_limit)
        self.history_images.setChecked(c.history_save_images)
        for field, *_ in ADVANCED:
            self.adv[field].setValue(getattr(c, field))

    def _reset_advanced(self) -> None:
        d = Config()
        for field, *_ in ADVANCED:
            self.adv[field].setValue(getattr(d, field))

    def collect(self) -> Config:
        values = {field: self.adv[field].value() for field, *_ in ADVANCED}
        return dataclasses.replace(
            self._app.config, language=self.language.currentData(), autostart=self.autostart.isChecked(),
            primary=self.primary.service(),
            backup=ServiceConfig() if self.backup.is_empty() else self.backup.service(),
            hotkey=self.hotkey_edit.spec(), domain_hint=self.domain.text().strip(),
            tencent_enabled=self.tencent.isChecked(), history_limit=self.history_limit.value(),
            history_save_images=self.history_images.isChecked(), **values)

    def apply(self) -> bool:
        self.error_label.setText("")
        bad: list[str] = []
        if self.primary.validate():
            bad.append("service")
        if self.backup.validate():
            bad.append("service")
        hk = self.hotkey_edit
        # 被占用只在用户改过快捷键时才挡：启动时就被占、用户没动，照常保存（apply 会重新试注册）。
        # 写错、空着由下面的 config.validate 报（[hotkey] capture → 快捷键页），这里不重复查
        if hk.taken and hk.spec() != self._loaded_hotkey:
            bad.append("hotkey")
        new = self.collect()
        bad += [_page_of(e.field) for e in config.validate(new)]
        glossary_dirty = self.glossary.is_dirty()
        if glossary_dirty and has_blocking(self.glossary.check()):
            bad.append("glossary")
        if bad:
            self.show_page(bad[0])
            self.error_label.setText(t("settings.fix_errors"))
            return False
        # 先存术语表、再存配置（计划定的顺序）；配置那步失败时术语表已经改了，提示里如实说明
        if glossary_dirty:
            try:
                self.glossary.path.parent.mkdir(parents=True, exist_ok=True)  # 数据目录被删了：save_doc 要在里面写 .tmp
                ok = self.glossary.save()
            except OSError as e:  # 没权限、被占用、磁盘满：不崩、不算保存过，配置也不写
                self.show_page("glossary")
                self._warn(t("settings.glossary_save_failed", error=e))
                return False
            if not ok:  # 只剩警告、用户选了不保存
                self.show_page("glossary")
                return False
        try:
            failed = self._app.save_config(new)
        except ConfigError as e:
            if glossary_dirty:
                self._warn(t("settings.save_failed_glossary_saved", error=e))
            else:
                self._warn(t("settings.save_failed", error=e))
            return False
        self._loaded_hotkey = new.hotkey  # 已存进配置：之后再按「应用」不算「改过」
        if failed:
            self.error_label.setText(t("settings.partly_applied", items=t("list.sep").join(failed)))
        return True

    def accept(self) -> None:
        if self.apply():
            self.close()

    def closeEvent(self, e) -> None:
        self.primary.shutdown()
        self.backup.shutdown()
        super().closeEvent(e)

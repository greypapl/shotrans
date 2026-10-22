"""翻译服务的一组控件：预设、地址、API key（默认遮掩，👁 切换明文）、模型（可手填，可获取列表）、关闭思考、
去注册 / 费用说明、测试连接。设置窗口的主服务/备用服务、首次运行引导共用。
网络在工作线程：线程只往 queue 里放 (种类, 代际, 结果)，主线程定时器取；改了任何一项，代际加一，旧结果作废。"""
from __future__ import annotations

import queue
import threading
import webbrowser
from typing import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QToolButton, QWidget)

from .. import i18n, services
from ..config import ServiceConfig, api_key_ok
from ..i18n import N_, t

RED = "border:1px solid #ef4444"
POLL_MS = 30
CONN_KEYS = {"auth": N_("service.conn.auth"), "unreachable": N_("service.conn.unreachable"),
             "model_not_found": N_("service.conn.model_not_found"), "timeout": N_("service.conn.timeout"),
             "bad_format": N_("service.conn.bad_format"), "http": N_("service.conn.http")}


def _test_worker(tester, svc, gen: int, inbox: queue.Queue) -> None:
    try:
        r = tester(svc)
    except Exception as e:  # 测试函数自己出错也要交回一个结果，不能让按钮一直灰着
        r = services.ConnResult("unreachable", 0.0, repr(e))
    inbox.put(("test", gen, r))


def _models_worker(lister, base_url: str, key: str, gen: int, inbox: queue.Queue) -> None:
    try:
        r = lister(base_url, key)
    except Exception as e:
        r = services.ModelsResult("unreachable", (), repr(e))
    inbox.put(("models", gen, r))


class ServiceForm(QWidget):
    tested = Signal(bool)
    edited = Signal()

    def __init__(self, allow_empty: bool, tester: Callable | None = None, lister: Callable | None = None,
                 open_url: Callable[[str], object] | None = None, parent=None):
        super().__init__(parent)
        self.allow_empty = allow_empty
        self._tester = tester or services.test_connection
        self._lister = lister or services.list_models
        self._open_url = open_url or webbrowser.open
        self._custom_style = "chat_template_kwargs"
        self._test_gen = 0
        self._models_gen = 0
        self._loading = False
        # 读入的 key 解不开（换了电脑/用户）且用户还没动过 key 框：保存时原样写回原密文，不把它清空
        self._unreadable = False
        self._kept_enc = ""
        self._thread: threading.Thread | None = None
        self.test_ok = False
        self._inbox: queue.Queue = queue.Queue()

        self.preset = QComboBox(self)
        for p in services.PRESETS:
            self.preset.addItem(p.name(i18n.language()), p.id)
        self.base_url = QLineEdit(self)
        self.api_key = QLineEdit(self)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.eye = QToolButton(self)
        self.eye.setText("👁")
        self.eye.setCheckable(True)
        self.eye.toggled.connect(lambda on: self.api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        self.btn_clear_key = QToolButton(self)  # 只在 key 解不开时显示
        self.btn_clear_key.clicked.connect(self.clear_key)
        self.model = QComboBox(self)
        self.model.setEditable(True)
        self.btn_models = QPushButton(self)
        self.btn_models.clicked.connect(self.fetch_models)
        self.thinking = QCheckBox(self)
        self.signup = QPushButton(self)
        self.signup.clicked.connect(lambda: self._open_url(services.preset(self.preset_id()).signup_url))
        self.cost = QLabel(self)
        self.cost.setWordWrap(True)
        self.btn_test = QPushButton(self)
        self.btn_test.clicked.connect(self.start_test)
        self.result = QLabel(self)
        self.result.setWordWrap(True)

        self._labels = {k: QLabel(self) for k in ("preset", "base_url", "api_key", "model")}
        form = QFormLayout(self)
        form.addRow(self._labels["preset"], self.preset)
        form.addRow(self._labels["base_url"], self.base_url)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(self.eye)
        key_row.addWidget(self.btn_clear_key)
        form.addRow(self._labels["api_key"], key_row)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.btn_models)
        form.addRow(self._labels["model"], model_row)
        form.addRow("", self.thinking)
        info = QHBoxLayout()
        info.addWidget(self.signup)
        info.addWidget(self.cost, 1)
        form.addRow("", info)
        test_row = QHBoxLayout()
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.result, 1)
        form.addRow("", test_row)

        self.preset.currentIndexChanged.connect(self._on_preset)
        self.base_url.textChanged.connect(self._on_edit)
        self.api_key.textChanged.connect(self._on_key_edit)
        self.model.editTextChanged.connect(self._on_edit)
        self.thinking.toggled.connect(self._on_edit)
        self._pump = QTimer(self)
        self._pump.setInterval(POLL_MS)
        self._pump.timeout.connect(self._drain)
        self._pump.start()
        i18n.hub().language_changed.connect(self.retranslate)
        self.retranslate()
        self.set_service(ServiceConfig())  # 新建时是「自定义」且全空：备用服务默认留空；引导自己再选预设

    # ---------- 文字 ----------
    def retranslate(self, *_) -> None:
        lang = i18n.language()
        for i, p in enumerate(services.PRESETS):
            self.preset.setItemText(i, p.name(lang))
        self._labels["preset"].setText(t("service.preset"))
        self._labels["base_url"].setText(t("service.base_url"))
        self._labels["api_key"].setText(t("service.api_key"))
        self._labels["model"].setText(t("service.model"))
        self.base_url.setPlaceholderText(t("service.base_url_placeholder"))
        self.api_key.setPlaceholderText(t("service.key_unreadable") if self._unreadable
                                        else t("service.key_placeholder"))
        self.eye.setToolTip(t("service.eye_tip"))
        self.btn_clear_key.setText(t("service.clear_key"))
        self.btn_clear_key.setToolTip(t("service.clear_key_tip"))
        self.model.lineEdit().setPlaceholderText(t("service.model_placeholder"))
        self.btn_models.setText(t("service.fetch_models"))
        self.thinking.setText(t("service.thinking"))
        self.signup.setText(t("service.signup"))
        self.btn_test.setText(t("service.test"))
        self._update_preset_info()

    # ---------- 读写 ----------
    def preset_id(self) -> str:
        return self.preset.currentData()

    def set_service(self, svc: ServiceConfig) -> None:
        self._loading = True
        try:
            self.preset.setCurrentIndex(self.preset.findData(services.preset(svc.preset).id))
            self.base_url.setText(svc.base_url)
            self.api_key.setText(svc.api_key)
            self.model.setEditText(svc.model)
            self.thinking.setChecked(svc.thinking_off)
            self._custom_style = svc.thinking_style
        finally:
            self._loading = False
        self._set_unreadable(svc.key_unreadable, svc.key_enc)
        self._update_preset_info()
        self._reset_test()

    def service(self) -> ServiceConfig:
        p = services.preset(self.preset_id())
        key = self.api_key.text().strip()
        keep = self._unreadable and not key  # 没动过解不开的 key：带上原密文，config 写盘时原样写回
        return ServiceConfig(preset=p.id, base_url=services.normalize_base_url(self.base_url.text()),
                             api_key=key, model=self.model.currentText().strip(),
                             thinking_off=self.thinking.isChecked(),
                             thinking_style=self._custom_style if p.id == "custom" else p.thinking_style,
                             key_unreadable=keep, key_enc=self._kept_enc if keep else "")

    def is_empty(self) -> bool:
        return not self.base_url.text().strip() and not self.api_key.text().strip()

    def validate(self) -> list[str]:
        url = services.normalize_base_url(self.base_url.text())
        errs = []
        if not url:
            if not self.allow_empty:
                errs.append("required")
        elif not url.lower().startswith(("http://", "https://")):
            errs.append("url")
        self.base_url.setStyleSheet(RED if errs else "")
        key_bad = not api_key_ok(self.api_key.text())
        if key_bad:
            errs.append("key")
        self.api_key.setStyleSheet(RED if key_bad else "")
        if key_bad:
            self.result.setText(t("service.key_bad"))
            self.result.setStyleSheet("color:#ef4444")
        return errs

    # ---------- 交互 ----------
    def _on_preset(self, _idx) -> None:
        if self._loading:
            return
        p = services.preset(self.preset_id())
        if p.id != "custom":
            self._loading = True
            try:
                self.base_url.setText(p.base_url)
                self.model.setEditText(p.model)
                self.thinking.setChecked(p.thinking_off_default)
            finally:
                self._loading = False
        self._update_preset_info()
        self._on_edit()

    def _update_preset_info(self) -> None:
        p = services.preset(self.preset_id())
        self.signup.setVisible(bool(p.signup_url))
        self.cost.setText(p.cost(i18n.language()))
        self.cost.setVisible(bool(p.cost_zh))

    def _on_key_edit(self, *_) -> None:
        if self._loading:
            return
        if self._unreadable:  # 用户动了 key 框（重填或清空）：原密文作废，清空就是真删掉
            self._set_unreadable(False)
        self._on_edit()

    def clear_key(self) -> None:
        """「清除」按钮：作废解不开的原密文，保存时这把 key 真被删掉。"""
        self._set_unreadable(False)
        self._on_edit()

    def _set_unreadable(self, on: bool, enc: str = "") -> None:
        """key 解不开的状态：红框、说明占位文字、「清除」按钮，三者跟着这一个标志走。"""
        self._unreadable = on
        self._kept_enc = enc if on else ""
        self.api_key.setStyleSheet(RED if on else "")
        self.api_key.setPlaceholderText(t("service.key_unreadable") if on else t("service.key_placeholder"))
        self.btn_clear_key.setVisible(on)

    def _on_edit(self, *_) -> None:
        if self._loading:
            return
        self.base_url.setStyleSheet("")
        self.api_key.setStyleSheet(RED if self._unreadable else "")  # 只改别的项：解不开的红框还留着
        self._reset_test()
        self.edited.emit()

    def _reset_test(self) -> None:
        self._test_gen += 1
        self._models_gen += 1
        self.test_ok = False
        self.result.setText("")
        self.result.setStyleSheet("")
        self.btn_test.setEnabled(True)
        self.btn_models.setEnabled(True)

    def start_test(self) -> bool:
        if self.validate():
            return False
        svc = self.service()
        if not svc.configured:
            return False  # 备用服务留空：没什么可测
        if not svc.model:  # 不替用户随便挑一个（服务端第一个可能是嵌入模型）：请他自己填或从列表选
            self._test_gen += 1
            self.test_ok = False
            self.result.setText(t("service.need_model"))
            self.result.setStyleSheet("color:#ef4444")
            return False
        self._test_gen += 1
        self.test_ok = False
        self.result.setText(t("service.testing"))
        self.result.setStyleSheet("")
        self.btn_test.setEnabled(False)
        self._thread = threading.Thread(target=_test_worker, args=(self._tester, svc, self._test_gen, self._inbox),
                                        daemon=True, name="shotrans-conntest")
        self._thread.start()
        return True

    def fetch_models(self) -> bool:
        if self.validate():
            return False
        svc = self.service()
        if not svc.configured:
            return False
        self._models_gen += 1
        self.btn_models.setEnabled(False)
        self.result.setText(t("service.fetching"))
        self.result.setStyleSheet("")
        self._thread = threading.Thread(target=_models_worker,
                                        args=(self._lister, svc.base_url, svc.api_key, self._models_gen, self._inbox),
                                        daemon=True, name="shotrans-models")
        self._thread.start()
        return True

    def _drain(self) -> None:
        while True:
            try:
                kind, gen, r = self._inbox.get_nowait()
            except queue.Empty:
                return
            if kind == "test" and gen == self._test_gen:
                self._on_test(r)
            elif kind == "models" and gen == self._models_gen:
                self._on_models(r)

    def _fail_text(self, r) -> str:
        return t("service.conn.fail", reason=t(CONN_KEYS.get(r.kind, N_("service.conn.http"))), detail=r.detail)

    def _on_test(self, r) -> None:
        self.btn_test.setEnabled(True)
        self.test_ok = r.kind == "ok"
        if self.test_ok:
            self.result.setText(t("service.conn.ok", secs=f"{r.seconds:.1f}", sample=r.sample))
            self.result.setStyleSheet("color:#16a34a")
        else:
            self.result.setText(self._fail_text(r))
            self.result.setStyleSheet("color:#ef4444")
        self.tested.emit(self.test_ok)

    def _on_models(self, r) -> None:
        self.btn_models.setEnabled(True)
        if r.kind != "ok":
            self.result.setText(self._fail_text(r))
            self.result.setStyleSheet("color:#ef4444")
            return
        cur = self.model.currentText()
        self._loading = True
        try:
            self.model.clear()
            self.model.addItems(list(r.models))
            self.model.setEditText(cur or r.models[0])
        finally:
            self._loading = False
        self.result.setText(t("service.models_ok", n=len(r.models)))
        self.result.setStyleSheet("")

    def shutdown(self) -> None:
        """窗口关闭时调用：作废在途结果、停定时器（不等线程）。"""
        self._test_gen += 1
        self._models_gen += 1
        self._pump.stop()

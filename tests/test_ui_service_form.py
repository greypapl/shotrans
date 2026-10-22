import threading

import pytest
from PySide6.QtWidgets import QLineEdit

from shotrans import i18n
from shotrans.config import ServiceConfig
from shotrans.services import ConnResult, ModelsResult
from shotrans.ui.service_form import ServiceForm


@pytest.fixture
def make(qtbot):
    def _make(allow_empty=False, tester=None, lister=None):
        calls = {"test": [], "list": [], "urls": []}

        def t_(svc):
            calls["test"].append(svc)
            return tester(svc) if tester else ConnResult("ok", 1.234, "", "你好", svc.model)

        def l_(url, key):
            calls["list"].append((url, key))
            return lister(url, key) if lister else ModelsResult("ok", ("m-a", "m-b"))

        f = ServiceForm(allow_empty, tester=t_, lister=l_, open_url=calls["urls"].append)
        qtbot.addWidget(f)
        f.calls = calls
        return f
    return _make


def _pick(f, pid):
    f.preset.setCurrentIndex(f.preset.findData(pid))


def test_preset_fills_fields(make):
    f = make()
    _pick(f, "bailian")
    assert f.base_url.text() == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert f.model.currentText() == "qwen-flash" and f.thinking.isChecked() is False
    _pick(f, "deepseek")
    assert f.thinking.isChecked() is True


def test_custom_keeps_fields(make):
    f = make()
    f.set_service(ServiceConfig(preset="deepseek", base_url="http://mine/v1", api_key="k", model="x"))
    _pick(f, "custom")
    assert f.base_url.text() == "http://mine/v1" and f.model.currentText() == "x"


def test_custom_keeps_loaded_thinking_style(make):
    f = make()
    f.set_service(ServiceConfig(preset="custom", base_url="http://mine/v1", thinking_off=True,
                                thinking_style="enable_thinking"))
    assert f.service().thinking_style == "enable_thinking"


def test_service_form_strips_pasted_whitespace(make):
    f = make()
    _pick(f, "custom")
    f.base_url.setText("  https://api.example.com/v1/chat/completions \n")
    f.api_key.setText(" sk-x \n")
    f.model.setEditText(" m1 ")
    s = f.service()
    assert (s.base_url, s.api_key, s.model) == ("https://api.example.com/v1", "sk-x", "m1")


def test_round_trip_service(make):
    f = make()
    svc = ServiceConfig(preset="deepseek", base_url="https://api.deepseek.com", api_key="sk-1", model="deepseek-flash",
                        thinking_off=True, thinking_style="deepseek")
    f.set_service(svc)
    assert f.service() == svc


def test_key_hidden_and_eye_toggles(make):
    f = make()
    assert f.api_key.echoMode() == QLineEdit.EchoMode.Password
    f.eye.click()
    assert f.api_key.echoMode() == QLineEdit.EchoMode.Normal


def test_signup_opens_preset_page_and_hidden_for_custom(make):
    f = make()
    _pick(f, "deepseek")
    f.signup.click()
    assert f.calls["urls"] == ["https://platform.deepseek.com/api_keys"] and f.cost.text()
    _pick(f, "custom")
    assert f.signup.isHidden()


def test_validate_required_and_url(make):
    f = make()
    _pick(f, "custom")
    f.base_url.setText("")
    assert f.validate() == ["required"] and "#ef4444" in f.base_url.styleSheet()
    f.base_url.setText("api.example.com/v1")
    assert f.validate() == ["url"]
    f.base_url.setText("https://api.example.com/v1")
    assert f.validate() == [] and f.base_url.styleSheet() == ""


def test_empty_allowed_for_backup(make):
    f = make(allow_empty=True)
    assert f.is_empty() and f.validate() == []
    assert f.start_test() is False and f.calls["test"] == []


def test_test_ok_shows_seconds_and_sample(qtbot, make):
    f = make()
    _pick(f, "deepseek")
    f.api_key.setText("sk-1")
    got = []
    f.tested.connect(got.append)
    assert f.start_test() is True
    qtbot.waitUntil(lambda: f.test_ok, timeout=3000)
    assert f.result.text() == "✅ 1.2 秒：你好" and got == [True]
    assert f.calls["test"][0].api_key == "sk-1"


@pytest.mark.parametrize("kind,reason", [("auth", "API key 无效"), ("unreachable", "地址连不上"),
                                         ("model_not_found", "模型名不存在"), ("timeout", "超时"),
                                         ("bad_format", "返回格式不对"), ("http", "服务返回错误")])
def test_test_failure_reason(qtbot, make, kind, reason):
    f = make(tester=lambda svc: ConnResult(kind, 0.5, "HTTP 401: x"))
    _pick(f, "deepseek")
    f.start_test()
    qtbot.waitUntil(lambda: f.result.text().startswith("❌"), timeout=3000)
    assert reason in f.result.text() and f.test_ok is False


def test_invalid_url_not_tested(make):
    f = make()
    _pick(f, "custom")
    f.base_url.setText("nope")
    assert f.start_test() is False and f.calls["test"] == []


def test_edit_after_start_drops_old_result(qtbot, make):
    gate = threading.Event()
    f = make(tester=lambda svc: gate.wait(5) and ConnResult("ok", 1.0, "", "好", "m"))
    _pick(f, "deepseek")
    f.start_test()
    th = f._thread
    f.api_key.setText("另一个")
    gate.set()
    th.join(5)
    qtbot.wait(100)
    assert f.test_ok is False and not f.result.text().startswith("✅")


def test_edit_after_ok_resets(qtbot, make):
    f = make()
    _pick(f, "deepseek")
    f.start_test()
    qtbot.waitUntil(lambda: f.test_ok, timeout=3000)
    got = []
    f.edited.connect(lambda: got.append(1))
    f.model.setEditText("other")
    assert f.test_ok is False and f.result.text() == "" and got == [1]


def test_service_form_late_result_after_shutdown_ignored(qtbot, make):
    gate = threading.Event()
    f = make(tester=lambda svc: gate.wait(5) and ConnResult("ok", 1.0, "", "好", "m"))
    _pick(f, "deepseek")
    f.start_test()
    th = f._thread
    f.shutdown()
    gate.set()
    th.join(5)
    qtbot.wait(100)
    assert f.test_ok is False


def test_fetch_models_fills_combo_keeps_text(qtbot, make):
    f = make()
    _pick(f, "deepseek")
    f.api_key.setText("sk-1")
    assert f.fetch_models() is True
    qtbot.waitUntil(lambda: f.model.count() == 2, timeout=3000)
    assert [f.model.itemText(i) for i in range(2)] == ["m-a", "m-b"]
    assert f.model.currentText() == "deepseek-flash"
    assert f.calls["list"] == [("https://api.deepseek.com", "sk-1")]


def test_fetch_models_failure(qtbot, make):
    f = make(lister=lambda u, k: ModelsResult("auth", (), "HTTP 401"))
    _pick(f, "deepseek")
    f.fetch_models()
    qtbot.waitUntil(lambda: f.result.text().startswith("❌"), timeout=3000)
    assert "API key 无效" in f.result.text()


def test_unreadable_key_placeholder(make):
    f = make()
    f.set_service(ServiceConfig(base_url="http://x/v1", key_unreadable=True))
    assert f.api_key.placeholderText() == "原来的 key 解不开（换了电脑或用户）：重新填写；不填则保留原密钥；要删除请点「清除」"


def test_language_switch(make):
    f = make()
    i18n.set_language("en")
    assert f.preset.itemText(f.preset.findData("custom")) == "Custom" and f.btn_test.text() == "Test connection"


# ---------- 以下为本任务在计划用例之外补的：解不开的 key、空模型 ----------
UNREADABLE = ServiceConfig(preset="custom", base_url="http://x/v1", key_unreadable=True, key_enc="CIPHER")


def test_unreadable_key_untouched_keeps_ciphertext(make):
    f = make()
    f.set_service(UNREADABLE)
    f.base_url.setText("http://y/v1")  # 改别的项不算动了 key
    s = f.service()
    assert (s.api_key, s.key_unreadable, s.key_enc) == ("", True, "CIPHER")


def test_unreadable_key_cleared_by_user_drops_ciphertext(make):
    f = make()
    f.set_service(UNREADABLE)
    f.api_key.setText("x")
    f.api_key.setText("")
    s = f.service()
    assert (s.api_key, s.key_unreadable, s.key_enc) == ("", False, "")
    assert f.api_key.placeholderText() == "粘贴你的 API key"


def test_unreadable_key_replaced_by_new_key(make):
    f = make()
    f.set_service(UNREADABLE)
    f.api_key.setText("sk-new")
    s = f.service()
    assert (s.api_key, s.key_unreadable, s.key_enc) == ("sk-new", False, "")


def test_set_service_again_forgets_old_ciphertext(make):
    f = make()
    f.set_service(UNREADABLE)
    f.set_service(ServiceConfig(preset="custom", base_url="http://x/v1"))
    s = f.service()
    assert (s.key_unreadable, s.key_enc) == (False, "")


def test_unreadable_placeholder_survives_language_switch(make):
    f = make()
    f.set_service(UNREADABLE)
    i18n.set_language("en")
    assert f.api_key.placeholderText() == ("The saved key cannot be decrypted (another PC or user): enter it again; "
                                         "leave empty to keep the old key; click 'Clear' to delete it")


def test_empty_model_asks_to_choose_instead_of_testing(make):
    f = make()
    _pick(f, "deepseek")
    f.model.setEditText("  ")
    assert f.start_test() is False and f.calls["test"] == []
    assert f.result.text() == "请先填写模型，或点「获取模型列表」选一个" and f.test_ok is False

# ---------- 评审修复（Minor 3/4/6） ----------
def test_unreadable_red_border_survives_other_edits(make):
    f = make()
    f.set_service(UNREADABLE)
    f.base_url.setText("http://y/v1")
    assert "#ef4444" in f.api_key.styleSheet()


def test_clear_button_only_when_unreadable(make):
    f = make()
    f.set_service(ServiceConfig(preset="custom", base_url="http://x/v1", api_key="sk"))
    assert f.btn_clear_key.isHidden()
    f.set_service(UNREADABLE)
    assert not f.btn_clear_key.isHidden() and f.btn_clear_key.text() == "清除"


def test_clear_button_deletes_key(make):
    f = make()
    f.set_service(UNREADABLE)
    got = []
    f.edited.connect(lambda: got.append(1))
    f.btn_clear_key.click()
    s = f.service()
    assert (s.api_key, s.key_unreadable, s.key_enc) == ("", False, "")
    assert f.btn_clear_key.isHidden() and f.api_key.styleSheet() == "" and got == [1]
    assert f.api_key.placeholderText() == "粘贴你的 API key"


def test_fetch_models_shows_fetching(qtbot, make):
    gate = threading.Event()
    f = make(lister=lambda u, k: gate.wait(5) and ModelsResult("ok", ("m",)))
    _pick(f, "deepseek")
    assert f.fetch_models() is True
    assert f.result.text() == "获取中…"
    gate.set()
    qtbot.waitUntil(lambda: f.result.text() == "获取到 1 个模型", timeout=3000)


def test_model_placeholder_matches_need_model(make):
    f = make()
    assert f.model.lineEdit().placeholderText() == "填模型名，或点「获取模型列表」选一个"
    i18n.set_language("en")
    assert f.model.lineEdit().placeholderText() == "Enter a model name, or click 'Get model list' to pick one"

@pytest.mark.parametrize("key", ["sk-中文SECRET1234", "sk-abc\u200bSECRET1234", "sk-ab cd"])
def test_validate_marks_bad_key_red_and_blocks_test(make, key):
    f = make()
    f.set_service(ServiceConfig(preset="custom", base_url="https://api.example.com/v1", model="m"))
    f.api_key.setText(key)
    assert f.validate() == ["key"] and "#ef4444" in f.api_key.styleSheet()
    assert f.result.text() == "key 里有空格、中文或看不见的字符，请重新复制"
    assert "SECRET1234" not in f.result.text()
    assert f.start_test() is False and f.calls["test"] == []
    f.api_key.setText("sk-ok")
    assert f.validate() == [] and f.api_key.styleSheet() == ""

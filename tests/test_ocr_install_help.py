"""OCR 语言包在「设置」里下载失败（拒绝访问 0x80070005 等）时的备用办法：引导页给出管理员 PowerShell 命令（只列没装的、
可一键复制）和「拒绝访问」的处理步骤；自检的提示里也带上命令。"""
import pytest

from shotrans import i18n
from shotrans.doctor import check_ocr_lang, ocr_install_command
from shotrans.services import ModelsResult
from shotrans.ui.wizard import Wizard
from tests.ui_fakes import FakeApp

EN_CMD = 'Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"'
ZH_CMD = 'Add-WindowsCapability -Online -Name "Language.OCR~~~zh-CN~0.0.1.0"'


def test_command_names_match_windows_capabilities():
    # 中文组件叫 zh-CN（不是 OCR 引擎报的 zh-Hans-CN），写错了用户照抄会报「找不到」
    assert ocr_install_command(["en-US", "zh-Hans-CN"]) == f"{EN_CMD}\n{ZH_CMD}"
    assert ocr_install_command(["zh-Hans-CN"]) == ZH_CMD
    assert ocr_install_command([]) == ""


def test_doctor_missing_message_has_command(monkeypatch):
    monkeypatch.setattr("shotrans.ocr.available_languages", lambda: ["zh-Hans-CN"])
    ok, msg = check_ocr_lang("en-US")
    assert not ok and EN_CMD in msg and ZH_CMD not in msg and "拒绝访问" in msg


class Clip:
    def __init__(self):
        self.text = None

    def setText(self, s):
        self.text = s


@pytest.fixture
def make(qtbot, tmp_path, track):
    def _make(langs):
        state = {"langs": list(langs)}
        clip = Clip()
        w = Wizard(FakeApp(tmp_path), languages_fn=lambda: list(state["langs"]),
                   lister=lambda u, k: ModelsResult("ok", ("m",)), open_url=lambda u: None, warn=lambda s: None,
                   clipboard=clip)
        track(w)
        w.show()
        w.next()  # → 环境检查
        return w, state, clip
    return _make


def test_both_missing_lists_both_and_copies(make):
    w, _, clip = make(())
    assert w.cmd_box.isVisible() and w.cmd_text.toPlainText() == f"{EN_CMD}\n{ZH_CMD}"
    assert "0x80070005" in w.env_cmd_note.text() and "拒绝访问" in w.env_denied.text()
    w.btn_copy_cmd.click()
    assert clip.text == f"{EN_CMD}\n{ZH_CMD}" and w.btn_copy_cmd.text() == "已复制"


def test_only_missing_one_listed_and_hidden_when_all_installed(make):
    w, state, clip = make(("zh-Hans-CN",))
    assert w.cmd_text.toPlainText() == EN_CMD
    w.btn_copy_cmd.click()
    state["langs"] = ["zh-Hans-CN", "en-US"]
    w.btn_recheck.click()
    assert not w.cmd_box.isVisible()


def test_recheck_after_partial_install_updates_command(make):
    w, state, clip = make(())
    w.btn_copy_cmd.click()
    state["langs"] = ["zh-Hans-CN"]
    w.btn_recheck.click()
    assert w.cmd_text.toPlainText() == EN_CMD and w.btn_copy_cmd.text() == "复制命令"


def test_english_ui(make):
    w, _, _ = make(())
    i18n.set_language("en")
    assert w.btn_copy_cmd.text() == "Copy command" and "Run as administrator" in w.env_cmd_note.text()
    assert w.cmd_text.toPlainText() == f"{EN_CMD}\n{ZH_CMD}"

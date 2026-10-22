"""启动时 key 解不开（换了电脑/用户）：气泡提示重新填写，不弹 key 本身。"""
import pytest

from shotrans import autostart
from shotrans.app import ShotTransApp
from shotrans.hotkey import GlobalHotkey
from shotrans.ui.notice import NoticePopup


class QuietRouter:
    glossary = None

    def vllm_down(self):
        return False


@pytest.fixture
def messages(monkeypatch):
    got = []
    monkeypatch.setattr(NoticePopup, "show_notice", lambda self, title, msg, *a, **k: got.append(msg))
    monkeypatch.setattr(autostart, "is_enabled", lambda folder=None: False)
    monkeypatch.setattr(GlobalHotkey, "register", lambda self: True)
    return got


def _app(qapp, tmp_path, toml):
    (tmp_path / "config.toml").write_text(toml, encoding="utf-8")
    return ShotTransApp(qapp, tmp_path, ocr_fn=lambda img: [], router=QuietRouter())


def test_unreadable_primary_key_notifies(qapp, tmp_path, messages):
    a = _app(qapp, tmp_path, '[primary]\nbase_url = "http://x/v1"\napi_key_enc = "AAAAAAAA"\n'
                             '[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n')
    try:
        assert len(messages) == 1 and "主服务" in messages[0] and "无法解密" in messages[0]
    finally:
        a.shutdown()


def test_unreadable_backup_key_notifies(qapp, tmp_path, messages):
    a = _app(qapp, tmp_path, '[backup]\nbase_url = "http://x/v1"\napi_key_enc = "AAAAAAAA"\n'
                             '[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n')
    try:
        assert len(messages) == 1 and "备用服务" in messages[0] and "无法解密" in messages[0]
    finally:
        a.shutdown()


def test_readable_key_no_notice(qapp, tmp_path, messages):
    a = _app(qapp, tmp_path, '[primary]\nbase_url = "http://x/v1"\napi_key = "k"\n'
                             '[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n')
    try:
        assert messages == []
    finally:
        a.shutdown()


def test_unreadable_key_without_base_url_no_notice(qapp, tmp_path, messages):
    # 备用服务没填地址 = 不用它：残留的解不开的密文不该弹气泡
    a = _app(qapp, tmp_path, '[backup]\nbase_url = ""\napi_key_enc = "AAAAAAAA"\n'
                             '[hotkey]\ncapture = "Ctrl+Alt+Shift+F10"\n')
    try:
        assert messages == []
    finally:
        a.shutdown()
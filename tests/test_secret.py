"""DPAPI：真调本机 API 往返；失败分支用假的 crypt32。"""
import base64

import pytest

from shotrans import secret
from shotrans.secret import SecretError, protect, unprotect


def test_round_trip_ascii():
    enc = protect("sk-abc-123")
    assert "sk-abc-123" not in enc
    assert unprotect(enc) == "sk-abc-123"


def test_round_trip_unicode_and_long():
    k = "密钥-" + "x" * 300
    assert unprotect(protect(k)) == k


def test_output_is_base64():
    base64.b64decode(protect("k"), validate=True)


def test_empty_maps_to_empty():
    assert protect("") == ""
    assert unprotect("") == ""


def test_garbage_blob_raises():
    with pytest.raises(SecretError):
        unprotect(base64.b64encode(b"not a dpapi blob").decode("ascii"))


def test_not_base64_raises():
    with pytest.raises(SecretError):
        unprotect("@@@ 不是 base64")


class _FailCrypt:
    def CryptProtectData(self, *a):
        return 0

    def CryptUnprotectData(self, *a):
        return 0

    def LocalFree(self, p):  # 当 kernel32 用：变异跳过失败判断时，测试要以断言失败变红，而不是 AttributeError
        return None


def test_protect_failure_raises_without_echo(monkeypatch):
    monkeypatch.setattr(secret, "_api", lambda: (_FailCrypt(), _FailCrypt()))
    with pytest.raises(SecretError) as ei:
        protect("sk-never-shown-9999")
    assert "sk-never-shown-9999" not in str(ei.value)


def test_unprotect_failure_raises(monkeypatch):
    monkeypatch.setattr(secret, "_api", lambda: (_FailCrypt(), _FailCrypt()))
    with pytest.raises(SecretError):
        unprotect(base64.b64encode(b"abc").decode("ascii"))

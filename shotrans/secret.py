"""API key 加密：Windows DPAPI（CryptProtectData，当前用户范围），结果 base64。
换了电脑或 Windows 用户就解不开（unprotect 抛 SecretError）。错误信息里绝不带明文。"""
from __future__ import annotations

import base64
import binascii
import ctypes
import sys
from ctypes import wintypes

from .i18n import t

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretError(Exception):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _api():
    """(crypt32, kernel32)。测试里会被换成假的。"""
    if sys.platform != "win32":
        raise SecretError(t("secret.err.platform"))
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pblob = ctypes.POINTER(_Blob)
    crypt32.CryptProtectData.argtypes = [pblob, wintypes.LPCWSTR, pblob, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.DWORD, pblob]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [pblob, ctypes.c_void_p, pblob, ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, pblob]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _in_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf  # buf 要活到调用结束


def _take(out: _Blob, kernel32) -> bytes:
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(plain: str) -> str:
    if plain == "":
        return ""
    crypt32, kernel32 = _api()
    inp, _keep = _in_blob(plain.encode("utf-8"))
    out = _Blob()
    if not crypt32.CryptProtectData(ctypes.byref(inp), "shotrans", None, None, None,
                                    CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise SecretError(t("secret.err.encrypt", code=ctypes.get_last_error()))
    return base64.b64encode(_take(out, kernel32)).decode("ascii")


def unprotect(enc: str) -> str:
    if enc == "":
        return ""
    try:
        raw = base64.b64decode(enc.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as e:
        raise SecretError(t("secret.err.base64")) from e
    crypt32, kernel32 = _api()
    inp, _keep = _in_blob(raw)
    out = _Blob()
    if not crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise SecretError(t("secret.err.decrypt", code=ctypes.get_last_error()))
    try:
        return _take(out, kernel32).decode("utf-8")
    except UnicodeDecodeError as e:
        raise SecretError(t("secret.err.utf8")) from e

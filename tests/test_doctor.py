import ctypes
import sys

from shotrans import doctor
from shotrans.config import load_config
from shotrans.doctor import (build_checks, check_backup, check_fonts, check_hotkey, check_ocr_lang,
                             check_primary, run_checks)
from tests.fakes import sse_chunks

SECRET = "sk-SECRET-1234567890"


def _cfg(tmp_path, toml=""):
    p = tmp_path / "config.local.toml"
    p.write_text(toml, encoding="utf-8")
    return load_config(p)


def _ok_server(make_server):
    return make_server(sse_chunks(["⟦1⟧你好，", "世界。"]))


def test_run_checks_all_ok():
    lines, ok = run_checks([("甲", lambda: (True, "好")), ("乙", lambda: (True, "也好"))])
    assert ok is True
    assert lines == ["✅ 甲：好", "✅ 乙：也好"]


def test_run_checks_one_fails():
    lines, ok = run_checks([("甲", lambda: (True, "好")), ("乙", lambda: (False, "坏"))])
    assert ok is False and lines[1] == "❌ 乙：坏"


def test_run_checks_failure_before_success_still_fails():
    lines, ok = run_checks([("甲", lambda: (False, "坏")), ("乙", lambda: (True, "好"))])
    assert ok is False and lines[0] == "❌ 甲：坏" and lines[1] == "✅ 乙：好"


def test_run_checks_exception_is_failure():
    def boom():
        raise RuntimeError("炸了")

    lines, ok = run_checks([("甲", boom)])
    assert ok is False and lines[0].startswith("❌ 甲：出错")


def test_run_checks_exception_does_not_stop_later_checks():
    def boom():
        raise RuntimeError("炸了")

    lines, ok = run_checks([("甲", boom), ("乙", lambda: (True, "好"))])
    assert len(lines) == 2 and lines[1] == "✅ 乙：好"


def test_build_checks_order(tmp_path):
    assert [n for n, _ in build_checks(tmp_path)] == [
        "OCR 英文", "OCR 中文", "配置文件", "主服务", "备用服务", "腾讯兜底", "快捷键", "字体"]


def test_config_check_reports_missing(tmp_path):
    checks = dict(build_checks(tmp_path))
    ok, msg = checks["配置文件"]()
    assert ok is False and "设置" in msg


def test_config_check_success_and_feeds_later_checks(tmp_path):
    (tmp_path / "config.toml").write_text('[fallback]\nbase_url = ""\n', encoding="utf-8")
    checks = dict(build_checks(tmp_path))
    assert checks["配置文件"]() == (True, "读取成功")
    assert checks["备用服务"]() == (True, "未配置（可以留空）")  # 读到的配置被后面的项用上了


def test_config_check_bad_toml(tmp_path):
    (tmp_path / "config.toml").write_text("这不是 toml = = =", encoding="utf-8")
    ok, msg = dict(build_checks(tmp_path))["配置文件"]()
    assert ok is False and "格式错误" in msg


def test_later_checks_without_config_file(tmp_path):
    checks = dict(build_checks(tmp_path))
    checks["配置文件"]()  # 失败，cfg 没存进去
    assert checks["主服务"]() == (False, "配置没读到，先修好上一项")
    assert checks["备用服务"]() == (False, "配置没读到，先修好上一项")


# ---------- OCR ----------

def test_ocr_ok(monkeypatch):
    monkeypatch.setattr("shotrans.ocr.available_languages", lambda: ["en-US", "zh-Hans-CN", "ja"])
    assert check_ocr_lang("en-US") == (True, "已安装")
    assert check_ocr_lang("zh-Hans-CN") == (True, "已安装")


def test_ocr_missing_chinese_only(monkeypatch):
    monkeypatch.setattr("shotrans.ocr.available_languages", lambda: ["en-US"])
    ok, msg = check_ocr_lang("zh-Hans-CN")
    assert ok is False and "zh-Hans-CN" in msg
    assert check_ocr_lang("en-US")[0] is True


def test_ocr_missing_english_only(monkeypatch):
    monkeypatch.setattr("shotrans.ocr.available_languages", lambda: ["zh-Hans-CN"])
    ok, msg = check_ocr_lang("en-US")
    assert ok is False and "en-US" in msg
    assert check_ocr_lang("zh-Hans-CN")[0] is True


# ---------- vLLM ----------

def test_vllm_check_without_config():
    assert check_primary(None) == (False, "配置没读到，先修好上一项")


def test_vllm_check_without_key(tmp_path):
    # 改动说明：原先没 key 报 ❌；Task 16 起没 key = 不使用 vLLM，是允许的配置
    assert check_primary(_cfg(tmp_path)) == (True, "未配置（不使用主服务）")


def test_vllm_check_success(tmp_path, make_server):
    srv = _ok_server(make_server)
    cfg = _cfg(tmp_path, f'[vllm]\nbase_url = "{srv.url}"\napi_key = "{SECRET}"\n')
    ok, msg = check_primary(cfg)
    assert ok is True and "fake-model" in msg and "你好，世界。" in msg and "秒" in msg
    assert srv.requests[0]["auth"] == f"Bearer {SECRET}"
    assert SECRET not in msg


def test_vllm_check_server_returns_no_text(tmp_path, make_server):
    srv = make_server(sse_chunks([]))
    cfg = _cfg(tmp_path, f'[vllm]\nbase_url = "{srv.url}"\napi_key = "{SECRET}"\n')
    lines, ok = run_checks([("主服务", lambda: check_primary(cfg))])
    assert ok is False and lines[0].startswith("❌ 主服务：出错") and SECRET not in lines[0]


def test_vllm_check_connect_failure_is_error_line(tmp_path):
    from tests.fakes import closed_port_url

    cfg = _cfg(tmp_path, f'[vllm]\nbase_url = "{closed_port_url()}"\napi_key = "{SECRET}"\n'
                         "[timeouts]\nconnect = 0.5\n")
    lines, ok = run_checks([("主服务", lambda: check_primary(cfg))])
    assert ok is False and lines[0].startswith("❌ 主服务：出错") and SECRET not in lines[0]


# ---------- 备用翻译 ----------

def test_fallback_without_config():
    assert check_backup(None) == (False, "配置没读到，先修好上一项")


def test_fallback_not_configured(tmp_path):
    assert check_backup(_cfg(tmp_path)) == (True, "未配置（可以留空）")  # 改动说明：现在还有腾讯一层，「只用 vLLM」不再成立


def test_fallback_configured_with_key(tmp_path, make_server):
    srv = _ok_server(make_server)
    cfg = _cfg(tmp_path, f'[fallback]\nbase_url = "{srv.url}"\napi_key = "{SECRET}"\nmodel = "my-model"\n')
    ok, msg = check_backup(cfg)
    assert ok is True and "my-model" in msg and "你好，世界。" in msg and "没填 key" not in msg
    req = srv.requests[0]
    assert req["auth"] == f"Bearer {SECRET}"
    assert req["json"]["model"] == "my-model"
    assert "chat_template_kwargs" not in req["json"]  # 备用不带 vLLM 专用字段
    assert srv.models_hits == 0  # 指定了 model 就不用去查 /models
    assert SECRET not in msg


def test_fallback_configured_without_key_still_tries(tmp_path, make_server):
    srv = _ok_server(make_server)
    cfg = _cfg(tmp_path, f'[fallback]\nbase_url = "{srv.url}"\n')
    ok, msg = check_backup(cfg)
    assert ok is True and "没填 key" in msg and "fake-model" in msg
    assert srv.requests[0]["auth"] is None


def test_fallback_failure_is_error_line_without_key(tmp_path, make_server):
    srv = make_server(sse_chunks([]))
    cfg = _cfg(tmp_path, f'[fallback]\nbase_url = "{srv.url}"\napi_key = "{SECRET}"\n')
    lines, ok = run_checks([("备用服务", lambda: check_backup(cfg))])
    assert ok is False and lines[0].startswith("❌ 备用服务：出错") and SECRET not in lines[0]


def test_whole_doctor_output_never_contains_keys(tmp_path, make_server, monkeypatch, capsys):
    srv = _ok_server(make_server)
    (tmp_path / "config.toml").write_text(
        f'[vllm]\nbase_url = "{srv.url}"\napi_key = "{SECRET}"\n'
        f'[fallback]\nbase_url = "{srv.url}"\napi_key = "{SECRET}2"\n', encoding="utf-8")
    monkeypatch.setattr("shotrans.ocr.available_languages", lambda: ["en-US", "zh-Hans-CN"])
    doctor.main(["--home", str(tmp_path)])
    out = capsys.readouterr().out
    assert SECRET not in out and "✅ 主服务" in out and "✅ 备用服务" in out


# ---------- 快捷键 / 字体 / main ----------

def test_fonts_present_on_this_machine():
    assert check_fonts()[0] is True


def test_fonts_missing(monkeypatch):
    monkeypatch.setattr(doctor, "FONT_ZH", "C:/不存在/zh.ttc")
    ok, msg = check_fonts()
    assert ok is False and "zh.ttc" in msg and "segoeui" not in msg


def test_fonts_missing_english(monkeypatch):
    monkeypatch.setattr(doctor, "FONT_EN", "C:/不存在/en.ttf")
    ok, msg = check_fonts()
    assert ok is False and "en.ttf" in msg


def test_hotkey_free_combo(qapp):
    class Cfg:
        hotkey = "Ctrl+Alt+Shift+F9"

    ok, msg = check_hotkey(Cfg())
    assert ok is True and "可用" in msg
    assert check_hotkey(Cfg())[0] is True  # 第一次必须把热键还回去，否则第二次会失败


def test_hotkey_default_when_no_config(qapp):
    ok, msg = check_hotkey(None)
    assert "Ctrl+Alt+Q" in msg


def test_hotkey_occupied(qapp):
    class Cfg:
        hotkey = "Ctrl+Alt+Shift+F8"

    user32 = ctypes.windll.user32
    assert user32.RegisterHotKey(None, 0xB0A3, 0x1 | 0x2 | 0x4 | 0x4000, 0x77)  # Alt+Ctrl+Shift+F8
    try:
        ok, msg = check_hotkey(Cfg())
    finally:
        user32.UnregisterHotKey(None, 0xB0A3)
    assert ok is False and "被占用" in msg


def test_hotkey_own_is_ok():
    class Cfg:
        hotkey = "Ctrl+Alt+Shift+F8"

    assert check_hotkey(Cfg(), own="Ctrl+Alt+Shift+F8") == (True, "Ctrl+Alt+Shift+F8 正由截图翻译使用")


def test_hotkey_registers_with_norepeat_and_releases_same_id(monkeypatch):
    calls = []

    class FakeUser32:
        def RegisterHotKey(self, hwnd, hid, mods, vk):
            calls.append(("reg", hid, mods, vk))
            return 1

        def UnregisterHotKey(self, hwnd, hid):
            calls.append(("unreg", hid))
            return 1

    class FakeWindll:
        user32 = FakeUser32()

    class FakeCtypes:
        windll = FakeWindll()

    monkeypatch.setattr(doctor, "ctypes", FakeCtypes())

    class Cfg:
        hotkey = "Ctrl+Alt+Shift+F9"

    assert check_hotkey(Cfg())[0] is True
    # 0x4000 = MOD_NOREPEAT；Alt0x1 | Ctrl0x2 | Shift0x4；F9 = 0x78
    assert calls == [("reg", 0xB0A2, 0x1 | 0x2 | 0x4 | 0x4000, 0x78), ("unreg", 0xB0A2)]


def test_main_exit_codes(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(doctor, "build_checks", lambda home, legacy=None: [("甲", lambda: (True, "好"))])
    assert doctor.main(["--home", str(tmp_path)]) == 0
    assert "全部通过" in capsys.readouterr().out
    monkeypatch.setattr(doctor, "build_checks", lambda home, legacy=None: [("甲", lambda: (False, "坏"))])
    assert doctor.main(["--home", str(tmp_path)]) == 1
    assert "有问题" in capsys.readouterr().out


def test_main_passes_root_to_build_checks(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(doctor, "build_checks", lambda home, legacy=None: seen.append(home) or [])
    doctor.main(["--home", str(tmp_path)])
    assert seen == [tmp_path]


def test_main_without_stdout_reconfigure(monkeypatch, tmp_path):
    import io

    class Plain:
        def __init__(self):
            self.buf = io.StringIO()

        def write(self, s):
            return self.buf.write(s)

        def flush(self):
            pass

    plain = Plain()
    assert not hasattr(plain, "reconfigure")
    monkeypatch.setattr(sys, "stdout", plain)
    monkeypatch.setattr(doctor, "build_checks", lambda home, legacy=None: [("甲", lambda: (True, "好"))])
    assert doctor.main(["--home", str(tmp_path)]) == 0
    assert "全部通过" in plain.buf.getvalue()


def test_build_checks_falls_back_to_legacy_file(tmp_path):
    legacy = tmp_path / "config.local.toml"
    legacy.write_text('[hotkey]\ncapture = "Ctrl+Alt+Shift+F9"\n[tencent]\nenabled = true\n', encoding="utf-8")
    checks = dict(build_checks(tmp_path / "home", legacy))
    assert checks["配置文件"]() == (True, "读取成功")


def test_main_passes_legacy_config_path(monkeypatch, tmp_path):
    """命令行 doctor 把程序目录的旧 config.local.toml 交给 build_checks（新数据目录还没配置时仍可自检）。"""
    monkeypatch.setenv("SHOTRANS_LEGACY_DIR", str(tmp_path / "prog"))
    seen = []
    monkeypatch.setattr(doctor, "build_checks", lambda home, legacy=None: seen.append(legacy) or [])
    doctor.main(["--home", str(tmp_path)])
    assert seen == [tmp_path / "prog" / "config.local.toml"]


def test_build_checks_prefers_config_toml(tmp_path):
    (tmp_path / "config.toml").write_text("[tencent\n", encoding="utf-8")
    legacy = tmp_path / "config.local.toml"
    legacy.write_text("", encoding="utf-8")
    ok, msg = dict(build_checks(tmp_path, legacy))["配置文件"]()
    assert ok is False and "格式错误" in msg

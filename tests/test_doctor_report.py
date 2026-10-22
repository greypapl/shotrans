"""自检报告：key 打码、地址保留、行格式。"""
from shotrans.config import Config, ServiceConfig
from shotrans.doctor import build_items, format_report, mask_key, run_checks, run_one
from tests.fakes import closed_port_url


def test_mask_key():
    assert mask_key("sk-abcdef123456") == "sk-…3456"
    assert mask_key("abcdefgh9876") == "…9876"
    assert mask_key("short") == "…"
    assert mask_key("sk-abc") == "sk-…"
    assert mask_key("") == "（未填）"
    assert mask_key("  ") == "（未填）"


def test_report_masks_keys_keeps_urls():
    cfg = Config(primary=ServiceConfig(preset="deepseek", base_url="https://api.deepseek.com",
                                       api_key="sk-abcdef123456", model="deepseek-flash"),
                 backup=ServiceConfig(base_url="http://10.0.0.2:8000/v1", api_key="plainkey-0000-9999"))
    rows = [("主服务", True, "模型 deepseek-flash，单句 1.2 秒，译文：你好（key sk-abcdef123456）"),
            ("备用服务", False, "出错：plainkey-0000-9999"), ("字体", None, "")]
    text = format_report(rows, cfg, "0.2.0", now=0)
    assert "sk-abcdef123456" not in text and "plainkey-0000-9999" not in text
    assert "sk-…3456" in text and "…9999" in text
    assert "https://api.deepseek.com" in text and "http://10.0.0.2:8000/v1" in text
    assert "✅ 主服务：" in text and "❌ 备用服务：" in text and "⏳ 字体：" in text
    assert "0.2.0" in text.splitlines()[1]


def test_report_without_config():
    text = format_report([("配置文件", False, "找不到")], None, "0.2.0", now=0)
    assert "❌ 配置文件：找不到" in text


def test_items_ids_labels_and_fixes(tmp_path):
    items = build_items(tmp_path)
    assert [(i.id, i.label, i.fix) for i in items] == [
        ("ocr_en", "OCR 英文", "lang_settings"), ("ocr_zh", "OCR 中文", "lang_settings"),
        ("config", "配置文件", "settings:service"), ("primary", "主服务", "settings:service"),
        ("backup", "备用服务", "settings:service"), ("tencent", "腾讯兜底", "settings:service"),
        ("hotkey", "快捷键", "settings:hotkey"), ("fonts", "字体", None)]


def test_own_hotkey_counts_as_ok(tmp_path):
    (tmp_path / "config.toml").write_text('[hotkey]\ncapture = "Ctrl+Alt+Shift+F8"\n', encoding="utf-8")
    items = {i.id: i for i in build_items(tmp_path, own_hotkey="ctrl+alt+shift+f8")}
    items["config"].fn()
    assert items["hotkey"].fn() == (True, "Ctrl+Alt+Shift+F8 正由截图翻译使用")


def test_unreadable_key_is_failure(tmp_path):
    # 地址用本机没人监听的端口：万一这个守卫回归、真的去连，也立刻失败，不碰真网络
    (tmp_path / "config.toml").write_text(f'[primary]\nbase_url = "{closed_port_url()}"\napi_key_enc = "AAAAAAAA"\n'
                                          "[timeouts]\nconnect = 0.5\n", encoding="utf-8")
    items = {i.id: i for i in build_items(tmp_path)}
    items["config"].fn()
    ok, msg = items["primary"].fn()
    assert ok is False and "无法解密" in msg


def test_service_items_depend_on_config(tmp_path):
    deps = {i.id: i.depends_on for i in build_items(tmp_path)}
    assert deps == {"ocr_en": None, "ocr_zh": None, "config": None, "primary": "config", "backup": "config",
                    "tencent": "config", "hotkey": None, "fonts": None}


# ---------- run_one：检查函数返回了怪东西 ----------

def test_run_one_none_result_is_failure():
    ok, msg = run_one(lambda: None)
    assert ok is False and msg.startswith("出错：") and "None" in msg


def test_run_one_unpackable_non_tuple_is_failure():
    ok, msg = run_one(lambda: "ab")  # 两个字符的字符串能被解包成两个值，也不能当成结果
    assert ok is False and "'ab'" in msg


def test_run_one_coerces_values():
    assert run_one(lambda: (None, 5)) == (False, "5")


def test_run_checks_bad_result_is_failure_line():
    lines, ok = run_checks([("甲", lambda: (True, "好", "多"))])  # 三元组：个数不对
    assert ok is False and lines[0].startswith("❌ 甲：出错：")

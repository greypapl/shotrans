"""按配置造主/备客户端（不联网：只构造，model 已给出就不查 /models）。"""
import pytest

from shotrans.app import build_router
from shotrans.config import Config, load_config
from shotrans.glossary import Glossary
from shotrans.translate.factory import service_client

FULL = """
[primary]
preset = "deepseek"
base_url = "https://api.deepseek.com"
api_key = "sk-f-1"
model = "deepseek-flash"
thinking_off = true
thinking_style = "deepseek"
[backup]
base_url = "http://127.0.0.1:9/v1"
model = "fb"
[translate]
domain_hint = "医学"
[timeouts]
connect = 1.5
first_token = 4.5
stall = 12.5
backup_connect = 2.5
backup_first_token = 8.5
backup_stall = 15.5
"""


def _router(tmp_path, toml):
    p = tmp_path / "config.toml"
    p.write_text(toml, encoding="utf-8")
    return build_router(load_config(p), Glossary())


def test_primary_client_from_config(tmp_path):
    r = _router(tmp_path, FULL)
    v = r.vllm
    assert v.name == "vllm" and v.base_url == "https://api.deepseek.com" and v.api_key == "sk-f-1"
    assert v.thinking_off is True and v.thinking_style == "deepseek"
    assert v.model_name() == "deepseek-flash"
    assert (v.connect_timeout, v.first_token_timeout, v.stall_timeout) == (1.5, 4.5, 12.5)
    assert v.domain_hint == "医学"


def test_backup_client_from_config(tmp_path):
    f = _router(tmp_path, FULL).fallback
    assert f.name == "fallback" and f.base_url == "http://127.0.0.1:9/v1" and f.api_key == ""
    assert f.thinking_off is False and f.model_name() == "fb"
    assert (f.connect_timeout, f.first_token_timeout, f.stall_timeout) == (2.5, 8.5, 15.5)


def test_unconfigured_services_are_none(tmp_path):
    r = _router(tmp_path, "")
    assert r.vllm is None and r.fallback is None
    assert r._chain() == []  # 新格式文件没写 [tencent] enabled = 腾讯也关


def test_unconfigured_services_with_tencent_on(tmp_path):
    r = _router(tmp_path, "[tencent]\nenabled = true\n")
    assert r.vllm is None and r.fallback is None
    assert [s.name for s in r._chain()] == ["tencent"]


def test_service_client_unknown_role():
    with pytest.raises(ValueError):
        service_client(Config(), "third")


@pytest.mark.parametrize("url", ["https://api.example.com/v1/chat/completions", "https://api.example.com/v1/models/",
                                 "  https://api.example.com/v1/  "])
def test_hand_edited_full_endpoint_url_normalized(tmp_path, url):
    """手改配置写成完整的接口地址：运行时也和设置界面一样去掉后缀，不会请求 …/chat/completions/chat/completions。"""
    r = _router(tmp_path, f'[primary]\nbase_url = "{url}"\nmodel = "m"\n[backup]\nbase_url = "{url}"\nmodel = "m"\n')
    assert r.vllm.base_url == "https://api.example.com/v1"
    assert r.fallback.base_url == "https://api.example.com/v1"


def test_key_surrounding_spaces_stripped(tmp_path):
    r = _router(tmp_path, '[primary]\nbase_url = "http://x/v1"\napi_key = " sk-k1 "\nmodel = "m"\n')
    assert r.vllm.api_key == "sk-k1"

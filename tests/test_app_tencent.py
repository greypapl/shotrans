"""build_router 的三层接线（只构造客户端，不发请求）。"""
from shotrans.app import build_router
from shotrans.config import load_config
from shotrans.glossary import Glossary
from shotrans.translate.tencent import TencentClient


def _router(tmp_path, toml):
    p = tmp_path / "config.local.toml"
    p.write_text(toml, encoding="utf-8")
    return build_router(load_config(p), Glossary())


def test_no_vllm_key_means_no_vllm(tmp_path):
    r = _router(tmp_path, '[vllm]\napi_key = ""\n')
    assert r.vllm is None
    assert [s.name for s in r._chain()] == ["tencent"]


def test_vllm_key_means_vllm(tmp_path):
    r = _router(tmp_path, '[vllm]\nbase_url = "http://10.0.0.1:9000/v1"\napi_key = "k"\n')
    assert r.vllm is not None and r.vllm.name == "vllm"


def test_tencent_on_when_legacy_file_omits_enabled_with_connect_timeout(tmp_path):
    # 旧格式文件没写 [tencent] enabled：按旧版本的默认（开着）算，见 config.LEGACY_TENCENT_IF_UNSET
    r = _router(tmp_path, '[vllm]\napi_key = ""\n[timeouts]\nconnect = 1.5\n[tencent]\nconnect = 4.5\n')
    assert isinstance(r.tencent, TencentClient)
    assert r.tencent.name == "tencent"
    assert r.tencent.timeout_connect == 4.5  # 用自己的连接超时（含 TLS 握手），不用 vLLM 的
    assert r.tencent.timeout_read == 8.0


def test_v2_file_without_enabled_has_no_tencent(tmp_path):
    r = _router(tmp_path, '[primary]\nbase_url = "http://10.0.0.1:9000/v1"\n[tencent]\nconnect = 4.5\n')
    assert r.tencent is None
    assert [s.name for s in r._chain()] == ["vllm"]


def test_tencent_disabled(tmp_path):
    r = _router(tmp_path, '[vllm]\nbase_url = "http://10.0.0.1:9000/v1"\napi_key = "k"\n[tencent]\nenabled = false\n')
    assert r.tencent is None
    assert [s.name for s in r._chain()] == ["vllm"]


def test_full_chain_order(tmp_path):
    r = _router(tmp_path, '[vllm]\nbase_url = "http://10.0.0.1:9000/v1"\napi_key = "k"\n[fallback]\nbase_url = "http://127.0.0.1:9/v1"\n')
    assert [s.name for s in r._chain()] == ["vllm", "fallback", "tencent"]


def test_nothing_configured_empty_chain(tmp_path):
    r = _router(tmp_path, "[tencent]\nenabled = false\n")
    assert r._chain() == []

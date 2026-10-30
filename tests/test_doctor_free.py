"""自检：微软免费翻译一项，以及「一层翻译服务都没有」的判定。全部走本机假服务器。"""
import pytest

from shotrans import doctor
from shotrans.config import load_config
from shotrans.doctor import build_checks, check_free
from shotrans.translate import microsoft
from tests.fakes import _json, edge_map


ON = "[free]\nenabled = true\n"  # 默认关：要测它就显式打开


def _cfg(tmp_path, toml=""):
    p = tmp_path / "config.local.toml"
    p.write_text(toml, encoding="utf-8")
    return load_config(p)


@pytest.fixture
def fake_tm(make_edge, monkeypatch):
    def _make(handler):
        srv = make_edge(handler)
        monkeypatch.setattr(microsoft, "EDGE_URL", srv.url)
        return srv
    return _make


# ---------- 微软免费翻译 ----------
def test_free_without_config():
    assert check_free(None) == (False, "配置没读到，先修好上一项")


def test_free_disabled(tmp_path):
    assert check_free(_cfg(tmp_path, "[free]\nenabled = false\n")) == (True, "已关闭")


def test_free_disabled_sends_nothing(tmp_path, fake_tm):
    srv = fake_tm(edge_map(lambda t: "你好"))
    check_free(_cfg(tmp_path, "[tencent]\nenabled = false\n"))
    assert srv.requests == []


def test_free_success(tmp_path, fake_tm):
    srv = fake_tm(edge_map(lambda t: "你好，世界。"))
    ok, msg = check_free(_cfg(tmp_path, ON))
    assert ok is True and "你好，世界。" in msg and "秒" in msg
    assert srv.requests[0]["json"] == ["Hello, world."]
    assert (srv.requests[0]["query"]["from"], srv.requests[0]["query"]["to"]) == ("en", "zh-Hans")


def test_free_connect_failure_is_red_with_reason(tmp_path):
    ok, msg = check_free(_cfg(tmp_path, "[free]\nenabled = true\nconnect = 0.5\n"))  # conftest：默认地址连不上
    assert ok is False and "不可用（connect" in msg and "智谱" in msg  # 告诉用户可以改用免费的智谱


def test_free_bad_reply_is_red_with_reason(tmp_path, fake_tm):
    fake_tm(lambda h, p: _json(h, 200, {"error": "x"}))
    ok, msg = check_free(_cfg(tmp_path, ON))
    assert ok is False and "不可用（format" in msg


def test_free_uses_connect_timeout(tmp_path, monkeypatch):
    seen = []

    class Fake:
        name = "microsoft"

        def __init__(self, timeout_connect=2.0, timeout_read=8.0, url=None):
            seen.append(timeout_connect)

        def model_name(self):
            return "x"

        def translate_stream(self, jobs, direction, mappings, on_segment, cancel=None):
            on_segment(jobs[0].idx, "好")

    monkeypatch.setattr(microsoft, "MicrosoftClient", Fake)
    assert check_free(_cfg(tmp_path, "[timeouts]\nconnect = 9\n[free]\nenabled = true\nconnect = 1.25\n"))[0] is True
    assert seen == [1.25]


# ---------- 一层都没有 ----------
NOTHING = '[vllm]\napi_key = ""\n[fallback]\nbase_url = ""\n[free]\nenabled = false\n'


def _config_check(tmp_path, toml):
    (tmp_path / "config.toml").write_text(toml, encoding="utf-8")
    checks = dict(build_checks(tmp_path))
    return checks, checks["配置文件"]()


def test_no_service_at_all_is_red(tmp_path):
    checks, (ok, msg) = _config_check(tmp_path, NOTHING)
    assert ok is False and "没有任何可用的翻译服务" in msg
    assert checks["主服务"]() == (True, "未配置（不使用主服务）")  # 配置照样交给后面的项


def test_only_free_is_enough(tmp_path):
    _, res = _config_check(tmp_path, '[vllm]\napi_key = ""\n')
    assert res == (True, "读取成功")


def test_only_vllm_is_enough(tmp_path):
    _, res = _config_check(tmp_path, '[vllm]\nbase_url = "http://10.0.0.1:9000/v1"\napi_key = "k"\n[tencent]\nenabled = false\n')
    assert res == (True, "读取成功")


def test_only_fallback_is_enough(tmp_path):
    _, res = _config_check(tmp_path, '[fallback]\nbase_url = "http://127.0.0.1:9/v1"\n[tencent]\nenabled = false\n')
    assert res == (True, "读取成功")


def test_whole_doctor_with_free_only(tmp_path, fake_tm, monkeypatch, capsys):
    fake_tm(edge_map(lambda t: "你好，世界。"))
    (tmp_path / "config.toml").write_text('[vllm]\napi_key = ""\n', encoding="utf-8")
    monkeypatch.setattr("shotrans.ocr.available_languages", lambda: ["en-US", "zh-Hans-CN"])
    doctor.main(["--home", str(tmp_path)])
    out = capsys.readouterr().out
    assert "✅ 主服务：未配置（不使用主服务）" in out
    assert "✅ 微软免费翻译：单句" in out and "你好，世界。" in out

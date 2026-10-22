"""浮窗状态栏：腾讯一层的文字、颜色、vLLM 掉线附注。"""
import pytest

from shotrans.glossary import Glossary
from shotrans.models import SegmentResult
from shotrans.translate.router import Router as RealRouter
from tests.test_overlay_guards import Router, make  # noqa: F401  (make 是夹具)


@pytest.fixture
def quiet_t(make):  # noqa: F811
    return make(router=Router(auto=False))


def _show(w, service, idx=1):
    w.session.apply(w.session.generation, SegmentResult(idx, "a", service))
    w._refresh()
    return w._status.text()


def test_status_tencent_text_and_purple(quiet_t):
    text = _show(quiet_t, "tencent")
    assert "腾讯 · 译文" in text
    assert "#a855f7" in text
    assert "掉线" not in text


def test_status_tencent_when_vllm_down(quiet_t):
    quiet_t._router.down, quiet_t._router.last_kind = True, "connect"
    assert "腾讯（主服务掉线中） · 译文" in _show(quiet_t, "tencent")


def test_status_tencent_when_vllm_key_rejected(quiet_t):
    quiet_t._router.down, quiet_t._router.last_kind = True, "auth"
    text = _show(quiet_t, "tencent")
    assert "腾讯（主服务 key 无效或未填） · 译文" in text
    assert "掉线" not in text


def test_status_tencent_beats_fallback_and_vllm(quiet_t):
    quiet_t.session.apply(quiet_t.session.generation, SegmentResult(2, "b", "fallback"))
    assert "腾讯 · 译文" in _show(quiet_t, "tencent")


def test_status_tencent_with_router_none_does_not_crash(quiet_t):
    quiet_t._router = None
    assert "腾讯 · 译文" in _show(quiet_t, "tencent")


def test_status_tencent_with_vllm_not_configured_has_no_down_note(quiet_t):
    class T:
        name = "tencent"

    quiet_t._router = RealRouter(None, None, Glossary(), tencent=T())
    text = _show(quiet_t, "tencent")
    assert "腾讯 · 译文" in text and "主服务" not in text

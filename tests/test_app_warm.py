"""托盘程序在启动时、按快捷键时预热腾讯连接；只在启用腾讯时，绝不阻塞界面线程。"""
import threading
import time

import pytest

from shotrans import capture as capture_mod
from shotrans.glossary import Glossary
from shotrans.translate.router import Router
from shotrans.translate.tencent import TencentClient
from tests.fakes import transmart_map
from tests.test_app_guards import QuietRouter, autostart_calls, make_app, messages  # noqa: F401  (夹具)


class SpyTencent:
    name = "tencent"

    def __init__(self):
        self.calls = []

    def warm_async(self):
        self.calls.append(threading.current_thread())


class WarmRouter(QuietRouter):
    def __init__(self, tencent):
        super().__init__()
        self.tencent = tencent


@pytest.fixture
def no_grab(monkeypatch):
    """Capturer.start 里 80 毫秒后真的截屏、弹框选窗口；这里让定时器什么都不做。"""
    class _T:
        @staticmethod
        def singleShot(ms, fn):
            pass

    monkeypatch.setattr(capture_mod, "QTimer", _T)


def test_startup_warms_tencent(make_app):  # noqa: F811
    t = SpyTencent()
    make_app(router=WarmRouter(t))
    assert len(t.calls) == 1


def test_hotkey_warms_tencent(make_app, no_grab):  # noqa: F811
    t = SpyTencent()
    a = make_app(router=WarmRouter(t))
    t.calls.clear()
    a.hotkey.callback()  # 快捷键 → Capturer.start → before_grab
    assert len(t.calls) == 1
    a.capturer._busy = False


def test_no_tencent_no_warm_no_crash(make_app, no_grab, caplog, monkeypatch):  # noqa: F811
    started, warms = [], []
    real_start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start",
                        lambda self: (started.append(self.name), real_start(self))[1])
    monkeypatch.setattr(TencentClient, "warm", lambda self: warms.append(1))
    monkeypatch.setattr(TencentClient, "warm_async", lambda self: warms.append(1))
    a = make_app(router=WarmRouter(None))
    a.hotkey.callback()
    a.capturer._busy = False
    assert warms == []
    assert not any("tencent" in n for n in started)
    assert not any("预热" in r.getMessage() for r in caplog.records)  # 根本不去预热，而不是试了再报错


def test_router_without_tencent_attribute_no_crash(make_app, caplog):  # noqa: F811
    make_app()  # QuietRouter 没有 tencent 属性
    assert not any("预热" in r.getMessage() for r in caplog.records)


def test_warm_failure_to_start_is_logged_not_raised(make_app, caplog):  # noqa: F811
    class Boom:
        def warm_async(self):
            raise RuntimeError("起不了线程")

    make_app(router=WarmRouter(Boom()))
    assert any("预热启动失败" in r.getMessage() for r in caplog.records)


def test_real_warm_runs_off_gui_thread_and_does_not_block(make_app, make_transmart, monkeypatch):  # noqa: F811
    srv = make_transmart(transmart_map(lambda t: "好"))
    srv.head_delay = 1.0
    client = TencentClient(timeout_connect=0.5, timeout_read=2.0, url=srv.url)
    threads = []
    real_warm = TencentClient.warm

    def spy(self):
        threads.append(threading.current_thread())
        real_warm(self)

    monkeypatch.setattr(TencentClient, "warm", spy)
    t0 = time.monotonic()
    make_app(router=Router(None, None, Glossary(), tencent=client))
    assert time.monotonic() - t0 < 0.8  # 预热要 1 秒，构造不等它
    end = time.monotonic() + 3
    while not srv.heads and time.monotonic() < end:
        time.sleep(0.01)
    assert len(srv.heads) == 1
    assert threads and threads[0] is not threading.main_thread()

from ctypes import wintypes

import pytest
import shiboken6
from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtGui import QGuiApplication

from tests.fakes import FakeTransmart, FakeVllm

WM_GETOBJECT = 0x003D


class _NoAccessibilityQueries(QAbstractNativeEventFilter):
    """吞掉 WM_GETOBJECT（外部无障碍/UI 自动化客户端来查询窗口）。

    实测（2026-10-09，在本机抓的原生调用栈）：测试里只要有窗口显示出来，本机某个外部 UI Automation 客户端就会发
    WM_GETOBJECT；Qt 的 qwindows.dll 应答时经 UIAutomationCore -> combase 发起 COM 调用，而这条消息是「输入同步」调用，
    COM 抛出 RPC_E_CANTCALLOUT_ININPUTSYNCCALL（0x8001010d）再自己接住。pytest 的 faulthandler 用向量化异常处理，
    连这种「已被接住」的异常也会打印成 Windows fatal exception。和剪贴板无关（剪贴板早已换成内存假的）。
    测试不需要无障碍接口，所以在测试进程里直接不应答这条消息。"""

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            if wintypes.MSG.from_address(int(message)).message == WM_GETOBJECT:
                return True, 0
        return False, 0


@pytest.fixture(scope="session", autouse=True)
def _quiet_uia(qapp):
    f = _NoAccessibilityQueries()
    qapp.installNativeEventFilter(f)
    yield f
    qapp.removeNativeEventFilter(f)


@pytest.fixture(autouse=True)
def _private_instance_name(monkeypatch):
    """main() 的单实例互斥量每个测试用自己的名字：不和真在运行的托盘程序、也不和别的测试互相拦。"""
    import uuid

    from shotrans import app as app_mod
    monkeypatch.setattr(app_mod, "INSTANCE_NAME", f"Local\\shotrans-test-{uuid.uuid4().hex}")


@pytest.fixture(autouse=True)
def _no_real_transmart(monkeypatch):
    """单元测试绝不连真的腾讯接口：默认地址换成本机一个没人监听的端口（连不上，立刻失败）。
    要测成功路径的测试自己起 FakeTransmart，并把 url 传给客户端或再 monkeypatch 这个地址。"""
    from shotrans.translate import tencent
    from tests.fakes import closed_port_url

    monkeypatch.setattr(tencent, "TRANSMART_URL", closed_port_url().replace("/v1", "/api/imt"))


@pytest.fixture
def make_transmart():
    servers = []

    def _make(handler):
        s = FakeTransmart(handler)
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.close()


@pytest.fixture
def make_server():
    servers = []

    def _make(handler):
        s = FakeVllm(handler)
        servers.append(s)
        return s

    yield _make
    for s in servers:
        s.close()


class FakeClipboard:
    """内存剪贴板：真剪贴板走 OLE，在同步派发的测试事件里会打印 Windows fatal exception 0x8001010d，
    还会冲掉开发者自己的剪贴板内容。"""

    def __init__(self):
        self._text = ""

    def setText(self, text, *_):
        self._text = text

    def text(self, *_):
        return self._text


@pytest.fixture(autouse=True)
def fake_clipboard(request, monkeypatch):
    """所有单元测试默认用内存剪贴板；标了 real_clipboard 的冒烟测试用真的。"""
    if request.node.get_closest_marker("real_clipboard"):
        return None
    fake = FakeClipboard()
    monkeypatch.setattr(QGuiApplication, "clipboard", staticmethod(lambda: fake))
    return fake


@pytest.fixture
def track(qtbot):
    """窗口带 WA_DeleteOnClose，qtbot.addWidget 收尾时会对已删除的对象再 close 一次而报错，所以自己收尾。"""
    made = []
    yield made.append
    for w in made:
        if shiboken6.isValid(w):
            w.close()


@pytest.fixture(autouse=True)
def _private_dirs(tmp_path_factory, monkeypatch):
    """测试绝不碰真实数据目录（%APPDATA%\\截图翻译）和程序目录里的旧配置（config.local.toml 含真 key）：
    数据目录、旧数据目录都指向本测试自己的空临时目录。要测迁移的用例自己传目录。"""
    monkeypatch.setenv("SHOTRANS_HOME", str(tmp_path_factory.mktemp("home")))
    monkeypatch.setenv("SHOTRANS_LEGACY_DIR", str(tmp_path_factory.mktemp("legacy")))


@pytest.fixture(autouse=True)
def _private_startup_dir(tmp_path_factory, monkeypatch):
    """测试绝不碰真实「启动」文件夹：没打桩 autostart 的用例读写的都是自己的空临时目录。
    试用模式开关也清掉（开发者可能在试用 bat 打开的环境里跑测试），要测的用例自己 setenv。"""
    from shotrans import autostart
    folder = tmp_path_factory.mktemp("startup")
    monkeypatch.setattr(autostart, "startup_dir", lambda: folder)
    monkeypatch.delenv("SHOTRANS_TRIAL", raising=False)


@pytest.fixture(autouse=True)
def _zh_language():
    """界面语言是进程级的：每个测试都从中文开始，结束后复位，免得切到英文的测试影响别的测试。"""
    from shotrans import i18n
    i18n._current = "zh"
    yield
    i18n._current = "zh"

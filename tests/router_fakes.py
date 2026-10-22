"""Router 两个测试文件共用的假件（只在这里定义一份）。"""
from shotrans.glossary import Glossary
from shotrans.models import Box, OcrLine, Segment
from shotrans.translate.base import TranslateError
from shotrans.translate.router import Router


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeService:
    def __init__(self, name, script):
        self.name = name
        self.script = script
        self.calls = []

    def translate_stream(self, jobs, direction, mappings, on_segment, cancel=None):
        self.calls.append({"jobs": list(jobs), "direction": direction,
                           "mappings": list(mappings), "cancel": cancel})
        self.script(self, jobs, on_segment)


def echo(prefix):
    def script(svc, jobs, on):
        for j in jobs:
            on(j.idx, prefix + j.text)
    return script


def fail(kind):
    def script(svc, jobs, on):
        raise TranslateError(kind)
    return script


def seg(idx, text, skip=False):
    return Segment(idx, text, [OcrLine(text, Box(0, idx * 30, 100, 20))], skip)


def run(router, segs, direction="en2zh", cancel=None):
    out = []
    router.translate(segs, direction, out.append, cancel)
    return {r.idx: r for r in out}, out


def make(vllm_script, fb_script=None, glossary=Glossary(), clock=None):
    v = FakeService("vllm", vllm_script)
    m = FakeService("fallback", fb_script or echo("M"))
    return Router(v, m, glossary, breaker_seconds=60, clock=clock or Clock()), v, m


def make3(vllm_script=None, fb_script=None, tc_script=None, clock=None, glossary=Glossary()):
    """三层：某层脚本传 None 就是没配置这一层（Router 拿到 None）。"""
    v = FakeService("vllm", vllm_script) if vllm_script else None
    m = FakeService("fallback", fb_script) if fb_script else None
    t = FakeService("tencent", tc_script) if tc_script else None
    return Router(v, m, glossary, breaker_seconds=60, clock=clock or Clock(), tencent=t), v, m, t


def make_no_fb(vllm_script, clock=None):
    v = FakeService("vllm", vllm_script)
    return Router(v, None, Glossary(), breaker_seconds=60, clock=clock or Clock()), v

"""假的 vLLM（OpenAI 兼容）HTTP 服务，跑在本机随机端口上。"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from shotrans.translate.base import Job
from shotrans.translate.vllm import VllmClient


class _Server:
    def __init__(self, handler_cls):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _json(h, code, obj):
    body = json.dumps(obj).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


class FakeVllm(_Server):
    """chat_handler(h, payload) 负责写 /chat/completions 的响应。"""

    def __init__(self, chat_handler, models=("fake-model",)):
        self.requests: list[dict] = []
        self.models_hits = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.endswith("/models"):
                    outer.models_hits += 1
                    _json(self, 200, {"data": [{"id": m} for m in models]})
                else:
                    self.send_error(404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(n))
                outer.requests.append({"auth": self.headers.get("Authorization"), "json": payload})
                try:
                    chat_handler(self, payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # 客户端超时后主动断开，正常

        super().__init__(H)
        self.url = self.base + "/v1"


def start_sse(h):
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.end_headers()
    h.wfile.flush()


def send_delta(h, text):
    obj = {"choices": [{"delta": {"content": text}}]}
    h.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
    h.wfile.flush()


def send_done(h):
    h.wfile.write(b"data: [DONE]\n\n")
    h.wfile.flush()


def sse_chunks(chunks, delay=0.0):
    def handler(h, payload):
        start_sse(h)
        h.wfile.write(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n')  # 只有角色、没有字
        for c in chunks:
            send_delta(h, c)
            time.sleep(delay)
        send_done(h)

    return handler


class FakeTransmart(_Server):
    """假的腾讯交互翻译接口（POST /api/imt）。handler(h, payload) 负责写响应。"""

    def __init__(self, handler):
        self.requests: list[dict] = []
        self.heads: list[dict] = []  # 预热发来的 HEAD：{"path", "start", "end"}（time.monotonic）
        self.head_delay = 0.0
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def do_HEAD(self):
                rec = {"path": self.path, "start": time.monotonic(), "end": None}
                outer.heads.append(rec)
                time.sleep(outer.head_delay)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                rec["end"] = time.monotonic()

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n)
                outer.requests.append({"content_type": self.headers.get("Content-Type"),
                                       "path": self.path, "json": json.loads(raw), "at": time.monotonic()})
                try:
                    handler(self, outer.requests[-1]["json"])
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        super().__init__(H)
        self.url = self.base + "/api/imt"


def transmart_reply(h, translations, ret_code="succ", code=200):
    _json(h, code, {"header": {"ret_code": ret_code}, "auto_translation": translations})


def transmart_map(fn):
    """每段原文经 fn 变成译文，按 TranSmart 的格式回。"""
    def handler(h, payload):
        transmart_reply(h, [fn(t) for t in payload["source"]["text_list"]])
    return handler


def closed_port_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/v1"


# ---------- vLLM 测试共用 ----------

JOBS2 = [Job(1, "Hello"), Job(2, "World")]


def _client(url):
    return VllmClient(url, "k", connect_timeout=0.5, first_token_timeout=0.4, stall_timeout=0.4)


def _collect(client, jobs):
    got = []
    client.translate_stream(jobs, "en2zh", [], lambda i, t: got.append((i, t)))
    return got

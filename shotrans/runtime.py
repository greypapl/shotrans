"""进程级守卫：未捕获异常记日志（pythonw 下没有控制台，不记就无声消失）、只允许一个实例运行。"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183


class ErrorReporter:
    """接管 sys.excepthook 和 threading.excepthook：异常写进日志，并（限频地）提醒用户。

    notify 可能在任意线程里被调用，所以只能是线程安全的东西（例如 Qt 信号的 emit），不能直接碰界面。"""

    def __init__(self, notify: Callable[[], None] | None = None, min_interval: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.notify = notify
        self.min_interval = min_interval
        self.clock = clock
        self._last: float | None = None
        self._lock = threading.Lock()
        self._old = None

    def handle(self, exc_type, exc, tb, where: str = "") -> None:
        log.error("未捕获的异常%s", where, exc_info=(exc_type, exc, tb))
        if self.notify is None:
            return
        with self._lock:
            now = self.clock()
            if self._last is not None and now - self._last < self.min_interval:
                return  # 限频：连环报错时只提醒一次，免得提醒刷屏
            self._last = now
        try:
            self.notify()
        except Exception:
            log.exception("提醒用户出错时又出错")

    def sys_hook(self, exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        self.handle(exc_type, exc, tb)

    def thread_hook(self, args) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        self.handle(args.exc_type, args.exc_value, args.exc_traceback, f"（线程 {name}）")

    def install(self) -> None:
        self._old = (sys.excepthook, threading.excepthook)
        sys.excepthook = self.sys_hook
        threading.excepthook = self.thread_hook

    def uninstall(self) -> None:
        if self._old is not None:
            sys.excepthook, threading.excepthook = self._old
            self._old = None


class SingleInstance:
    """命名互斥量：同一用户会话里第二次启动时 acquire() 返回 False。"""

    def __init__(self, name: str):
        self.name = name
        self.handle = None
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.CreateMutexW.restype = wintypes.HANDLE
        self._k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self._k32.CloseHandle.argtypes = [wintypes.HANDLE]

    def acquire(self) -> bool:
        h = self._k32.CreateMutexW(None, False, self.name)
        err = ctypes.get_last_error()
        if not h:
            log.warning("创建单实例互斥量失败（错误码 %s），不做单实例限制", err)
            return True  # 锁建不起来时宁可放行，也不能让用户打不开程序
        if err == ERROR_ALREADY_EXISTS:
            self._k32.CloseHandle(h)
            return False
        self.handle = h
        return True

    def release(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None

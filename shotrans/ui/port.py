"""窗口和托盘程序之间的接口。窗口只用这里列出的东西；测试用 tests/ui_fakes.FakeApp 代替托盘程序。"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..config import Config
from ..history import History


class AppPort(Protocol):
    home: Path
    config: Config
    history: History
    log_file: Path

    def save_config(self, new: Config) -> list[str]:
        """写 config.toml（失败抛 ConfigError，什么都不改）并立即生效；返回没生效的项（已翻译好的文字）。"""

    def open_window(self, kind: str, page: str | None = None):
        """kind：settings / doctor / history / about / wizard；page 只对设置窗口有意义。
        窗口代码不要依赖返回值（FakeApp 返回 None；真程序返回窗口对象，只供托盘自己和测试用）。"""

    def start_capture(self) -> None: ...

    def hotkey_free(self, spec: str) -> bool:
        """这个快捷键现在能不能用（是程序自己正在用的那个也算能用）。"""

    def own_hotkey(self) -> str | None:
        """程序自己注册成功的快捷键；没注册上返回 None。"""

    def reload_config(self) -> bool: ...

    def on_wizard_closed(self, completed: bool) -> None: ...

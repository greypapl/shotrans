"""窗口测试用的托盘程序替身，实现 shotrans.ui.port.AppPort。
T6 一次写全；T7～T11 的窗口测试只用、不改这个文件（要别的行为就在自己的测试里给实例属性赋值）。"""
from __future__ import annotations

from pathlib import Path

from shotrans.config import Config, ConfigError
from shotrans.history import History


class FakeApp:
    def __init__(self, home: Path, config: Config | None = None):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.config = config if config is not None else Config()
        self.history = History(self.home / "history.json", self.config.history_limit)
        self.log_file = self.home / "logs" / "shotrans.log"
        self.saved: list[Config] = []
        self.save_error: str | None = None        # 设了就让 save_config 抛 ConfigError(这段文字)
        self.apply_failures: list[str] = []       # save_config 的返回值
        self.opened: list[tuple[str, str | None]] = []
        self.captures = 0
        self.reloads = 0
        self.wizard_closed: list[bool] = []
        self.taken_hotkeys: set[str] = set()      # hotkey_free 对这些返回 False
        self.hotkey_checks: list[str] = []
        self.registered_hotkey: str | None = self.config.hotkey

    def save_config(self, new: Config) -> list[str]:
        if self.save_error is not None:
            raise ConfigError(self.save_error)
        self.saved.append(new)
        self.config = new
        return list(self.apply_failures)

    def open_window(self, kind: str, page: str | None = None):
        self.opened.append((kind, page))

    def start_capture(self) -> None:
        self.captures += 1

    def hotkey_free(self, spec: str) -> bool:
        self.hotkey_checks.append(spec)
        return spec not in self.taken_hotkeys

    def own_hotkey(self) -> str | None:
        return self.registered_hotkey

    def reload_config(self) -> bool:
        self.reloads += 1
        return True

    def on_wizard_closed(self, completed: bool) -> None:
        self.wizard_closed.append(completed)

"""托盘主程序：快捷键 → 框选 → 浮窗；托盘菜单打开设置、自检、历史、关于、引导窗口。"""
from __future__ import annotations

import ctypes
import dataclasses
import logging
import sys
import tomllib
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart, i18n, paths
from .capture import Capturer
from .config import Config, ConfigError, load_config, write_config
from .glossary import Glossary, load_glossary
from .history import History
from .hotkey import GlobalHotkey, parse_hotkey, try_register
from .i18n import N_, t
from .iconart import make_icon
from .migrate import migrate_legacy, needs_migration
from .ocr import available_languages, recognize
from .overlay import OverlayWindow
from .runtime import ErrorReporter, SingleInstance
from .session import Session
from .translate.router import Router
from .translate.tencent import TencentClient
from .translate.factory import service_client
from .ui.notice import NoticePopup
from .ui.windows import WindowManager

log = logging.getLogger(__name__)

INSTANCE_NAME = "Local\\shotrans-single-instance"  # 单实例互斥量（当前用户会话内）

GLOSSARY_TEMPLATE = """# 术语表：一行一条，# 开头是注释。
# 「原文 => 译法」指定译法；只写一个词表示保持原样、不翻。
# 例：
# depression => 抑郁
# DAIC-WOZ
"""


def ensure_glossary(path: Path) -> None:
    if not path.exists():
        path.write_text(GLOSSARY_TEMPLATE, encoding="utf-8")


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "shotrans.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def build_router(c, glossary) -> Router:
    """主服务 → 备用服务 → 腾讯，每层都可不配置：base_url 为空 = 不用这层；[tencent] enabled = false = 不用腾讯。
    熔断、限流等现有行为仍只针对主服务（Router.vllm）。"""
    tencent = TencentClient(c.tencent_connect_timeout) if c.tencent_enabled else None
    return Router(service_client(c, "primary"), service_client(c, "backup"), glossary, c.breaker_seconds,
                  tencent=tencent, chunk_chars=c.chunk_chars, chunk_max_segments=c.chunk_max_segments,
                  max_concurrency=c.max_concurrency)


def migration_notice(m, home: Path):
    """迁移结果的提醒（文字, 图标）；没做迁移返回 None。"""
    if m is None or m.status not in ("migrated", "failed"):
        return None
    if m.failed_copies:
        tail = t("notice.migrate_copy_failed", files=t("list.sep").join(m.failed_copies), legacy=m.legacy, home=home)
    else:
        tail = ""
    if m.status == "migrated":
        icon = QSystemTrayIcon.MessageIcon.Warning if m.failed_copies else QSystemTrayIcon.MessageIcon.Information
        return t("notice.migrated", path=home) + tail, icon
    where = tail or t("notice.migrate_old_kept", legacy=m.legacy)
    return t("notice.migrate_failed", reason=m.reason) + where, QSystemTrayIcon.MessageIcon.Warning


def same_hotkey(a: str, b: str) -> bool:
    """按解析结果比较（Ctrl+Alt+Q 和 ctrl+alt+q 是同一个）；任一个写错就算不同。"""
    try:
        return parse_hotkey(a) == parse_hotkey(b)
    except ValueError:
        return False


def _default_factories(app) -> dict[str, Callable]:
    """五种窗口的入口。窗口模块在各自任务里实现（T8～T11），这里延迟导入；构造签名见实施计划 T6。"""
    def settings(page):
        from .ui.settings_window import SettingsWindow
        return SettingsWindow(app, page=page)

    def doctor(page):
        from .ui.doctor_window import DoctorWindow
        return DoctorWindow(app)

    def history(page):
        from .ui.history_window import HistoryWindow
        return HistoryWindow(app)

    def about(page):
        from .ui.about_window import AboutWindow
        return AboutWindow(app)

    def wizard(page):
        from .ui.wizard import Wizard
        return Wizard(app)

    return {"settings": settings, "doctor": doctor, "history": history, "about": about, "wizard": wizard}


class ShotTransApp(QObject):
    # 未捕获异常的提醒：可能从任意线程发出，信号排队到界面线程再弹提示小窗
    error_notice = Signal()

    def __init__(self, qapp: QApplication, home: Path, ocr_fn=recognize, router=None, migration=None,
                 router_factory: Callable | None = None, window_factories: dict | None = None,
                 ocr_languages: Callable[[], list[str]] | None = None):
        super().__init__()
        self.error_notice.connect(self._show_error_notice)
        self._qapp = qapp
        self._shut = False
        self.home = home
        self.log_file = paths.log_dir(home) / "shotrans.log"
        self.ocr_fn = ocr_fn
        home.mkdir(parents=True, exist_ok=True)
        cfg_path = paths.config_path(home)
        self.needs_setup = not cfg_path.exists()  # 没配置过：快捷键、点托盘改为打开引导，启动时也自动打开一次
        self.config = Config() if self.needs_setup else load_config(cfg_path)
        i18n.set_language(self.config.language)
        c = self.config
        self.glossary_path = paths.glossary_path(home)
        ensure_glossary(self.glossary_path)
        self._router_factory = router_factory or build_router
        glossary_failed = False
        if router is None:
            try:
                glossary = load_glossary(self.glossary_path)
            except Exception:  # 和截图时一样：术语表坏了不能拦住启动
                log.exception("启动时读取术语表失败，先用空术语表")
                glossary, glossary_failed = Glossary(), True
            router = self._router_factory(c, glossary)
        self.router = router
        self.history = History(paths.history_path(home), c.history_limit,
                               images_dir=paths.history_images_dir(home), save_images=c.history_save_images)
        self.overlays: list[OverlayWindow] = []
        self._hidden: list[OverlayWindow] = []  # 截屏前被藏起来的浮窗
        self.capturer = Capturer(before_grab=self._before_grab, after_grab=self._after_grab)
        self.capturer.captured.connect(self.on_captured)
        self.capturer.cancelled.connect(self._restore_after_cancel)
        self.windows = WindowManager(window_factories if window_factories is not None else _default_factories(self))
        self._notice_target: str | None = None  # 点最近一条提醒时打开的窗口（"kind" 或 "kind:page"）
        self._noticed = False  # 弹过提醒没有：启动时已有提醒（快捷键被占、迁移、key 解不开…）就不再弹启动提示
        self._last_failed: list[str] = []       # 最近一次 apply_config 没生效的项
        self._pending_notice: tuple | None = None  # 截图 / 框选进行中来的提醒（文字, 目标, 图标），结束后再弹
        # 提醒用程序自己画的小窗，不用托盘气泡：Windows 关了通知 / 开了勿扰时，托盘气泡会被系统直接吞掉
        self.notice = NoticePopup()
        self.notice.clicked.connect(self._on_notice_clicked)
        self._build_tray()
        i18n.hub().language_changed.connect(self._retranslate_tray)
        self.hotkey = GlobalHotkey(c.hotkey, self.start_capture)
        qapp.installNativeEventFilter(self.hotkey)
        if not self.hotkey.register():
            self.notify(t("notice.hotkey_taken", hotkey=c.hotkey), target="settings:hotkey")
            log.warning("快捷键 %s 注册失败", c.hotkey)
        if glossary_failed:
            self.notify(t("notice.glossary_startup", log=self.log_file))
        if not c.primary.configured:
            # 没配主服务是允许的配置（走备用/腾讯），只记日志不弹提醒
            log.info("没有配置主服务（[primary] base_url 为空），当前不使用主服务")
        for role, svc in ((N_("role.primary"), c.primary), (N_("role.backup"), c.backup)):
            if svc.configured and svc.key_unreadable:  # 没填地址 = 不用这层，key 解不开也无所谓
                self.notify(t("notice.key_unreadable", role=t(role)), target="settings:service")
        notice = migration_notice(migration, home)
        if notice is not None:
            self.notify(notice[0], target="wizard" if migration.status == "failed" else None, icon=notice[1])
        self._check_ocr_languages(ocr_languages or available_languages)
        if self.needs_setup:
            QTimer.singleShot(0, self._open_setup_on_start)
        self._warm_tencent()

    def _warm_tencent(self) -> None:
        """腾讯一层启用时，在后台线程里预热 TLS 连接（启动时、每次按快捷键时）。绝不阻塞界面线程。"""
        tencent = getattr(self.router, "tencent", None)
        warm = getattr(tencent, "warm_async", None)
        if warm is None:
            return
        try:
            warm()
        except Exception:  # 起线程失败之类：预热只是锦上添花
            log.exception("腾讯预热启动失败")

    # ---------- 托盘 ----------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon())
        menu = QMenu()
        self._actions: dict[str, QAction] = {}

        def add(key: str, slot) -> None:
            a = QAction(menu)
            a.triggered.connect(slot)
            menu.addAction(a)
            self._actions[key] = a

        add("capture", lambda _=False: self.start_capture())
        add("history", lambda _=False: self.open_window("history"))
        menu.addSeparator()
        add("settings", lambda _=False: self.open_window("settings"))
        add("doctor", lambda _=False: self.open_window("doctor"))
        add("reload", lambda _=False: self._reload_from_menu())
        add("about", lambda _=False: self.open_window("about"))
        menu.addSeparator()
        add("quit", lambda _=False: QApplication.quit())
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.start_capture() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self._retranslate_tray()
        self.tray.show()

    def _retranslate_tray(self, *_) -> None:
        hk = self.config.hotkey
        self.tray.setToolTip(t("tray.tooltip", hotkey=hk))
        texts = {"capture": t("tray.capture", hotkey=hk), "history": t("tray.history"),
                 "settings": t("tray.settings"), "doctor": t("tray.doctor"), "reload": t("tray.reload"),
                 "about": t("tray.about"), "quit": t("tray.quit")}
        for key, text in texts.items():
            self._actions[key].setText(text)

    def _reload_from_menu(self) -> None:
        """托盘「重新加载配置」：成功且全部生效时给个回执（读失败、有项没生效时 reload/apply 自己会弹提醒）。"""
        if self.reload_config() and not self._last_failed:
            self.notify(t("notice.reloaded"), icon=QSystemTrayIcon.MessageIcon.Information)

    def notify(self, text: str, target: str | None = None,
               icon=QSystemTrayIcon.MessageIcon.Warning) -> None:
        """右下角的提示小窗（ui/notice.py，不走 Windows 通知）。icon 沿用托盘气泡的 MessageIcon，Warning/Critical
        显示成警告样式。target（如 "settings:hotkey"、"doctor"、"wizard"）= 点小窗时打开的窗口；只用一次。
        只有一个小窗：新提醒替换旧的（文字、点击目标都以最新一条为准）。
        正在截图 / 框选时先存着，框选结束或取消后（_after_grab）再弹；存着的也算「已有提醒」（announce_start 不再弹）。"""
        self._noticed = True
        if self.capturer.busy:  # 正在截图 / 框选：小窗会被冻进截图或盖在框选层上，先存着，结束后再弹（最新一条为准）
            self._pending_notice = (text, target, icon)
            return
        self._notice_target = target
        self.notice.show_notice(t("app.name"), text, icon, clickable=target is not None)

    def announce_start(self) -> None:
        """手动启动（不是开机自启）时由 main 调用：提示小窗告诉用户程序已在托盘里、按什么键截图，点它打开历史记录。
        首次运行（会打开引导）不弹；启动时已经弹过别的提醒也不弹——提示小窗只有一个，后一条会顶掉前一条，
        不能让「已在后台运行」把快捷键被占、迁移结果、key 解不开这类提醒挤掉。"""
        if self.needs_setup or self._noticed:
            return
        self.notify(t("notice.started", hotkey=self.config.hotkey), target="history",
                    icon=QSystemTrayIcon.MessageIcon.Information)

    def _on_notice_clicked(self) -> None:
        target, self._notice_target = self._notice_target, None
        if target is None:
            return
        kind, _, page = target.partition(":")
        self.open_window(kind, page or None)

    def _show_error_notice(self) -> None:
        self.notify(t("notice.internal_error", log=self.log_file), target="doctor")

    def _check_ocr_languages(self, fn) -> None:
        try:
            langs = set(fn())
        except Exception:
            log.exception("查询 OCR 语言包失败")
            return
        if "en-US" not in langs and "zh-Hans-CN" not in langs:
            self.notify(t("notice.ocr_missing_all"), target="doctor")

    def _open_setup_on_start(self) -> None:
        if not self._shut and self.needs_setup:
            self.open_window("wizard")

    # ---------- AppPort ----------
    def open_window(self, kind: str, page: str | None = None):
        return self.windows.show(kind, page)

    def start_capture(self) -> None:
        if self.needs_setup:  # 还没配置翻译服务：快捷键、点托盘都改为打开引导
            self.open_window("wizard")
            return
        self.capturer.start()

    def on_wizard_closed(self, completed: bool) -> None:
        if not completed and self.needs_setup and not self._shut:  # 退出时关掉引导：不弹提醒
            self.notify(t("notice.not_configured"), target="wizard",
                        icon=QSystemTrayIcon.MessageIcon.Information)

    def own_hotkey(self) -> str | None:
        return self.config.hotkey if self.hotkey.registered else None

    def hotkey_free(self, spec: str) -> bool:
        try:
            target = parse_hotkey(spec)
        except ValueError:
            return False
        if self.hotkey.registered and target == (self.hotkey.mods, self.hotkey.vk):
            return True
        return try_register(spec)

    def save_config(self, new: Config) -> list[str]:
        write_config(paths.config_path(self.home), new)  # 失败抛 ConfigError，什么都不改
        self.needs_setup = False
        return self.apply_config(new)

    def reload_config(self) -> bool:
        try:
            new = load_config(paths.config_path(self.home))
        except (ConfigError, OSError) as e:  # OSError：没权限、被别的程序锁住之类，按读取失败处理
            self.notify(t("notice.reload_failed", error=e))
            return False
        self.needs_setup = False
        self.apply_config(new)
        return True

    def apply_config(self, new: Config) -> list[str]:
        """设置保存、重新加载配置共用。按顺序：路由 → 快捷键 → 托盘文字 → 开机自启 → 语言；
        任一步失败不影响其余步骤，失败项汇总成一条提醒。返回失败项（已按新语言翻译好的文字）。"""
        old = self.config
        failed: list[tuple[str, dict]] = []  # (文字键, 参数)：最后统一翻译，汇总提示与新语言一致
        try:  # 1. 路由：进行中的浮窗手里拿的是旧路由，继续用完；新截图用新的
            glossary = getattr(self.router, "glossary", None) or Glossary()
            self.router = self._router_factory(new, glossary)
        except Exception:
            log.exception("重建翻译路由失败")
            failed.append((N_("apply.router"), {}))
        hotkey = old.hotkey  # 2. 快捷键：注销旧键 → 注册新键；失败就重新注册旧键
        try:
            same = same_hotkey(new.hotkey, old.hotkey)
            if same:
                hotkey = new.hotkey  # 同一组合只是写法不同（大小写）：沿用新写法
            if not same or not self.hotkey.registered:  # 启动时被占用、后来让出来了：同一组合也要重新注册
                result = self._swap_hotkey(new.hotkey)
                if result == "ok":
                    hotkey = new.hotkey
                elif result == "restored":
                    failed.append((N_("apply.hotkey"), {"hotkey": new.hotkey}))
                else:
                    failed.append((N_("apply.hotkey_lost"), {"hotkey": new.hotkey}))
        except Exception:
            log.exception("更换快捷键失败")
            failed.append((N_("apply.hotkey"), {"hotkey": new.hotkey}))
        self.config = dataclasses.replace(new, hotkey=hotkey)
        self.history.limit = new.history_limit
        self.history.save_images = new.history_save_images
        try:  # 3. 托盘文字
            self._retranslate_tray()
        except Exception:
            log.exception("更新托盘文字失败")
            failed.append((N_("apply.tray"), {}))
        # 4. 开机自启（None = 配置里没写，不动；试用模式不动系统级的「启动」文件夹，也不算失败）
        if new.autostart is not None and not autostart.trial_mode():
            try:
                self._sync_autostart(new.autostart)
            except Exception:
                log.exception("设置开机自启失败")
                failed.append((N_("apply.autostart"), {}))
        try:  # 5. 语言：发信号，托盘与已打开的窗口刷新文字
            i18n.set_language(new.language)
        except Exception:
            log.exception("切换界面语言失败")
            failed.append((N_("apply.language"), {}))
        items = [t(k, **kw) for k, kw in failed]
        self._last_failed = items
        if items:
            hotkey_failed = any(k in ("apply.hotkey", "apply.hotkey_lost") for k, _ in failed)
            self.notify(t("notice.apply_failed", items=t("list.sep").join(items)),
                        target="settings:hotkey" if hotkey_failed else None)
        return items

    def _swap_hotkey(self, spec: str) -> str:
        """返回 "ok"（换上了）、"restored"（新键注册失败，旧键已恢复）、"lost"（新旧都没注册上）。
        先构造新键再注销旧键：spec 写错时构造就抛 ValueError，旧键原封不动。"""
        old = self.hotkey
        new = GlobalHotkey(spec, self.start_capture)
        old.unregister()
        if new.register():
            self._qapp.removeNativeEventFilter(old)
            self._qapp.installNativeEventFilter(new)
            self.hotkey = new
            return "ok"
        if not old.register():
            log.warning("新快捷键 %s 注册失败，旧快捷键也没能重新注册", spec)
            return "lost"
        return "restored"

    def _sync_autostart(self, on: bool) -> None:
        """开：快捷方式不在、或指向的不是本程序（移动过文件夹、从源码版换成 exe）就重写；关：在就删。
        只在保存设置 / 重新加载配置时调用，启动时不偷偷改。"""
        if on:
            cmd = autostart.launch_command()
            if not autostart.points_to(*cmd):
                autostart.enable(*cmd)
        elif autostart.is_enabled():
            autostart.disable()

    # ---------- 截图 ----------
    def _before_grab(self) -> None:
        """按下快捷键（或点托盘）后、截屏前：趁用户框选的时间预热腾讯连接，再藏浮窗。"""
        self._warm_tencent()
        self.notice.hide()  # 和浮窗一样：别被冻进截图（启动提示正写着「按 … 截图翻译」，读完马上按是常态）；截完不放回
        self._hide_overlays()

    def _after_grab(self) -> None:
        """框选结束或取消（浮窗出现之前）：放回钉住的浮窗，再弹截图期间推迟的提醒。"""
        self._show_pinned()
        pending, self._pending_notice = self._pending_notice, None
        if pending is not None:
            text, target, icon = pending
            self.notify(text, target=target, icon=icon)

    def _hide_overlays(self) -> None:
        """截屏前把所有浮窗（含没钉住的）藏起来，免得上一张译文被冻进下一张截图。"""
        self._hidden = [o for o in self.overlays if o.isVisible()]
        for o in self._hidden:
            o.hide()

    def _show_pinned(self) -> None:
        for o in self.overlays:
            if o.pinned:
                o.show()

    def _restore_after_cancel(self) -> None:
        """没框成：把截屏前可见、没钉住的浮窗也放回来（钉住的已由 _show_pinned 放回）。"""
        hidden, self._hidden = self._hidden, []
        for o in hidden:
            if o in self.overlays and not o.pinned:
                o.show()
                o.raise_()
                o.activateWindow()

    def on_captured(self, image, rect: QRect) -> None:
        self._hidden = []
        for o in list(self.overlays):
            if not o.pinned:
                o.close()
        try:
            self.router.glossary = load_glossary(self.glossary_path)
        except Exception:
            log.exception("读取术语表失败，沿用上一份")
            self.notify(t("notice.glossary_capture", log=self.log_file))
        w = OverlayWindow(Session(image), rect, self.ocr_fn, self.router, self.history, open_window=self.open_window)
        w.closed.connect(self._on_overlay_closed)
        self.overlays.append(w)
        w.show()
        w.raise_()
        w.activateWindow()

    def _on_overlay_closed(self, w) -> None:
        if w in self.overlays:
            self.overlays.remove(w)

    def shutdown(self) -> None:
        first, self._shut = not self._shut, True
        self.windows.close_all()
        self.notice.hide()
        if first:  # 只断开一次：重复断开时 PySide6 先发 RuntimeWarning，-W error 下会变成 SystemError
            try:
                i18n.hub().language_changed.disconnect(self._retranslate_tray)
            except (RuntimeError, TypeError):
                pass
        self.hotkey.unregister()
        self._qapp.removeNativeEventFilter(self.hotkey)
        for o in list(self.overlays):
            o.close()  # 钉住的也关：pinned 只影响失焦自动关闭，不拦 close()
        self.tray.hide()
        self.tray.setContextMenu(None)  # 菜单先于托盘图标被销毁时，托盘会拿着悬空指针


def _show_fatal(text: str) -> None:
    """启动失败的提示。QApplication 都没建起来时 QMessageBox 用不了，退回系统消息框。"""
    try:
        if QApplication.instance() is not None:
            QMessageBox.critical(None, t("app.name"), text)
        else:
            ctypes.windll.user32.MessageBoxW(None, text, t("app.name"), 0x10)
    except Exception:
        log.exception("弹出错误提示失败")


def _early_language(home: Path) -> None:
    """启动早期（读完整配置之前）就按 config.toml 的 [general] language 设好界面语言，
    让「已在运行」、配置报错、迁移失败这些最先出现的文字也用对语言。读不到、写错了一律留在中文。"""
    try:
        with open(paths.config_path(home), "rb") as f:
            lang = tomllib.load(f)["general"]["language"]
        i18n.set_language(lang)  # 不支持的值（"fr"、1）抛 ValueError，同样留在中文
    except Exception:
        pass


def _early_hotkey(home: Path) -> str | None:
    """「已在运行」提示里写的快捷键：和 _early_language 一样直接读 config.toml，尽力而为。
    没写 [hotkey] capture 就是默认值；没有配置文件、读不了、写错了返回 None（提示里不写快捷键）。"""
    try:
        with open(paths.config_path(home), "rb") as f:
            data = tomllib.load(f)
        spec = data.get("hotkey", {}).get("capture", Config().hotkey)
        parse_hotkey(spec)  # 写错了（"Q"、3）抛异常：不写快捷键
        return spec
    except Exception:
        return None


def _already_running_text(home: Path) -> str:
    hotkey = _early_hotkey(home)
    if hotkey is None:
        return t("fatal.already_running")
    return t("fatal.already_running_hotkey", hotkey=hotkey)


def main(argv: list[str] | None = None, home: Path | None = None, instance_name: str | None = None) -> int:
    raw = sys.argv if argv is None else argv
    if "--selftest" in raw[1:]:   # 自测：不建托盘、不抢单实例、数据目录用临时目录（见 selftest.py）
        from .selftest import selftest_main
        return selftest_main(list(raw))
    autostarted = autostart.AUTOSTART_ARG in raw[1:]  # 开机自启快捷方式带的标记：不弹启动提示
    qt_argv = [a for a in raw if a != autostart.AUTOSTART_ARG]  # 标记是给自己看的，不交给 Qt
    home = home or paths.data_dir()
    _early_language(home)
    log_file = paths.log_dir(home) / "shotrans.log"
    reporter = ErrorReporter()
    reporter.install()  # pythonw 没有控制台：槽函数、线程里的异常不接住就无声消失
    instance = SingleInstance(instance_name or INSTANCE_NAME)
    try:
        try:
            home.mkdir(parents=True, exist_ok=True)
            setup_logging(paths.log_dir(home))
            qapp = QApplication.instance() or QApplication(qt_argv)
            qapp.setQuitOnLastWindowClosed(False)
            if not instance.acquire():
                log.info("截图翻译已在运行，本次启动直接退出")
                QMessageBox.information(None, t("app.name"), _already_running_text(home))
                return 0
            legacy = paths.legacy_dir()
            migration = None
            if needs_migration(home, legacy):
                try:
                    auto = autostart.is_enabled()
                except Exception:  # 查不到自启状态不能拦住启动：当作「不知道」，不写进配置
                    log.exception("迁移时读取开机自启状态失败")
                    auto = None
                migration = migrate_legacy(home, legacy, auto)
            app = ShotTransApp(qapp, home, migration=migration)
        except ConfigError as e:
            log.error("配置错误：%s", e)
            _show_fatal(str(e))
            return 1
        except Exception:
            log.exception("启动失败")
            _show_fatal(t("fatal.startup", log=log_file))
            return 1
        notice = getattr(app, "error_notice", None)
        reporter.notify = notice.emit if notice is not None else None
        qapp.aboutToQuit.connect(app.shutdown)
        announce = getattr(app, "announce_start", None)
        if announce is not None and not autostarted:
            announce()
        log.info("截图翻译已启动，数据目录 %s", home)
        return qapp.exec()
    finally:
        instance.release()
        reporter.uninstall()

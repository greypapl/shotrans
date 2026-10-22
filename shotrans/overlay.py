"""译文浮窗：原位显示、整体对照、悬停看原文、复制、钉住、单段重翻。

窗口比截图多出一条工具栏（默认在上方，贴屏幕顶时放下方），只在鼠标进入时显示。

线程规则（关窗时绝不能崩）：后台线程跑识别和翻译，但**不碰任何 Qt 对象，也不持有窗口本身**——
开线程前把要用的东西（图片、识别函数、路由、收件箱队列、代际号）取成普通变量交给线程，
线程只往 queue.Queue 里放 (种类, 代际, 内容) 元组。界面线程里的定时器每 30 毫秒取一次、分发给 _on_* 处理。
关窗时定时器停掉；之后线程再往队列里放东西没人读，无害。
（旧设计用挂在窗口上的 QObject 发信号，且线程闭包捕获了 self：窗口在主线程释放、
或窗口最后一个引用在工作线程里被释放时，整个进程会 access violation。）"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QContextMenuEvent, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QToolTip, QWidget

from . import i18n
from .i18n import N_, t
from .qtimage import pil_to_qimage
from .session import Session
from .snapshot import build_snapshot, write_snapshot

log = logging.getLogger(__name__)

BAR_H = 28
BAR_MIN_W = 460
STATUS = {
    "pending": (N_("status.pending"), "#9ca3af"),
    "empty": (N_("status.empty"), "#9ca3af"),
    "skipped": (N_("status.skipped"), "#9ca3af"),
    "vllm": (N_("status.vllm"), "#22c55e"),
    "fallback": (N_("status.fallback"), "#3b82f6"),
    "tencent": (N_("status.tencent"), "#a855f7"),
    "none": (N_("status.none"), "#ef4444"),
}
OCR_LANG_KEYS = {"en-US": N_("lang.en-US"), "zh-Hans-CN": N_("lang.zh-Hans-CN")}
BUTTONS = [  # (文字键, 槽函数名, 可勾选)
    (N_("bar.toggle_view"), "toggle_view", False),
    (N_("bar.toggle_mode"), "toggle_mode", False),
    (N_("bar.flip"), "flip_direction", False),
    (N_("bar.copy_translation"), "copy_translation", False),
    (N_("bar.copy_original"), "copy_original", False),
    (N_("bar.export"), "export_snapshot", False),
]


def place_window(rect: QRect, screen: QRect) -> tuple[QRect, QRect, int]:
    """算浮窗摆放：返回（窗口全局几何, 截图在窗口内的位置, 工具栏在窗口内的 y）。

    截图必须正好盖在原选区上，只能挪工具栏：
    - 竖直：上方放得下放上方；否则下方放得下放下方；都放不下（选区几乎满屏高）就叠在截图顶部。
    - 水平：窗口至少 BAR_MIN_W 宽，默认从选区左边往右伸；右边超出屏幕就改为往左伸（不越过屏幕左边）。"""
    width = max(rect.width(), BAR_MIN_W)
    screen_right = screen.left() + screen.width()
    screen_bottom = screen.top() + screen.height()
    left = rect.left()
    if left + width > screen_right:
        # 只会往左挪（两项都小于 rect.left()）；选区在屏幕内，往左伸后仍能完整装下截图
        left = max(screen.left(), screen_right - width)
    if rect.top() - BAR_H >= screen.top():
        top, img_y, bar_y, height = rect.top() - BAR_H, BAR_H, 0, rect.height() + BAR_H
    elif rect.top() + rect.height() + BAR_H <= screen_bottom:
        top, img_y, bar_y, height = rect.top(), 0, rect.height(), rect.height() + BAR_H
    else:
        top, img_y, bar_y, height = rect.top(), 0, 0, rect.height()
    img = QRect(rect.left() - left, img_y, rect.width(), rect.height())
    return QRect(left, top, width, height), img, bar_y


OCR_GEN = -1  # 识别阶段的消息用这个代际（不属于任何翻译代际）
POLL_MS = 30  # 界面线程取队列的间隔
RENDER_MIN_S = 0.12  # 两次整图重画之间至少隔这么久；期间到的结果只记进状态，到点补一次（最终状态一定会画出来）


class OverlayWindow(QWidget):
    closed = Signal(object)

    def __init__(self, session: Session, rect: QRect, ocr_fn: Callable, router, history=None,
                 open_window: Callable[[str, str | None], object] | None = None):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.session = session
        self._ocr_fn = ocr_fn
        self._router = router
        self._history = history
        self._open_window = open_window  # 打开设置/自检窗口（T9 状态栏链接用；这里只存）
        self.pinned = False
        self._cancel = threading.Event()
        self._press_global: QPoint | None = None
        self._press_window_pos: QPoint | None = None
        self._moved = False
        self._menu_open = False
        self._exporting = False             # 导出在后台进行中：防止连点写两份
        self._error: str | None = None      # 翻译出错：随下一次翻译清除
        self._ocr_error: str | None = None  # 识别失败：一直保留
        self._status_key = N_("status.ocr_running")
        self._ocr_missing: tuple[str, ...] = ()  # 没装的 OCR 语言包（只装了一个时状态栏注明）
        self._buttons: list[tuple[QToolButton, str]] = []  # (按钮, 文字键)：切换语言时重设文字
        self._hover_pos = QPoint()
        self._dirty = False                 # 有结果已记进 session、还没画到屏幕上
        self._last_render = float("-inf")   # 上次整图重画的时刻（time.monotonic）
        self._render_timer = QTimer(self)   # 间隔没到时的补画定时器
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._flush)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(500)
        self._hover_timer.timeout.connect(self._show_hover)

        # 工作线程 -> 界面线程的唯一通道：纯 Python 队列，线程只 put，不碰任何 Qt 对象
        self._inbox: queue.Queue = queue.Queue()
        self._pump = QTimer(self)
        self._pump.setInterval(POLL_MS)
        self._pump.timeout.connect(self._drain)
        self._pump.start()

        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        win, self._img_rect, bar_y = place_window(rect, screen.geometry())
        self.setGeometry(win)
        self._scale = session.image.width / max(rect.width(), 1)
        self._build_bar(bar_y, win.width())
        self._refresh()
        self._start_ocr()
        i18n.hub().language_changed.connect(self._retranslate_ui)

    # ---------- 工具栏 ----------
    def _build_bar(self, y: int, width: int) -> None:
        self._bar = QWidget(self)
        self._bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._bar.setGeometry(0, y, width, BAR_H)
        self._bar.setStyleSheet(
            "QWidget{background:#1f2937;color:white;border-radius:4px}"
            "QToolButton{border:none;padding:2px 6px;color:white}"
            "QToolButton:hover{background:#374151} QToolButton:checked{background:#2563eb}")
        lay = QHBoxLayout(self._bar)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(2)
        self._status = QLabel(self._bar)
        self._status.setTextFormat(Qt.TextFormat.RichText)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._status.linkActivated.connect(self._on_status_link)
        lay.addWidget(self._status)

        def btn(text: str, slot, checkable: bool = False) -> QToolButton:
            b = QToolButton(self._bar)
            b.setText(text)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        for key, slot, checkable in BUTTONS:
            b = btn(t(key), getattr(self, slot), checkable)
            self._buttons.append((b, key))
            if key == "bar.export":
                b.setToolTip(t("bar.export_tip"))
        btn("📌", self._on_pin, checkable=True)
        btn("×", self.close)
        lay.addStretch(1)
        self._bar.hide()

    def _flush(self) -> None:
        """把攒着的结果画出来：离上次重画不到 RENDER_MIN_S 就等到点再画（定时器已在等就不重复排）。"""
        if not self._dirty:
            return
        self._update_status()   # 状态文字很便宜，不节流；贵的只有整图重画
        wait = self._last_render + RENDER_MIN_S - time.monotonic()
        if wait > 0:
            if not self._render_timer.isActive():
                self._render_timer.start(int(wait * 1000) + 1)
            return
        self._refresh()

    def _refresh(self) -> None:
        self._dirty = False
        self._render_timer.stop()
        self._pixmap = QPixmap.fromImage(pil_to_qimage(self.session.display_image()))
        # 画完才打点：大图重画本身就超过 RENDER_MIN_S 时，在画之前打点等于没节流（紧接着到的结果又立即重画）
        self._last_render = time.monotonic()
        self._update_status()
        self.update()

    def _retranslate_ui(self, *_) -> None:
        """界面语言切换后：工具栏按钮和状态栏换成新语言。（_retranslate 是单段重翻，别混淆。）"""
        for b, key in self._buttons:
            b.setText(t(key))
            if key == "bar.export":
                b.setToolTip(t("bar.export_tip"))
        self._update_status()

    def _update_status(self) -> None:
        status = None
        if self._ocr_error is not None:
            text, color = t("status.ocr_failed", error=self._ocr_error), "#ef4444"
        elif self._error is not None:
            text, color = t("status.translate_failed", error=self._error), "#ef4444"
        else:
            status = self.session.service_status()
            key, color = STATUS[status]
            text = t(self._status_key) if status == "pending" else t(key)
            if status in ("fallback", "tencent") and self._router is not None and self._router.vllm_down():
                # 主服务没配置时 vllm_down() 恒为 False，不会显示掉线附注
                text += t("status.primary_auth") if self._vllm_auth_failed() else t("status.primary_down")
            elif status == "none" and self._vllm_auth_failed():
                text = t("status.none_auth")
            if self._ocr_missing:
                langs = t("list.sep").join(t(OCR_LANG_KEYS[tag]) if tag in OCR_LANG_KEYS else tag
                                           for tag in self._ocr_missing)
                text += t("status.ocr_partial", langs=langs)
        view = t("status.view_original") if self.session.show_original else t("status.view_translation")
        links = ""
        if self._open_window is not None:
            if self._ocr_error is not None:
                links = f' <a href="doctor">{t("status.link_doctor")}</a>'
            elif self._error is None and (
                    status == "none" or (status in ("fallback", "tencent") and self._vllm_auth_failed())):
                links = f' <a href="settings">{t("status.link_settings")}</a>'
        self._status.setText(f"<span style='color:{color}'>●</span> {text} · {view}{links}")

    def _on_status_link(self, href: str) -> None:
        if self._open_window is None:
            return
        if href == "settings":
            self._open_window("settings", "service")
        elif href == "doctor":
            self._open_window("doctor", None)

    def _vllm_auth_failed(self) -> bool:
        """vLLM 最近一次熔断是因为 key 不对（401/403），而不是连不上。"""
        return self._router is not None and self._router.vllm_last_error_kind() == "auth"

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self._img_rect, self._pixmap)
        p.setPen(QPen(QColor("#3b82f6"), 1))
        p.drawRect(self._img_rect.adjusted(0, 0, -1, -1))

    # ---------- 后台：识别、翻译 ----------
    # 线程函数都在模块级（见文件末尾），参数只有普通值和队列，拿不到 self。
    def _start_ocr(self) -> None:
        threading.Thread(target=_ocr_worker, args=(self._ocr_fn, self.session.image, self._inbox),
                         daemon=True).start()

    def _drain(self) -> None:
        """界面线程（定时器）调用：把队列里已到的消息全部取出，按种类交给处理函数。代际原样传下去，过期判断在处理函数里。"""
        while True:
            try:
                kind, gen, payload = self._inbox.get_nowait()
            except queue.Empty:
                break
            if kind == "lines":
                self._ocr_missing = tuple(getattr(payload, "missing", ()))
                self._on_lines(payload)
            elif kind == "result":
                self._on_result(gen, payload)
            elif kind == "finished":
                self._on_finished(gen)
            elif kind == "failed":
                self._on_failed(gen, payload)
            elif kind == "exported":
                self._exporting = False
                self._toast(t("toast.exported", name=payload))
            elif kind == "export_failed":
                self._exporting = False
                self._toast(t("toast.export_failed", error=payload))
            elif kind == "retranslated":
                self._on_retranslated((gen, payload))
            else:
                log.warning("未知的后台消息 %r", kind)
        # 结果只记进状态、置脏标记，整张图（几百毫秒）在一次取队列结束后只重画一次
        self._flush()

    def _on_lines(self, lines) -> None:
        self.session.set_lines(lines)
        self._status_key = N_("status.pending")
        self._start_translate()

    def _start_translate(self) -> None:
        self._cancel.set()
        self._cancel = cancel = threading.Event()
        self._error = None
        gen = self.session.generation
        segs = list(self.session.segments)
        direction = self.session.direction
        self._refresh()
        if not any(not s.skip for s in segs):
            return
        threading.Thread(target=_translate_worker, args=(self._router, segs, direction, gen, cancel, self._inbox),
                         daemon=True).start()

    def _on_result(self, gen: int, result) -> None:
        if self.session.apply(gen, result):
            self._dirty = True

    def _on_finished(self, gen: int) -> None:
        if gen != self.session.generation or self._history is None:
            return
        # 要存图：在界面线程里取两张 PIL 副本（原图、译文图）；PNG 编码和写盘交给后台线程
        images = None
        if getattr(self._history, "wants_images", False):
            try:
                images = (self.session.image.copy(), self.session.rendered_image().copy())
            except Exception:
                log.exception("历史截图准备失败，这次只存文字")
        try:
            if images is None:
                self._history.add(self.session.history_entry())
                return
            image_id = self._history.add(self.session.history_entry(), with_images=True)
        except Exception:
            log.exception("写历史失败")
            return
        if image_id:
            threading.Thread(target=_history_images_worker, args=(self._history.write_images, image_id, *images),
                             daemon=True).start()

    def _on_failed(self, gen: int, msg: str) -> None:
        if gen == OCR_GEN:
            self._ocr_error = msg
        elif gen == self.session.generation:
            self._error = msg
        else:
            return  # 过期代际的翻译出错，丢弃
        self._refresh()

    # ---------- 操作 ----------
    def toggle_view(self) -> None:
        self.session.toggle_view()
        self._refresh()

    def toggle_mode(self) -> None:
        self.session.set_mode("line" if self.session.mode == "para" else "para")
        self._start_translate()

    def flip_direction(self) -> None:
        self.session.flip_direction()
        self._start_translate()

    def copy_translation(self) -> None:
        QGuiApplication.clipboard().setText(self.session.all_translation())
        self._toast(t("toast.copied_translation"))

    def copy_original(self) -> None:
        QGuiApplication.clipboard().setText(self.session.all_original())
        self._toast(t("toast.copied_original"))

    def export_snapshot(self) -> None:
        """界面线程只把状态取成普通数据（PIL 图片 + dict），写文件交给后台线程。"""
        if self._exporting:
            self._toast(t("toast.exporting"))
            return
        try:
            snap = build_snapshot(self.session)
        except Exception as e:
            log.exception("导出调试快照失败")
            self._toast(t("toast.export_failed", error=e))
            return
        self._exporting = True
        threading.Thread(target=_export_worker, args=(snap, self._inbox), daemon=True).start()

    def _on_pin(self, checked: bool) -> None:
        self.pinned = checked

    def _toast(self, text: str) -> None:
        QToolTip.showText(self.mapToGlobal(self._img_rect.center()), text, self)

    def _retranslate(self, seg) -> None:
        gen, direction = self.session.generation, self.session.direction
        r = self.session.results.get(seg.idx)
        current = r.service if r else "none"
        self._toast(t("toast.retranslating"))
        threading.Thread(target=_retranslate_worker, args=(self._router, seg, direction, current, gen, self._inbox),
                         daemon=True).start()

    def _on_retranslated(self, payload) -> None:
        gen, res = payload
        if gen != self.session.generation:
            return
        if res is None:
            self._toast(t("toast.retranslate_none"))
            return
        self.session.apply_retranslation(res)
        self._refresh()

    # ---------- 键盘 ----------
    def keyPressEvent(self, e) -> None:
        k, mods = e.key(), e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if k == Qt.Key.Key_Escape:
            self.close()
        elif k == Qt.Key.Key_Space:
            self.toggle_view()
        elif k == Qt.Key.Key_D and not ctrl:
            self.flip_direction()
        elif k == Qt.Key.Key_C and ctrl:
            self.copy_original() if shift else self.copy_translation()
        else:
            super().keyPressEvent(e)

    # ---------- 鼠标 ----------
    def _segment_at(self, pos: QPoint):
        # 这个 contains 与 session.segment_at 的范围判断重复（等价变异 M22），留作双重保险。
        if not self._img_rect.contains(pos):
            return None
        return self.session.segment_at((pos.x() - self._img_rect.x()) * self._scale,
                                       (pos.y() - self._img_rect.y()) * self._scale)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._press_window_pos = self.pos()
            self._moved = False

    def mouseMoveEvent(self, e) -> None:
        if self._press_global is not None and e.buttons() & Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() > 3:
                self._moved = True
            if self._moved:
                self.move(self._press_window_pos + delta)
            return
        self._hover_pos = e.position().toPoint()
        QToolTip.hideText()
        self._hover_timer.start()

    def mouseReleaseEvent(self, e) -> None:
        self._press_global = None

    def mouseDoubleClickEvent(self, e) -> None:
        seg = self._segment_at(e.position().toPoint())
        if seg is not None:
            QGuiApplication.clipboard().setText(self.session.translation_of(seg))
            self._toast(t("toast.copied"))

    def _show_hover(self) -> None:
        seg = self._segment_at(self._hover_pos)
        if seg is not None:
            text = self.session.hover_text(seg)
            if text:
                QToolTip.showText(self.mapToGlobal(self._hover_pos), text, self)

    def contextMenuEvent(self, e) -> None:
        if self._bar.geometry().contains(e.pos()):
            # 工具栏/状态栏上的右键（按钮不接右键，冒泡到这里）：什么都不做。
            # 选区几乎满屏高时工具栏叠在截图顶上，位置也在 _img_rect 里，所以要先按工具栏判断
            return
        seg = self._segment_at(e.pos())
        if seg is None or seg.skip:
            # 鼠标右键点在截图里面、但没有可翻译段的地方（空白、公式之类跳过的段）= 立即关窗，钉住也关；
            # 截图外面（工具栏、状态栏、截图右侧多出的窗口宽度）不管；键盘菜单键不关（位置不是用户点的）
            if e.reason() == QContextMenuEvent.Reason.Mouse and self._img_rect.contains(e.pos()):
                self.close()
            return
        menu = QMenu(self)
        a_re = menu.addAction(t("menu.retranslate"))
        a_undo = menu.addAction(t("menu.undo"))
        a_undo.setEnabled(self.session.can_undo(seg.idx))
        menu.addSeparator()
        a_src = menu.addAction(t("menu.copy_original"))
        a_dst = menu.addAction(t("menu.copy_translation"))
        self._menu_open = True
        try:
            chosen = menu.exec(e.globalPos())
        finally:
            self._menu_open = False
        if chosen is a_re:
            self._retranslate(seg)
        elif chosen is a_undo:
            self.session.undo_retranslation(seg.idx)
            self._refresh()
        elif chosen is a_src:
            QGuiApplication.clipboard().setText(seg.text)
        elif chosen is a_dst:
            QGuiApplication.clipboard().setText(self.session.translation_of(seg))

    def enterEvent(self, e) -> None:
        self._bar.show()

    def leaveEvent(self, e) -> None:
        self._bar.hide()
        self._hover_timer.stop()

    # ---------- 生命周期 ----------
    def changeEvent(self, e) -> None:
        # 这里的 isActiveWindow 与 _close_if_inactive 里的重复（等价变异 M40），留着可少排一个无谓的定时器。
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            QTimer.singleShot(0, self._close_if_inactive)
        super().changeEvent(e)

    def _close_if_inactive(self) -> None:
        # 被藏起来（截屏期间）时失焦不算「点到外面」：框选取消后它还要放回来
        if not self.isVisible():
            return
        if not self.isActiveWindow() and not self.pinned and not self._menu_open:
            self.close()

    def closeEvent(self, e) -> None:
        self._cancel.set()
        self._render_timer.stop()
        self._pump.stop()  # 不再取队列：之后线程放进来的消息没人读，随队列一起被回收
        self._hover_timer.stop()
        self.closed.emit(self)
        super().closeEvent(e)


# ---------- 工作线程函数 ----------
# 规则：只用参数里的普通值、路由/识别函数和队列；不碰任何 Qt 对象，不持有窗口。
def _ocr_worker(ocr_fn: Callable, image, inbox: queue.Queue) -> None:
    try:
        lines = ocr_fn(image)
    except Exception as e:
        log.exception("识别失败")
        inbox.put(("failed", OCR_GEN, str(e)))  # 原始错误文字；前缀由界面线程按当前语言加
        return
    inbox.put(("lines", OCR_GEN, lines))


def _translate_worker(router, segs, direction: str, gen: int, cancel: threading.Event, inbox: queue.Queue) -> None:
    try:
        router.translate(segs, direction, lambda r: inbox.put(("result", gen, r)), cancel)
    except Exception as e:
        log.exception("翻译出错")
        if not cancel.is_set():
            inbox.put(("failed", gen, str(e)))
        return
    if not cancel.is_set():
        inbox.put(("finished", gen, None))


def _export_worker(snap, inbox: queue.Queue) -> None:
    try:
        folder = write_snapshot(snap)
    except Exception as e:
        log.exception("导出调试快照失败")
        inbox.put(("export_failed", 0, str(e)))
        return
    log.info("调试快照已导出：%s", folder)
    inbox.put(("exported", 0, folder.name))


def _history_images_worker(write: Callable, image_id: str, original, rendered) -> None:
    """write 是 History.write_images（纯 Python、自带锁、绝不抛）；图是界面线程里取好的 PIL 副本。
    不经队列回到界面线程：浮窗关了（定时器停了）也照样写完、绑到记录上。"""
    try:
        write(image_id, original, rendered)
    except Exception:  # write_images 自己不抛；替身或将来改动万一抛了，也只记日志
        log.exception("历史截图保存失败")


def _retranslate_worker(router, seg, direction: str, current: str, gen: int, inbox: queue.Queue) -> None:
    try:
        res = router.retranslate_one(seg, direction, current)
    except Exception:
        log.exception("重翻出错")
        res = None
    inbox.put(("retranslated", gen, res))

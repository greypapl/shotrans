"""历史记录：最近 N 条原文和译文，存 JSON；可选每条连同两张截图（原图、译文图，PNG）一起存。

图片放在 images_dir（数据目录下的 history_images\\），文件名 <image_id>-original.png / <image_id>-rendered.png：
- add(entry, with_images=True) 当场写文字记录，并在记录里写下新编号 "image_id"；
- write_images(image_id, 原图, 译文图) 在后台线程里写 PNG，两张都写成功后才在记录上补 "images"（文件名）。
  写失败只记日志，文字记录不受影响；那条记录已被替换/删除/挤出时，刚写的图直接删掉。
- 每次写历史（添加、删除、清空）之后清理 images_dir：没有记录引用、也不在后台写入中的文件一律删掉。
  所以超出条数被挤掉、删除、清空、同一张截图重翻替换掉的记录，图片都会跟着删除；残留的孤儿文件也会被清掉。
- 清理只对**读成功**的历史列表做。history.json 坏了（改名为 .bad-时间 备份）或不见了时，文件夹里现有的图
  一张都不删，而是挪进 images_dir 里的 .kept-时间 子文件夹（清理从不碰子文件夹），和 .bad 备份一起留着救。
  挪不动的话，本次运行里干脆不再清理。批量删图只有 clear()（用户确认过的「清空」）。
没开 save_images（或没给 images_dir）时只存文字，和旧版本一样。"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

IMAGE_KINDS = ("original", "rendered")


def _safe_name(name) -> bool:
    """记录里的图片文件名只能是 images_dir 里的一个文件名：不许带目录、盘符、..（history.json 可能被手改）。"""
    return isinstance(name, str) and bool(name) and Path(name).name == name and name not in (".", "..")


def image_names(image_id: str) -> dict[str, str]:
    return {kind: f"{image_id}-{kind}.png" for kind in IMAGE_KINDS}


class History:
    def __init__(self, path: Path, limit: int = 200, images_dir: Path | None = None, save_images: bool = True):
        self.path = path
        self.limit = limit
        self.images_dir = images_dir
        self.save_images = save_images
        self._lock = threading.Lock()
        self._pending: set[str] = set()  # 图片还在后台写的 image_id：清理时不许当孤儿删
        self._cleanup_blocked = False    # 保护性挪图失败过：本次运行不再清理，宁可多留文件
        self._logged_errors: set[str] = set()  # 同一种写图错误只记一次完整堆栈，免得冲掉日志

    @property
    def wants_images(self) -> bool:
        return self.save_images and self.images_dir is not None

    def _backup_bad(self) -> None:
        stamp = time.strftime('%Y%m%d-%H%M%S')
        bad = self.path.with_name(f"{self.path.name}.bad-{stamp}")
        os.replace(self.path, bad)
        log.warning("历史文件损坏，已改名备份为 %s", bad.name)
        self._keep_images(stamp)

    def _keep_images(self, stamp: str) -> None:
        """历史列表读不出来（坏了/不见了）：现有的图不能当孤儿删，挪进 .kept-<stamp> 子文件夹留着。"""
        d = self.images_dir
        if d is None:
            return
        try:
            files = [f for f in d.iterdir() if f.is_file() and f.name.partition("-")[0] not in self._pending]
        except FileNotFoundError:
            return
        except OSError:
            log.warning("读不了历史截图文件夹 %s，本次运行不再清理历史截图", d, exc_info=True)
            self._cleanup_blocked = True
            return
        if not files:
            return
        dest = d / f".kept-{stamp}"
        try:
            dest.mkdir(exist_ok=True)
            for f in files:
                _move(f, dest / f.name)
        except OSError:
            log.warning("历史截图挪不进 %s，本次运行不再清理历史截图", dest.name, exc_info=True)
            self._cleanup_blocked = True
            return
        log.warning("历史记录读不出来，原有的 %d 张截图已挪进 %s（没有删除）", len(files), dest)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._backup_bad()
            return []
        if not isinstance(data, list):
            self._backup_bad()
            return []
        return data

    def add(self, entry: dict, with_images: bool = False) -> str | None:
        """追加一条。entry 带 "session" 时，同一张截图（同一 session）的旧条目先删掉：
        浮窗里切 行/段、中⇄英 会重翻多次，历史里只留最新那次。
        with_images 且 wants_images 时返回新的 image_id（随后用 write_images 把图写上）；否则返回 None。"""
        image_id = None
        if with_images and self.wants_images:
            image_id = time.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:12]  # 不含「-」：文件名按第一个「-」取编号
            entry = {**entry, "image_id": image_id}
        with self._lock:
            existed = self.path.exists()
            items = self.load()
            sid = entry.get("session")
            if sid is not None:
                items = [e for e in items if not (isinstance(e, dict) and e.get("session") == sid)]
            items = (items + [entry])[-self.limit:]
            self._write(items)
            if image_id is not None:
                self._pending.add(image_id)
            if existed:
                self._cleanup(items)
            else:  # history.json 不见了：空列表不等于「图都没人要」
                self._keep_images(time.strftime('%Y%m%d-%H%M%S'))
        return image_id

    def _write(self, items: list) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _index_of(items: list, target: dict) -> int | None:
        """target 在 items 里的位置。带 session 的记录按（session, time）认：后台写完图会给记录补 "images"，
        窗口手里早先读到的副本和文件里的不再相等，但仍是同一条（同一 session 重翻后的新记录 time 不同，不算）。
        不带 session 的旧记录按整条相等认。"""
        sid = target.get("session")
        if sid is not None:
            for i, e in enumerate(items):
                if isinstance(e, dict) and e.get("session") == sid and e.get("time") == target.get("time"):
                    return i
            return None
        try:
            return items.index(target)
        except ValueError:
            return None

    def find(self, target: dict) -> dict | None:
        """文件里 target 的当前版本（可能已补上 "images"）；没有了返回 None。"""
        with self._lock:
            items = self.load()
            i = self._index_of(items, target)
            return items[i] if i is not None else None

    def images_pending(self, entry) -> bool:
        """这条记录的图还在后台写（有 image_id、还没有 images、写入没结束）。"""
        iid = entry.get("image_id") if isinstance(entry, dict) else None
        with self._lock:
            return isinstance(iid, str) and iid in self._pending

    def delete(self, target) -> bool:
        """删一条：target 是 load() 列表里的下标（旧→新），或一条记录本身（见 _index_of）。找不到返回 False。"""
        with self._lock:
            items = self.load()
            if isinstance(target, dict):
                i = self._index_of(items, target)
                if i is None:
                    return False
                del items[i]
            elif isinstance(target, int) and not isinstance(target, bool) and 0 <= target < len(items):
                del items[target]
            else:
                return False
            self._write(items)
            self._cleanup(items)
            return True

    def clear(self) -> None:
        with self._lock:
            self._write([])
            self._cleanup([])

    # ---------- 图片 ----------
    def _cleanup(self, items: list) -> None:
        """在锁里、写完 history.json 之后调用：删掉 images_dir 里没有记录引用、也不在写入中的文件。出错只记日志。"""
        d = self.images_dir
        if d is None or self._cleanup_blocked:
            return
        keep: set[str] = set()
        for e in items:
            imgs = e.get("images") if isinstance(e, dict) else None
            if isinstance(imgs, dict):
                keep.update(v for v in imgs.values() if isinstance(v, str))  # 手改坏的值（列表之类）不能拿来比
        try:
            children = list(d.iterdir())
        except FileNotFoundError:
            return
        except OSError:
            if "cleanup-dir" not in self._logged_errors:  # 文件夹坏了每次写历史都会碰到：只记一次
                self._logged_errors.add("cleanup-dir")
                log.warning("读不了历史截图文件夹 %s，这次不清理", d, exc_info=True)
            return
        for f in children:
            if f.name in keep or f.name.partition("-")[0] in self._pending:
                continue
            try:
                if f.is_file():
                    f.unlink()
            except OSError:
                log.warning("删不掉历史截图 %s", f.name, exc_info=True)

    def write_images(self, image_id: str, original, rendered) -> bool:
        """后台线程调用：把两张 PIL 图写成 PNG，再绑到 image_id 那条记录上。成功返回 True；绝不抛异常。"""
        names = image_names(image_id)
        written: list[Path] = []
        try:
            try:
                self.images_dir.mkdir(parents=True, exist_ok=True)
                for kind, img in (("original", original), ("rendered", rendered)):
                    final = self.images_dir / names[kind]
                    tmp = final.with_name(final.name + ".tmp")
                    written.append(tmp)
                    img.save(tmp, format="PNG")
                    os.replace(tmp, final)
                    written[-1] = final
            except Exception as e:
                self._log_failure(e)
                _remove(written)
                return False
            with self._lock:
                items = self.load()
                for e in items:
                    if isinstance(e, dict) and e.get("image_id") == image_id:
                        e["images"] = names
                        self._write(items)
                        return True
                _remove(written)  # 记录已被重翻替换、删除或挤出：图没人要了
                return False
        except Exception as e:  # 绑图时写 history.json 失败之类：记录没绑上，刚写的图也不留
            self._log_failure(e)
            _remove(written)
            return False
        finally:
            with self._lock:
                self._pending.discard(image_id)

    def _log_failure(self, e: Exception) -> None:
        """同一种错误（类型 + 文字）第一次记完整堆栈，之后只记一行：数据目录不可写时每次翻译都会失败。"""
        sig = f"{type(e).__name__}: {e}"
        if sig in self._logged_errors:
            log.warning("历史截图保存失败（文字记录不受影响）：%s", sig)
            return
        self._logged_errors.add(sig)
        log.exception("历史截图保存失败（文字记录不受影响）")

    def image_path(self, entry, kind: str) -> Path | None:
        """这条记录的某张图（"original" / "rendered"）；没存图、文件不在、文件名不合法时返回 None。"""
        imgs = entry.get("images") if isinstance(entry, dict) else None
        name = imgs.get(kind) if isinstance(imgs, dict) else None
        if self.images_dir is None or not _safe_name(name):
            return None
        p = self.images_dir / name
        return p if p.is_file() else None


def _move(src: Path, dst: Path) -> None:
    os.replace(src, dst)


def _remove(files: list[Path]) -> None:
    for f in files:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            log.warning("删不掉历史截图 %s", f.name, exc_info=True)

"""旧数据迁移：数据目录还没有 config.toml、程序目录有旧的 config.local.toml 时，
先复制术语表.txt、history.json（目标已存在就不覆盖；先写 .tmp 再替换），最后才写 config.toml（key 加密）。
config.toml 是「迁移完成」的标记：它在不在是判断要不要迁移的唯一依据，所以必须最后写——
复制失败或写配置失败时，下次启动还会再试。
旧配置读不了时不迁移配置（由调用方提示并走引导），但术语表、历史和配置坏没坏无关，照样复制。
旧文件原样保留，不改不删。"""
from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config import ConfigError, config_from_raw, write_config
from .i18n import t
from .paths import config_path

log = logging.getLogger(__name__)

LEGACY_NAME = "config.local.toml"
COPY_NAMES = ("术语表.txt", "history.json")


@dataclass(frozen=True)
class MigrateResult:
    status: str  # migrated / skipped / failed
    reason: str = ""
    copied: tuple[str, ...] = ()
    failed_copies: tuple[str, ...] = ()  # 旧目录里有、却没复制成功的文件
    legacy: str = field(default="", compare=False)  # 旧文件所在目录（提示用户去哪里找）


def needs_migration(home: Path, legacy: Path) -> bool:
    return not config_path(home).exists() and (legacy / LEGACY_NAME).exists()


def _copy_atomic(src: Path, dst: Path) -> None:
    """先写 dst.tmp 再替换：复制到一半失败不会留下半个文件，也不会让下次误以为已经复制过。"""
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        shutil.copyfile(src, tmp)  # 只复制内容：copy2 会连「只读」属性一起带过来，之后保存就失败
        os.replace(tmp, dst)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _copy_data(home: Path, legacy: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    copied, failed = [], []
    for name in COPY_NAMES:
        s, d = legacy / name, home / name
        if s.exists() and not d.exists():
            try:
                home.mkdir(parents=True, exist_ok=True)
                _copy_atomic(s, d)
                copied.append(name)
            except OSError:
                log.exception("迁移时复制 %s 失败", name)
                failed.append(name)
    return tuple(copied), tuple(failed)


def _read_legacy(src: Path):
    """读旧配置并转成新格式；读不了时返回 (None, 原因)。原因里不会有 key（config 只报类型不回显）。"""
    try:
        return config_from_raw(tomllib.loads(src.read_text(encoding="utf-8-sig"))), ""
    except tomllib.TOMLDecodeError as e:
        return None, t("migrate.err.syntax", error=e)
    except UnicodeDecodeError:
        return None, t("migrate.err.not_utf8")
    except (OSError, ConfigError) as e:
        return None, str(e)


def migrate_legacy(home: Path, legacy: Path, autostart_on: bool | None = None) -> MigrateResult:
    if config_path(home).exists():
        return MigrateResult("skipped", t("migrate.skip.exists"))
    src = legacy / LEGACY_NAME
    if not src.exists():
        return MigrateResult("skipped", t("migrate.skip.none"))
    c, reason = _read_legacy(src)
    copied, failed = _copy_data(home, legacy)  # 先复制数据，config.toml 最后写
    if c is not None:
        try:
            home.mkdir(parents=True, exist_ok=True)
            write_config(config_path(home), dataclasses.replace(c, autostart=autostart_on))
        except (OSError, ConfigError) as e:
            reason = str(e)
    if reason:
        log.warning("旧配置迁移失败：%s；复制了 %s，没复制成功 %s", reason, copied or "（无）", failed or "（无）")
        return MigrateResult("failed", reason, copied, failed, str(legacy))
    log.info("已把旧配置 %s 迁移到 %s，复制了 %s，没复制成功 %s", src, home, copied or "（无）", failed or "（无）")
    return MigrateResult("migrated", "", copied, failed, str(legacy))

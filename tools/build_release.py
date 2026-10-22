"""一条命令打出发布包：python -m tools.build_release [--skip-tests] [--legacy-config 路径]
步骤（spec §7）：全量测试 → 图标 → PyInstaller（PATH 只认构建解释器自己的 conda 环境）→ DLL 来源检查 → 复制使用说明/许可 → 干净环境 --selftest（含复制到带空格中文的路径再测）
→ 个人数据扫描 → 打 zip → 体积上限 → SHA-256。任何一步失败即停，退出码 1。
--skip-tests 只给调试打包规则时用；正式发布必须不带它。
--legacy-config：旧配置文件（默认 截图翻译/config.local.toml），其中每个长字符串值都成扫描规则；
文件不存在时打印一行警告，只用固定规则扫。只把路径交给扫描器，本脚本绝不打印其内容。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

import shotrans

from . import licenses, release_checks as rc

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP = DIST / "截图翻译"
EXE = APP / "截图翻译.exe"
ZIP = DIST / f"截图翻译-{shotrans.__version__}-win64.zip"
DROP_VARS = ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM", "QT_QPA_PLATFORM_PLUGIN_PATH",
             "SHOTRANS_LEGACY_DIR")
# 扫描自证：exe 里 PYZ 解开后必须出现这个条目，否则说明解包失效、扫描形同虚设
PYZ_PROOF = "PYZ!shotrans.app"
SELFTEST_TIMEOUT = 180


class BuildError(Exception):
    pass


def clean_env(base: dict[str, str], home: Path) -> dict[str, str]:
    """模拟别人的电脑：去掉 Python/Qt/conda 相关变量和 PATH 里的 conda 目录；数据目录指向 home。"""
    env = {k: v for k, v in base.items() if k not in DROP_VARS and not k.upper().startswith("CONDA")}
    parts = [p for p in base.get("PATH", "").split(";") if p and "conda" not in p.lower()]
    env["PATH"] = ";".join(parts)
    env["SHOTRANS_HOME"] = str(home)
    return env


def _under(entry: str, prefix: Path) -> bool:
    e = os.path.normcase(os.path.normpath(entry))
    p = os.path.normcase(os.path.normpath(str(prefix)))
    return e == p or e.startswith(p.rstrip("\\") + "\\")


def build_env(base: dict[str, str], prefix: Path) -> dict[str, str]:
    """跑 PyInstaller 用的环境：PATH 开头是构建解释器自己的 Library\\bin、环境根、DLLs，后面接原 PATH 并去掉
    其它 conda 目录（含 conda 字样、又不在 prefix 下的）。PyInstaller 按 PATH 找扩展模块依赖的 DLL；没激活环境时
    PATH 上只有 base conda 的 Library\\bin，就会把 base 的 OpenSSL 3.0 打进包里，和环境里按 3.5 编的 _ssl 对不上。"""
    env = dict(base)
    rest = [p for p in base.get("PATH", "").split(";")
            if p and ("conda" not in p.lower() or _under(p, prefix))]
    env["PATH"] = ";".join([str(d) for d in env_dll_dirs(prefix)] + rest)
    return env


def env_dll_dirs(prefix: Path) -> list[Path]:
    return [prefix / "Library" / "bin", prefix, prefix / "DLLs"]


def dll_provenance(internal: Path, sources: list[Path]) -> tuple[list[str], int]:
    """_internal 顶层每个 .dll：若构建环境（sources）里有同名文件，必须与其中某一份逐字节相同。
    返回（对不上的文件名，比对过的个数）。同名在环境里有多份（如 zlib.dll）时与任一份相同即可。"""
    bad, compared = [], 0
    for f in sorted(internal.iterdir(), key=lambda p: p.name.lower()):
        if f.suffix.lower() != ".dll" or not f.is_file():
            continue
        copies = [s / f.name for s in sources if (s / f.name).is_file()]
        if not copies:
            continue
        compared += 1
        data = f.read_bytes()
        if not any(c.read_bytes() == data for c in copies):
            bad.append(f.name)
    return bad, compared


def require_provenance(internal: Path, prefix: Path) -> int:
    bad, compared = dll_provenance(internal, env_dll_dirs(prefix))
    if bad:
        raise BuildError(f"包里这些 DLL 与构建环境 {prefix} 里的同名文件不一致（多半混进了别的 conda 环境）："
                         + "、".join(bad))
    if compared == 0:
        raise BuildError(f"{internal} 里没有一个 DLL 能与构建环境 {prefix} 比对：来源检查形同虚设")
    return compared


def _pyz_entries(pyz) -> Iterable[tuple[str, bytes]]:
    from PyInstaller.loader.pyimod01_archive import PYZ_ITEM_NSPKG
    for name, (typecode, _offset, _length) in pyz.toc.items():
        data = b"" if typecode == PYZ_ITEM_NSPKG else pyz.extract(name, raw=True)
        yield f"PYZ!{name}", data


def _iter_pyinstaller(p: Path) -> Iterable[tuple[str, bytes]]:
    """.pyz：直接按 PYZ 读。.exe：读 PE 后面附带的 CArchive（PKG），每条解压后给出；PYZ 类型的条目再逐模块解开。
    读不了就抛（扫描不能悄悄跳过）。"""
    from PyInstaller.archive.readers import PKG_ITEM_PYZ, CArchiveReader
    from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader
    if p.suffix.lower() == ".pyz":
        yield from _pyz_entries(ZlibArchiveReader(str(p), 0))
        return
    pkg = CArchiveReader(str(p))
    for name, entry in pkg.toc.items():
        yield f"PKG!{name}", pkg.extract(name)
        if entry[-1] == PKG_ITEM_PYZ:
            yield from _pyz_entries(pkg.open_embedded_archive(name))


def pyinstaller_extractor() -> rc.Extractor:
    """把 exe 里的 PKG 归档与其中的 PYZ 解开逐条扫（压缩过的内容直接扫字节是扫不到的）。
    用 PyInstaller.archive.readers.CArchiveReader（toc / extract / open_embedded_archive）与
    PyInstaller.loader.pyimod01_archive.ZlibArchiveReader（toc / extract(raw=True)）。"""
    return (lambda p: p.suffix.lower() in (".exe", ".pyz")), _iter_pyinstaller


def scan_package(app: Path, rules) -> tuple[list[rc.Hit], int, list[str]]:
    """扫整个包；额外返回 extractor 给出的全部条目名（供 require_pyz 自证）。不改 release_checks 的接口。"""
    match, it = pyinstaller_extractor()
    names: list[str] = []

    def recording(p: Path):
        for name, data in it(p):
            names.append(name)
            yield name, data

    hits, n = rc.scan_tree(app, rules, extractors=[(match, recording)])
    return hits, n, names


def require_pyz(names: Iterable[str]) -> None:
    if PYZ_PROOF not in set(names):
        raise BuildError(f"扫描没有解开 PYZ（条目里没有 {PYZ_PROOF}）：解包失效时扫描形同虚设")


def program_defaults() -> dict:
    """程序默认配置的表树（与 config.toml 同形）：旧配置里与它同一路径相等的值是公开默认值，不算个人数据。"""
    from shotrans.config import Config, config_to_dict
    return config_to_dict(Config())


def scan_rules(legacy_config: Path):
    if not legacy_config.exists():
        print(f"警告：旧配置 {legacy_config} 不存在，只用固定规则扫描")
        return rc.default_rules(None)
    return rc.default_rules(legacy_config, program_defaults())


def judge_selftest(report: dict, returncode: int) -> list[str]:
    """自测通过 = 退出码 0、顶层 ok、frozen 为真、至少一行检查、且没有任何一行 ok 为假。返回问题清单（空 = 通过）。"""
    problems = []
    if returncode != 0:
        problems.append(f"退出码 {returncode}")
    if report.get("ok") is not True:
        problems.append("结果 ok 不是 true")
    if report.get("frozen") is not True:
        problems.append("frozen 不是 true（跑的不是打包版？）")
    checks = report.get("checks") or []
    if not checks:
        problems.append("没有任何检查行")
    for c in checks:
        if c.get("ok") is not True:
            problems.append(f"{c.get('name')}: {c.get('detail')}")
    return problems


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m tools.build_release")
    ap.add_argument("--skip-tests", action="store_true", help="跳过全量测试（只用于调试打包规则）")
    ap.add_argument("--legacy-config", type=Path, default=ROOT / "config.local.toml",
                    help="旧配置文件，其中的长字符串值也作为扫描规则（默认 截图翻译/config.local.toml）")
    return ap.parse_args(argv)


def _step(title: str) -> None:
    print(f"\n== {title}", flush=True)


def _run(cmd: list[str], **kw) -> None:
    r = subprocess.run(cmd, cwd=ROOT, **kw)
    if r.returncode != 0:
        raise BuildError(f"命令失败（退出码 {r.returncode}）：{' '.join(cmd[:4])} …")


def _selftest(app_dir: Path, label: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="shotrans-build-") as tmp:
        out = Path(tmp) / "selftest.json"
        env = clean_env(dict(os.environ), Path(tmp) / "home")
        try:
            r = subprocess.run([str(app_dir / "截图翻译.exe"), "--selftest", str(out)], env=env, cwd=tmp,
                               timeout=SELFTEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise BuildError(f"自测（{label}）超过 {SELFTEST_TIMEOUT} 秒没结束") from None
        if not out.exists():
            raise BuildError(f"自测（{label}）没有写出结果文件，退出码 {r.returncode}")
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except ValueError as e:
            raise BuildError(f"自测（{label}）结果文件读不了：{e}") from None
        problems = judge_selftest(report, r.returncode)
        if problems:
            raise BuildError(f"自测（{label}）失败：\n" + "\n".join(f"- {p}" for p in problems))
        return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    py = sys.executable
    try:
        if args.skip_tests:
            print("警告：跳过了全量测试，这个包不能作为正式发布。")
        else:
            _step("1/8 全量测试")
            _run([py, "-m", "pytest", "-W", "error", "-q"])
        _step("2/8 图标 + PyInstaller")
        _run([py, "-m", "tools.make_icon"])
        shutil.rmtree(DIST, ignore_errors=True)
        shutil.rmtree(ROOT / "build", ignore_errors=True)
        _run([py, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "截图翻译.spec")],
             env=build_env(dict(os.environ), Path(sys.prefix)))
        if not EXE.exists():
            raise BuildError(f"PyInstaller 没有产出 {EXE}")
        _step("3/8 DLL 来源检查")
        n_dll = require_provenance(APP / "_internal", Path(sys.prefix))
        print(f"{n_dll} 个 DLL 与构建环境 {sys.prefix} 里的同名文件逐字节相同")
        _step("4/8 使用说明与许可证")
        for name in ("使用说明.txt", "LICENSE.txt", "用临时数据目录启动.bat"):
            shutil.copy2(ROOT / "packaging" / name, APP / name)
        shutil.copytree(ROOT / "licenses", APP / "licenses")
        licenses.collect(APP / "licenses")
        _step("5/8 干净环境自测")
        rep = _selftest(APP, "dist")
        print(f"自测通过：MSVCP140 = {rep['msvcp140']}")
        for c in rep["checks"]:
            print(f"  {c['name']}: {str(c['detail']).splitlines()[0] if c['detail'] else ''}")
        with tempfile.TemporaryDirectory(prefix="shotrans-path-") as tmp:
            moved = Path(tmp) / "试 用 目录" / "截图翻译"
            shutil.copytree(APP, moved)
            _selftest(moved, "带空格和中文的路径")
        print("带空格和中文的路径：自测通过")
        _step("6/8 个人数据扫描")
        hits, n, names = scan_package(APP, scan_rules(args.legacy_config))
        if hits:
            raise BuildError("包里发现个人数据（只列规则和位置）：\n" + rc.format_hits(hits))
        require_pyz(names)
        n_pyz = sum(1 for x in names if x.startswith("PYZ!"))
        print(f"扫描了 {n} 个文件/条目（其中 exe 内 PYZ 模块 {n_pyz} 个，含 {PYZ_PROOF}），没有命中")
        _step("7/8 打 zip + 体积上限")
        rc.make_zip(APP, ZIP, "截图翻译")
        msg = rc.check_size(ZIP, rc.LIMIT_BYTES, APP)
        if msg:
            raise BuildError(msg)
        print("最大的 10 个文件：")
        for p, s in rc.largest_files(APP, 10):
            print(f"  {s / 1048576:8.2f} MB  {p}")
        _step("8/8 SHA-256")
        digest = rc.sha256(ZIP)
        (ZIP.parent / (ZIP.name + ".sha256")).write_text(f"{digest}  {ZIP.name}\n", encoding="utf-8")
        print(f"{ZIP}\n体积 {ZIP.stat().st_size / 1048576:.1f} MB（{ZIP.stat().st_size} 字节）\nSHA-256 {digest}")
        return 0
    except BuildError as e:
        print(f"\n构建失败：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

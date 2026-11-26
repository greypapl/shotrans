# -*- mode: python ; coding: utf-8 -*-
# 截图翻译打包规则（PyInstaller 文件夹模式、无控制台）。构建入口：python -m tools.build_release
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from comtypes.client import GetModule

ROOT = Path(SPECPATH)
sys.path.insert(0, str(ROOT))
from tools.bundle_filter import EXCLUDES, keep  # noqa: E402

hidden = [m for m in collect_submodules("shotrans") if not m.endswith("__main__")]
# winrt.windows.* 是命名空间包，collect_submodules 只收到 winrt._winrt_* 扩展，程序实际导入的模块显式列出
hidden += collect_submodules("winrt")
hidden += ["winrt.windows.foundation", "winrt.windows.foundation.collections", "winrt.windows.globalization",
           "winrt.windows.graphics.imaging", "winrt.windows.media.ocr", "winrt.windows.storage.streams"]
# 动态生成的 Windows 类型库也打进包里，运行时无需在安装目录写入。
uia = GetModule("UIAutomationCore.dll")
hidden += [uia.__name__, uia.IUIAutomation.__module__]

a = Analysis(
    [str(ROOT / "run_shotrans.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "shotrans" / "assets" / "icon.ico"), "shotrans/assets")],
    hiddenimports=hidden,
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
a.binaries = [e for e in a.binaries if keep(e[0])]
a.datas = [e for e in a.datas if keep(e[0])]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="截图翻译", icon=str(ROOT / "shotrans" / "assets" / "icon.ico"),
          console=False, upx=False, version=None)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="截图翻译")

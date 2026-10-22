# 第三方组件与许可证

本文件夹随程序分发：LGPL-3.0.txt、GPL-3.0.txt 为 gnu.org 原文；third_party\ 为各组件安装包自带的许可文本（构建时由 tools/licenses.py 收集）；组件清单.txt 列出名称、版本、许可。

| 组件 | 许可证 | 官方许可证文本 |
|---|---|---|
| PySide6 / Qt | LGPL-3.0 | https://www.gnu.org/licenses/lgpl-3.0.html |
| Pillow | MIT-CMU | https://github.com/python-pillow/Pillow/blob/main/LICENSE |
| numpy | BSD-3-Clause | https://github.com/numpy/numpy/blob/main/LICENSE.txt |
| httpx | BSD-3-Clause | https://github.com/encode/httpx/blob/master/LICENSE.md |
| pywinrt (winrt-*) | MIT | https://github.com/pywinrt/pywinrt/blob/main/LICENSE |

## LGPL-3.0 说明（PySide6 / Qt）

PySide6 / Qt 以 LGPL-3.0 许可，程序以动态链接方式使用，你可以替换为自己编译的版本。打包版为 PyInstaller 文件夹模式：Qt 的 DLL 是 `_internal\PySide6\` 下的独立文件，可直接替换为你自行编译的同版本 Qt。

- LGPL-3.0 官方文本：https://www.gnu.org/licenses/lgpl-3.0.html
- GPL-3.0 官方文本（LGPL-3.0 以它为基础）：https://www.gnu.org/licenses/gpl-3.0.html
- 源码获取：https://download.qt.io/official_releases/QtForPython/ 与 https://code.qt.io/

"""界面文字表：每个模块定义 ZH、EN 两个 dict，键完全一致。由 shotrans.i18n 合并。

打包版（PyInstaller）里 pkgutil 扫不到这些模块，所以在这里显式登记；
tests/test_i18n.py 保证这个列表和目录里的文件一致。"""
MODULES = ("about", "core", "doctor", "glossary", "history", "settings", "wizard")

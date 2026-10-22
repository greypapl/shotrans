"""PyInstaller 的入口脚本（shotrans/__main__.py 用相对导入，不能直接当脚本）。源码运行仍用 python -m shotrans。"""
from shotrans.app import main

raise SystemExit(main())

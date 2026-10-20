# -*- coding: utf-8 -*-
"""
PyInstaller 打包入口（EXE 版启动入口）

开发时也可以直接运行： python tools\exe_entry.py
打包后由 build_exe.py 调用，生成「校友邦周志助手.exe」。
"""
import sys
from pathlib import Path


def _prepare_sys_path():
    """源码模式：把 xyb/ 加入 sys.path，让 cli/run 等模块可被导入。
    EXE 模式：PyInstaller 已把模块打包进 _MEIPASS，无需处理。"""
    if getattr(sys, "frozen", False):
        return
    pkg = Path(__file__).resolve().parent.parent / "xyb"
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))


def main() -> int:
    _prepare_sys_path()
    import cli

    return int(cli.main() or 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)

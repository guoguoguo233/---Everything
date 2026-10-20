# -*- coding: utf-8 -*-
"""
PyInstaller 打包入口（EXE 版启动入口）

开发时也可以直接运行： python tools\\exe_entry.py
打包后由 build_exe.py 调用，生成「校友邦周志助手.exe」。

重要：EXE 模式下会在最早期就把 TEMP/TMP 重定向到 exe 同目录的 _tmp\\。
这样单文件 EXE 的自解压和 Playwright 的临时文件都不会写入系统 %TEMP%，
可避开火绒/360 等安全软件对"程序往系统临时目录写文件"的拦截。
"""
import os
import sys
from pathlib import Path


def _redirect_temp() -> None:
    """EXE 模式：把临时目录改到 exe 同目录，必须在导入 playwright 之前调用。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        base = Path(sys.executable).resolve().parent
        tmp = base / "_tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        os.environ["TEMP"] = str(tmp)
        os.environ["TMP"] = str(tmp)
        # 让 tempfile 也立刻用上新目录（避免缓存旧路径）
        import tempfile
        tempfile.tempdir = str(tmp)
    except Exception:
        pass


def _prepare_sys_path():
    """源码模式：把 xyb/ 加入 sys.path，让 cli/run 等模块可被导入。
    EXE 模式：PyInstaller 已把模块打包进 _MEIPASS，无需处理。"""
    if getattr(sys, "frozen", False):
        return
    pkg = Path(__file__).resolve().parent.parent / "xyb"
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))


def main() -> int:
    _redirect_temp()
    _prepare_sys_path()
    import cli

    return int(cli.main() or 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)

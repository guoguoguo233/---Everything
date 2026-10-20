# -*- coding: utf-8 -*-
"""
PyInstaller 打包入口（EXE 版启动入口）

开发时也可以直接运行： python tools\\exe_entry.py
打包后由 build_exe.py 调用，生成「校友邦周志助手.exe」。

要点：
1. EXE 模式下最早期就把 TEMP/TMP 重定向到 exe 同目录的 _tmp\\，
   避免火绒/360 拦截程序写系统临时目录（Playwright 也依赖这个目录）。
2. 顶层捕获所有异常，写入 exe 同目录的 _error.log 并暂停，
   避免"控制台窗口一闪就关"、看不到任何错误信息。
"""
import os
import sys
import traceback
from pathlib import Path


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _redirect_temp() -> None:
    """EXE 模式：把临时目录改到 exe 同目录，必须在导入 playwright 之前调用。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        tmp = _app_dir() / "_tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        os.environ["TEMP"] = str(tmp)
        os.environ["TMP"] = str(tmp)
        import tempfile
        tempfile.tempdir = str(tmp)
    except Exception:
        pass


def _prepare_sys_path():
    """源码模式：把 xyb/ 加入 sys.path，让 cli/run 等模块可被导入。
    EXE 模式：PyInstaller 已把模块打包进 _MEIPASS，无需处理。"""
    if getattr(sys, "frozen", False):
        return
    pkg = Path(__file__).resolve().parent / "xyb"
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))


def _write_crash_log(exc: BaseException) -> Path | None:
    try:
        path = _app_dir() / "_error.log"
        with path.open("a", encoding="utf-8") as f:
            f.write("\n" + "=" * 70 + "\n")
            f.write("时间: " + __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write("程序目录: " + str(_app_dir()) + "\n")
            f.write("TEMP: " + str(os.environ.get("TEMP")) + "\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        return path
    except Exception:
        return None


def _pause():
    try:
        input("\n按回车键关闭窗口…")
    except (EOFError, KeyboardInterrupt):
        pass


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
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - 顶层兜底，保证能看到错误
        print()
        print("=" * 70)
        print("  程序出现未处理的错误，详细信息如下：")
        print("=" * 70)
        traceback.print_exc()
        log = _write_crash_log(exc)
        print()
        if log:
            print(f"错误日志已保存到：{log}")
        print("请把上面的报错（或 _error.log 内容）反馈给开发者。")
        _pause()
        sys.exit(1)

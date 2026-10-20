# -*- coding: utf-8 -*-
"""
一键打包 EXE（Windows）

用法：
    python build_exe.py              # 单文件 EXE（推荐分发，体积大、首次启动慢几秒）
    python build_exe.py --onedir     # 文件夹版（启动快，适合自己用）
    python build_exe.py --clean      # 清掉 build/dist/*.spec 后重新打包（默认就会清）

产物：
    dist/校友邦周志助手.exe            （单文件模式）
    dist/校友邦周志助手/                （文件夹模式）

要求：Python 3.10+、pip install pyinstaller
EXE 里已包含 Playwright 驱动（用于控制本机 Chrome），但**不包含 Chrome 本身**，
用户机器上仍需安装 Google Chrome。
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
APP_NAME = "校友邦周志助手"
ENTRY = ROOT / "tools" / "exe_entry.py"
LINE = "=" * 62


def run(cmd: list) -> int:
    print("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.call(cmd)


def main() -> int:
    args = sys.argv[1:]
    onedir = "--onedir" in args

    print(LINE)
    print("  打包 校友邦周志助手.exe")
    print(LINE)
    print()

    # 1) 依赖检查
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller。正在安装…")
        if run([sys.executable, "-m", "pip", "install", "pyinstaller"]) != 0:
            print("PyInstaller 安装失败，请手动执行： pip install pyinstaller")
            return 1
    ver = subprocess.run([sys.executable, "-c", "import PyInstaller;print(PyInstaller.__version__)"],
                         capture_output=True, text=True)
    print(f"[1/4] PyInstaller 版本：{ver.stdout.strip() or '?'}")

    # 2) 检查打包所需文件
    print("[2/4] 检查打包素材")
    need = [ENTRY, ROOT / "xyb" / "cli.py", ROOT / "xyb" / "config.example.json",
            ROOT / "README.md", ROOT / "docs"]
    missing = [p for p in need if not p.exists()]
    if missing:
        for p in missing:
            print(f"  缺少：{p}")
        return 1
    print("  OK：入口、源码、配置模板、README、docs 都在")

    # 3) 清理旧产物
    print("[3/4] 清理旧产物")
    for d in (ROOT / "build", ROOT / "dist"):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            print(f"  已删除 {d.name}/")
    for spec in ROOT.glob("*.spec"):
        spec.unlink(missing_ok=True)
        print(f"  已删除 {spec.name}")
    print("  完成")

    # 4) 打包
    print("[4/4] 开始打包（首次约 1-2 分钟，产物约 55MB）")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir" if onedir else "--onefile",
        "--console",
        "--noupx",
        "--name", APP_NAME,
        "--paths", str(ROOT / "xyb"),
        "--add-data", f"{ROOT / 'xyb' / 'config.example.json'};.",
        "--add-data", f"{ROOT / 'README.md'};.",
        "--add-data", f"{ROOT / 'docs'};docs",
        "--collect-all", "playwright",
        "--hidden-import", "greenlet",
        "--hidden-import", "pyee",
        "--hidden-import", "qrcode.image.pil",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        str(ENTRY),
    ]
    if not onedir:
        # 单文件版默认把自解压目录从系统 %TEMP% 改到 %LOCALAPPDATA%\XybJournal。
        # 火绒/360 等安全软件常拦截"程序往系统临时目录写文件"，改到非 Temp 目录可绕开；
        # 目录名带程序名，便于用户识别和手动清理。
        cmd += ["--runtime-tmpdir", r"%LOCALAPPDATA%\XybJournal"]
    code = run(cmd)
    if code != 0:
        print()
        print("打包失败，请把上面的报错发到 Issue。")
        return code

    # 结果
    exe = ROOT / "dist" / f"{APP_NAME}.exe"
    folder = ROOT / "dist" / APP_NAME
    print()
    print(LINE)
    if exe.exists():
        print(f"  打包成功：{exe}")
        print(f"  体积：{exe.stat().st_size / 1024 / 1024:.1f} MB")
        print()
        print("  分发方式：把这一个 exe 上传到 GitHub Releases 即可。")
        print("  用户下载后放到任意可写文件夹，双击运行，无需安装 Python。")
    elif folder.exists():
        print(f"  打包成功（文件夹版）：{folder}")
        print()
        print("  把整个文件夹打包成 zip 后分发。")
    else:
        print("  未找到打包产物，请检查上面的日志。")
        return 1
    print()
    print("  提醒：")
    print("   1. 单文件 EXE 首次启动需自解压，通常慢 5-10 秒（杀软扫描时可能更久）；")
    print("   2. 杀毒软件可能误报（PyInstaller 打包常见），可加入信任或改用源码版；")
    print("   3. EXE 不含 Chrome，用户电脑需已安装 Google Chrome。")
    print(LINE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

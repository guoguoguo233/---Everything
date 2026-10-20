# -*- coding: utf-8 -*-
"""
安装脚本（由 install.bat 调用）

做 4 件事：
  1. 检查 Python 版本（>= 3.10）
  2. 安装 requirements.txt 里的依赖
  3. 检查本机 Chrome
  4. 若 xyb/config.json 不存在则从 config.example.json 生成

单独运行： python tools\\install.py
只做检查： python tools\\install.py --check
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
if os.name == "nt":
    os.system("chcp 65001 >nul 2>nul")

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements.txt"
CONFIG = ROOT / "xyb" / "config.json"
EXAMPLE = ROOT / "xyb" / "config.example.json"
LINE = "=" * 62
OK = "[OK]"
BAD = "[X]"

CHROME_PATHS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]


def main() -> int:
    check_only = "--check" in sys.argv

    print(LINE)
    print("  校友邦周志助手 - 安装程序")
    print(LINE)
    print()

    # ---------- 1. Python ----------
    v = sys.version_info
    print(f"[1/4] 检查 Python 版本：{v.major}.{v.minor}.{v.micro}")
    if v < (3, 10):
        print(f"  {BAD} 版本过低，需要 Python 3.10 或更高。")
        print("      请到 https://www.python.org/downloads/ 升级后重试。")
        return 1
    print(f"  {OK} 版本满足要求（需 3.10+）")

    # ---------- 2. 依赖 ----------
    print()
    print("[2/4] 安装 Python 依赖（首次约 1-3 分钟，需要联网）")
    if not REQ.exists():
        print(f"  {BAD} 找不到 {REQ}")
        return 1
    if check_only:
        print("  （--check 模式，跳过安装）")
    else:
        print("      正在执行 pip install -r requirements.txt ...")
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(REQ),
               "--disable-pip-version-check"]
        code = subprocess.call(cmd)
        if code != 0:
            print()
            print(f"  {BAD} 依赖安装失败（pip 退出码 {code}）。")
            print("      常见原因：网络问题。可换国内镜像重试：")
            print("      python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple")
            return 1

    missing = []
    for mod, name in [("requests", "requests"), ("playwright", "playwright"),
                      ("qrcode", "qrcode"), ("PIL", "pillow")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(name)
    if missing:
        print(f"  {BAD} 以下依赖仍不可用：{', '.join(missing)}")
        print("      请重试安装，或手动执行：pip install " + " ".join(missing))
        return 1
    print(f"  {OK} 依赖就绪：requests / playwright / qrcode / pillow")
    print("      说明：本工具直接调用你电脑上已安装的 Chrome，不需要另外下载浏览器内核。")

    # ---------- 3. Chrome ----------
    print()
    print("[3/4] 检查 Google Chrome")
    found = next((p for p in CHROME_PATHS if p.exists()), None)
    if found:
        print(f"  {OK} 已检测到 Chrome：{found}")
    else:
        print(f"  {BAD} 未检测到 Google Chrome。")
        print("      本工具需要 Chrome 来复用你的登录状态，请先安装：")
        print("      https://www.google.com/chrome/")
        print("      （可以先继续，但运行程序时会无法登录）")

    # ---------- 4. 配置 ----------
    print()
    print("[4/4] 准备配置文件")
    if CONFIG.exists():
        print(f"  {OK} 配置文件已存在，保留不覆盖：{CONFIG}")
    elif EXAMPLE.exists():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(EXAMPLE.read_text(encoding="utf-8-sig"), encoding="utf-8")
        print(f"  {OK} 已生成配置文件：{CONFIG}")
    else:
        print(f"  {BAD} 找不到配置模板 {EXAMPLE}")
        return 1

    # ---------- 完成 ----------
    print()
    print(LINE)
    if check_only:
        print("  检查完成。")
    else:
        print("  安装完成！")
        print()
        print("  下一步：双击 start.bat 启动")
        print("  首次启动会自动引导你：")
        print("    ① 填写 DeepSeek API Key（用于生成周志正文）")
        print("    ② 扫码登录校友邦（只需一次）")
        print("  登录完成后，在菜单里选 [2] 就能写第一篇周志。")
        print()
        print("  不知道怎么申请 API Key？看 docs\\AI接口接入.md")
    print(LINE)
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(130)

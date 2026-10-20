# -*- coding: utf-8 -*-
"""
校友邦周志助手 · 一键菜单

双击 start.bat 就会进入这个菜单，按数字选择功能，不需要记命令。
"""
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
RUN_PY = PKG / "run.py"
SYNC_PY = PKG / "sync_archive.py"
CONFIG = PKG / "config.json"
CONFIG_EXAMPLE = PKG / "config.example.json"
TITLE = "校友邦周志助手"
VERSION = "1.0.0"
PLACEHOLDER = "sk-在这里填你的DeepSeekKey"

LINE = "=" * 62


# ---------------- 基础 ----------------
def clear():
    os.system("cls" if os.name == "nt" else "clear")


def pause():
    try:
        input("\n按回车键返回菜单…")
    except (EOFError, KeyboardInterrupt):
        pass


def wait_enter(prompt: str):
    """等待用户按回车；管道/重定向环境下不报错。"""
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()


def call(args) -> int:
    """调用子脚本，输出直接透传到控制台"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, "-u", str(RUN_PY if args is not SYNC_PY else SYNC_PY)]
    if args is not SYNC_PY:
        cmd += [str(a) for a in args]
    try:
        return subprocess.call(cmd, cwd=str(ROOT), env=env)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


def load_cfg() -> dict:
    if not CONFIG.exists():
        CONFIG.write_text(CONFIG_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已创建配置文件：{CONFIG}")
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        print(f"配置文件格式有误：{e}\n请删除 {CONFIG} 后重新运行（会重新生成）。")
        raise SystemExit(1)


def save_cfg(cfg: dict):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def key_status(cfg: dict) -> str:
    k = str(((cfg.get("ai") or {}).get("api_key")) or "")
    if not k or k == PLACEHOLDER:
        return "未配置（菜单 [6] 填写）"
    tail = k[-4:] if len(k) >= 4 else "****"
    return f"已配置（sk-****{tail}）"


def env_problems() -> list[str]:
    probs = []
    if sys.version_info < (3, 10):
        probs.append(f"Python 版本过低（当前 {platform.python_version()}），需要 3.10 或更高")
    for mod, name in [("playwright", "playwright"), ("requests", "requests"),
                      ("qrcode", "qrcode"), ("PIL", "pillow")]:
        try:
            __import__(mod)
        except ImportError:
            probs.append(f"缺少依赖：{name}（请退出后双击 install.bat）")
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    if not any(Path(p).exists() for p in chrome_paths):
        probs.append("没有找到 Google Chrome（本工具依赖它复用登录态，请先安装 Chrome）")
    return probs


# ---------------- 配置向导 ----------------
def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    show = f"（当前：{default}）" if default and not secret else ""
    try:
        if secret and sys.stdin.isatty():
            import getpass
            val = getpass.getpass(f"{prompt} {show}\n> ").strip()
        else:
            val = input(f"{prompt} {show}\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val or default


def ask_list(prompt: str, default: list) -> list:
    d = "、".join(default or [])
    val = ask(prompt + "（用逗号分隔，可留空）", d)
    if val == d:
        return default or []
    return [x.strip() for x in val.replace("，", ",").split(",") if x.strip()]


def wizard(cfg: dict, first_run: bool = False) -> dict:
    ai = cfg.setdefault("ai", {})
    intern = cfg.setdefault("internship", {})
    style = cfg.setdefault("style", {})

    print(LINE)
    print("  配置向导" + ("（首次使用，只需填一次）" if first_run else ""))
    print(LINE)
    print("  · DeepSeek Key 申请方法见 docs/AI接口接入.md")
    print("  · 直接回车 = 保持原值")
    print()

    k = ask("1) DeepSeek API Key（sk- 开头的字符串，输入时不显示）",
            str(ai.get("api_key") or ""), secret=True)
    if k:
        ai["api_key"] = k

    model = ask("2) 模型名称", str(ai.get("model") or "deepseek-chat"))
    ai["model"] = model or "deepseek-chat"
    ai.setdefault("base_url", "https://api.deepseek.com/v1")

    intern["company"] = ask("3) 实习单位名称（留空=自动从校友邦读取）", str(intern.get("company") or ""))
    intern["position"] = ask("4) 实习岗位（留空=自动读取）", str(intern.get("position") or ""))
    intern["work_summary"] = ask("5) 主要工作内容（一句话，例如：参与后端接口开发与维护）",
                                 str(intern.get("work_summary") or ""))
    intern["common_tasks"] = ask_list("6) 常见日常任务", intern.get("common_tasks") or [])
    intern["tech_stack"] = ask_list("7) 用到的技术/工具", intern.get("tech_stack") or [])

    mn = ask("8) 每篇最少字数（学校要求，留空保持）", str(style.get("min_words") or 320))
    mx = ask("9) 每篇最多字数（留空保持）", str(style.get("max_words") or 620))
    try:
        style["min_words"] = int(mn)
        style["max_words"] = int(mx)
    except ValueError:
        pass
    style.setdefault("title_pattern", "{position}实习周志（{index}周）")

    save_cfg(cfg)
    print(f"\n已保存：{CONFIG}")

    if (ai.get("api_key") or "") not in ("", PLACEHOLDER):
        ok = ask("\n要现在测试一下 Key 能不能用吗？(y/n)", "y")
        if ok.lower().startswith("y"):
            print("正在测试…")
            try:
                import ai_writer
                good, msg = ai_writer.check_api(cfg)
                print(("✓ " if good else "✗ ") + msg)
            except Exception as e:
                print("测试失败：", e)
    return cfg


# ---------------- 菜单动作 ----------------
def act_login():
    print("\n正在启动浏览器检查登录状态…")
    print("如果弹出提示要求登录：请用「校友邦学生版小程序」扫一扫（不是微信扫一扫）\n")
    call(["--login-only"])
    pause()


def act_write_current(cfg: dict):
    if key_status(cfg).startswith("未配置"):
        print("\n还没有配置 DeepSeek API Key，请先选 [6] 修改配置。")
        pause()
        return
    notes = ask("\n本周有什么真实素材？（例如：主要在做排序算法优化，可留空）", "")
    args = []
    if notes:
        args += ["--notes", notes]
    call(args)
    pause()


def act_backfill(cfg: dict):
    print("\n批量补交：先生成、再预览、最后确认提交。")
    weeks = ask("要补哪些周？（例如 1-9；直接回车=全部未提交的周）", "")
    args = ["--gen-batch"]
    if weeks:
        args += ["--weeks", weeks]
    print("\n—— 第 1 步：生成正文 ——")
    t0 = time.time()
    code = call(args)
    if code == 8:
        print("\n没有需要补交的周次（都提交过了）。")
        pause()
        return
    if code != 0:
        print(f"\n生成过程出错（退出码 {code}），请把上面的报错发出来排查。")
        pause()
        return

    preview = [p for p in (ROOT / "周志记录").glob("待提交预览_*.md")
               if p.stat().st_mtime >= t0 - 2]
    preview.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if preview:
        p = preview[0]
        print(f"\n预览文件：{p}")
        if ask("要现在打开预览文件逐篇看吗？(y/n)", "y").lower().startswith("y"):
            try:
                os.startfile(str(p))  # type: ignore[attr-defined]
            except Exception as e:
                print("打开失败，请手动打开：", e)
    else:
        print("\n本次没有生成新的预览文件。")

    print("\n—— 第 2 步：质检 ——")
    call(["--qc"])

    print("\n—— 第 3 步：提交 ——")
    ans = ask("确认以上内容没问题、现在全部提交到校友邦吗？(yes/n)", "n")
    if ans.lower() in ("y", "yes", "是"):
        call(["--submit-batch", "--yes"])
        call_sync()
    else:
        print("已取消提交。内容已保存在本地，下次可选 [3] 重新走一遍或直接运行 --submit-batch。")
    pause()


def act_qc():
    print("\n—— 质检已生成的内容 ——")
    call(["--qc"])
    pause()


def act_sync():
    print("\n—— 从校友邦回读，刷新本地归档 ——")
    call_sync()
    pause()


def call_sync():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.call([sys.executable, "-u", str(SYNC_PY)], cwd=str(ROOT), env=env)


def act_config(cfg: dict):
    wizard(cfg)
    pause()


# ---------------- 主循环 ----------------
def main():
    config_existed = CONFIG.exists()
    cfg = load_cfg()
    need_key = key_status(cfg).startswith("未配置")
    # 首次使用判定：配置文件刚创建，或者 Key 还没填（install.bat 会先创建配置，所以两者都要算）
    need_setup = (not config_existed) or need_key

    probs = env_problems()
    if probs:
        clear()
        print(LINE)
        print(f"  {TITLE} v{VERSION} —— 环境检查未通过")
        print(LINE)
        for p in probs:
            print("  ✗", p)
        print("\n请先双击 install.bat 完成安装，再运行本程序。")
        wait_enter("\n按回车键退出…")
        return 1

    if need_setup:
        clear()
        print(LINE)
        print(f"  欢迎使用 {TITLE} v{VERSION}")
        print(LINE)
        print("  首次使用需要完成 3 件事：")
        print("    ① 填写 DeepSeek API Key（用于生成周志正文，申请方法见 docs/AI接口接入.md）")
        print("    ② 扫码登录校友邦（只需一次）")
        print("    ③ 生成并提交你的第一篇周志（登录后在菜单选 [2]）")
        print()
        wait_enter("按回车键开始配置…")
        cfg = wizard(cfg, first_run=True)
        print("\n下一步：扫码登录。")
        act_login()

    while True:
        clear()
        cfg = load_cfg()
        print(LINE)
        print(f"  {TITLE}  v{VERSION}")
        print("  自动生成实习周志 → 预览确认 → 提交校友邦")
        print(LINE)
        print(f"  AI Key：{key_status(cfg)}")
        print(f"  配置文件：{CONFIG}")
        print("-" * 62)
        print("  [1] 登录 / 检查登录状态")
        print("  [2] 写本周周志（生成 → 预览 → 确认 → 提交）")
        print("  [3] 批量补交历史周（先生成预览，确认后再提交）")
        print("  [4] 质检已生成的内容")
        print("  [5] 刷新本地归档（从校友邦回读）")
        print("  [6] 修改配置（AI Key / 实习信息 / 字数）")
        print("  [7] 查看使用说明")
        print("  [0] 退出")
        print("-" * 62)
        try:
            choice = input("请输入编号后按回车：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice == "1":
            act_login()
        elif choice == "2":
            act_write_current(cfg)
        elif choice == "3":
            act_backfill(cfg)
        elif choice == "4":
            act_qc()
        elif choice == "5":
            act_sync()
        elif choice == "6":
            act_config(cfg)
        elif choice == "7":
            readme = ROOT / "README.md"
            if readme.exists():
                try:
                    os.startfile(str(readme))  # type: ignore[attr-defined]
                except Exception:
                    print("请手动打开：", readme)
            else:
                print("README.md 不存在。")
            pause()
        elif choice == "0":
            print("\n再见。")
            return 0
        else:
            print("没有这个选项，请输入 0-7。")
            pause()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已退出。")
        sys.exit(0)

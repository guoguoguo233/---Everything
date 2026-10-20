# -*- coding: utf-8 -*-
"""
校友邦周志助手 · 一键菜单

双击 start.bat（源码版）或 校友邦周志助手.exe（免安装版）都会进入这个菜单，
按数字选择功能，不需要记命令。
"""
import json
import os
import platform
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import paths  # noqa: E402

ROOT = paths.app_dir()
CONFIG = paths.config_path()
CONFIG_EXAMPLE = paths.example_config_path()
TITLE = "校友邦周志助手"
VERSION = "1.0.0"
PLACEHOLDER = "sk-在这里填你的DeepSeekKey"
FROZEN = paths.is_frozen()

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
    """在当前进程内执行 run.py 的主流程。

    EXE 版里没有独立的 Python 解释器和 run.py 脚本，所以统一改成进程内调用，
    源码版与 EXE 版走同一条代码路径，行为一致。
    任何异常都在这里兜住并打印，避免控制台窗口直接关闭。
    """
    import traceback

    import run

    old_argv = sys.argv
    sys.argv = ["run.py"] + [str(a) for a in args]
    try:
        return int(run.main() or 0)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except SystemExit as exc:
        try:
            return int(exc.code or 0)
        except (TypeError, ValueError):
            return 0
    except Exception as exc:  # noqa: BLE001 - 保证错误可见，不让窗口闪退
        print()
        print("=" * 62)
        print("  执行出错：")
        print("=" * 62)
        traceback.print_exc()
        try:
            log = paths.app_dir() / "_error.log"
            import datetime as _dt
            with log.open("a", encoding="utf-8") as f:
                f.write("\n" + "=" * 62 + "\n")
                f.write("时间: " + _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
                f.write("参数: " + repr(args) + "\n")
                f.write("TEMP: " + str(os.environ.get("TEMP")) + "\n")
                f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            print(f"\n错误日志已保存到：{log}")
        except Exception:
            pass
        print("\n请把上面的报错（或 _error.log）反馈给开发者。")
        return 1
    finally:
        sys.argv = old_argv


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
    if not FROZEN and sys.version_info < (3, 10):
        probs.append(f"Python 版本过低（当前 {platform.python_version()}），需要 3.10 或更高")
    for mod, name in [("playwright", "playwright"), ("requests", "requests"),
                      ("qrcode", "qrcode"), ("PIL", "pillow")]:
        try:
            __import__(mod)
        except ImportError:
            if FROZEN:
                probs.append(f"程序文件不完整：缺少 {name}，请重新下载 EXE（可能被杀毒软件删了文件）")
            else:
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
# 用户从终端复制粘贴时，很容易把整段提示文字一起贴进来（导致 model 变成一句问话）。
# 这里识别这类"粘贴噪声"，避免写进配置。
_NOISE_MARKERS = (
    "（当前：", "(y/n)", "按回车键", "正在测试", "AI 接口返回", "已保存：",
    "错误日志", "Traceback", "✗", "请输入编号", "==============================================================",
)


def _looks_like_paste_noise(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return False
    if v.startswith(">") or v.startswith("（当前："):
        return True
    return any(m in v for m in _NOISE_MARKERS)


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
    if val and _looks_like_paste_noise(val):
        print("  ⚠ 检测到你似乎把终端提示文字也粘进来了，这一项已按原值保留。")
        print("    （请只粘贴要填的内容本身，例如 Key、公司名，不要包含“>”或提示语）")
        return default
    return val or default


def ask_list(prompt: str, default: list) -> list:
    d = "、".join(default or [])
    val = ask(prompt + "（用逗号分隔，可留空）", d)
    if val == d:
        return default or []
    items = [x.strip() for x in val.replace("，", ",").split(",") if x.strip()]
    return [x for x in items if not _looks_like_paste_noise(x)]


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
    print("  ⚠ 注意：Key 会显示在屏幕上（隐藏输入在 Windows 控制台里粘贴容易只收到第一个字符）。")
    print("    输入框里请用【右键粘贴】或 Ctrl+V，粘贴完检查一下长度是不是 30 多个字符。")
    for attempt in range(3):
        k = ask("1) DeepSeek API Key（sk- 开头的字符串）", str(ai.get("api_key") or ""))
        if not k:
            break
        if len(k) >= 20 and k.startswith("sk-"):
            ai["api_key"] = k
            break
        print(f"  ✗ 这个 Key 看起来不完整（长度 {len(k)}，应以 sk- 开头且通常 30 多个字符）。请重新输入，或直接回车跳过。")
    else:
        print("  （多次输入无效，本次不修改 Key）")

    model = ask("2) 模型名称（DeepSeek 填 deepseek-chat 或 deepseek-flash）",
                str(ai.get("model") or "deepseek-chat"))
    # 模型名只允许字母/数字/点/横线/斜杠/冒号，防止把提示语写进配置
    if not re.fullmatch(r"[A-Za-z0-9._:/\-]{2,80}", model or ""):
        print(f"  ⚠ 模型名看起来不合法（{model!r}），已改回 deepseek-chat。")
        model = "deepseek-chat"
    ai["model"] = model
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
    """进程内执行归档同步。"""
    import sync_archive

    try:
        return int(sync_archive.main() or 0)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except SystemExit as exc:
        try:
            return int(exc.code or 0)
        except (TypeError, ValueError):
            return 0


def act_config(cfg: dict):
    wizard(cfg)
    pause()


# ---------------- 环境自检（--check） ----------------
def quick_login_status() -> str:
    """快速判断登录态，未登录时不会卡在扫码等待。"""
    try:
        from browser import XybClient
        import auth
    except Exception as e:
        return f"组件加载失败：{e}"
    try:
        with XybClient(headless=True) as c:
            c.goto("#/workbench")
            c.page.wait_for_timeout(2500)
            c.cache_secret_url()
            if auth.is_logged_in(c):
                info = auth.loginer_info(c)
                return f"已登录：{info.get('loginer') or '?'} | {info.get('schoolName') or '?'}"
            return "未登录（启动程序后选 [1] 扫码登录）"
    except Exception as e:
        return f"登录检测失败：{str(e)[:120]}（可先忽略，启动程序后选 [1] 重试）"


def do_check() -> int:
    """打印环境自检信息，供排错和 Issue 使用。"""
    probs = env_problems()
    print(LINE)
    print(f"  {TITLE} v{VERSION} —— 环境自检")
    print(LINE)
    print(f"  运行方式   ：{'EXE 免安装版' if FROZEN else '源码版（Python）'}")
    print(f"  操作系统   ：{platform.platform()}")
    if not FROZEN:
        print(f"  Python     ：{platform.python_version()}")
    print(f"  程序目录   ：{ROOT}")
    print(f"  配置文件   ：{CONFIG}（{'存在' if CONFIG.exists() else '不存在，将自动创建'}）")
    cfg = load_cfg()
    print(f"  AI Key     ：{key_status(cfg)}")
    print(f"  模型       ：{(cfg.get('ai') or {}).get('model') or '?'}")
    print()
    if probs:
        print("  环境检查   ：发现问题")
        for p in probs:
            print("    ✗", p)
    else:
        print("  环境检查   ：通过（依赖完整、Chrome 已安装）")
    print()
    print("  正在检查登录态（约 5 秒，会启动一次 Chrome）…")
    print(f"  登录状态   ：{quick_login_status()}")
    print()
    print("  提示：完整功能请直接运行本程序（不加参数）。")
    print(LINE)
    return 0 if not probs else 1


# ---------------- 主循环 ----------------
def main():
    argv = sys.argv[1:]
    if "--version" in argv or "-V" in argv:
        print(f"{TITLE} v{VERSION}（{'EXE' if FROZEN else '源码'}）")
        return 0
    if "--check" in argv:
        try:
            return do_check()
        except KeyboardInterrupt:
            return 0

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
        if FROZEN:
            print("\n请把本程序放在一个可写的普通文件夹后重新运行；仍失败请重新下载 EXE。")
        else:
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
        print(f"  运行方式：{'EXE 免安装版' if FROZEN else '源码版（Python）'}")
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
            readme = paths.readme_path()
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

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


# ---------------- 登录状态（主页展示） ----------------
LOGIN_STATE = paths.app_dir() / "_login_state.json"
LOGIN_TTL = 600  # 10 分钟内复用上次结果，避免每次进菜单都启动浏览器


def probe_login(force: bool = False) -> dict:
    """检测登录状态（带缓存）；返回 {logged_in, name, school, error}。"""
    if not force:
        try:
            if LOGIN_STATE.exists():
                data = json.loads(LOGIN_STATE.read_text(encoding="utf-8-sig"))
                if time.time() - float(data.get("checked_at") or 0) < LOGIN_TTL:
                    return data
        except Exception:
            pass

    print("正在检查登录状态（约 5~8 秒，会启动一次浏览器）…")
    data = {"logged_in": None, "checked_at": time.time()}
    try:
        from browser import XybClient
        import auth
        with XybClient(headless=True) as c:
            c.goto("#/workbench")
            c.page.wait_for_timeout(2500)
            c.cache_secret_url()
            if auth.is_logged_in(c):
                info = auth.loginer_info(c) or {}
                data.update(logged_in=True,
                            name=info.get("loginer") or "",
                            school=info.get("schoolName") or "")
            else:
                data.update(logged_in=False)
    except Exception as exc:
        data["error"] = str(exc)[:120]
    try:
        LOGIN_STATE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return data


def login_line(state: dict) -> str:
    if state.get("logged_in") is True:
        name = state.get("name") or "（未知用户）"
        school = state.get("school") or "（未知学校）"
        return f"已登录 — {name} / {school}"
    if state.get("logged_in") is False:
        return "未登录（请选 [1] 扫码登录）"
    err = state.get("error") or "无法连接"
    return f"未检测到（{err}）"


# ---------------- 菜单动作 ----------------
def act_login():
    print("\n正在启动浏览器检查登录状态…")
    print("如果提示需要登录：请用「校友邦学生版小程序」扫一扫（不是微信扫一扫）\n")
    call(["--login-only"])
    state = probe_login(force=True)
    print(f"\n当前登录状态：{login_line(state)}")
    pause()


def act_write_current(cfg: dict):
    if key_status(cfg).startswith("未配置"):
        print("\n还没有配置 DeepSeek API Key，请先选 [6] 修改配置。")
        pause()
        return
    notes = ask("\n本周有什么真实素材？（例如：主要在做排序算法优化，可留空）", "")
    feedback = ""
    while True:
        args = ["--gen-only"]
        if not sys.stdin.isatty():
            args.append("--no-open")  # 非交互环境（脚本/测试）不要弹编辑器
        merged = notes
        if feedback:
            merged = (notes + "。" if notes else "") + "【用户修改意见】" + feedback
        if merged:
            args += ["--notes", merged]
        code = call(args)
        if code != 0:
            print(f"\n生成失败（退出码 {code}），请把上面的报错发出来排查。")
            pause()
            return

        ans = ask("预览文件已自动打开。下一步：y=提交 / m=输入修改意见重新生成 / n=放弃", "y")
        choice = (ans or "").strip().lower()
        if choice in ("y", "yes", "是", "提交", "ok"):
            rc = call(["--submit-pending", "--yes"])
            if rc == 0:
                call_sync()
            pause()
            return
        if choice in ("m", "modify", "改", "修改", "重写"):
            feedback = ask("请输入修改意见（例如：多写具体工作细节 / 语气再朴素些 / 少写套话 / 多写遇到的困难）", "")
            if not feedback:
                print("没有输入修改意见，保持当前内容。")
            continue
        print("\n已放弃提交。内容已保存在本地，可稍后重新选 [2] 或 [3]。")
        pause()
        return


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


HELP_TEXT = """\
【第 1 步】登录：选 [1]，用「校友邦学生版小程序」扫一扫（不是微信扫一扫）。
           登录状态会显示在菜单顶部；失效时重新扫一次即可。
【第 2 步】写周志：选 [2]，可先填本周真实素材 → 自动生成 →
           自动打开预览文件给你看 → 回到窗口选择：
             y = 提交到校友邦
             m = 输入修改意见，让 AI 重新写一版（可反复改）
             n = 放弃，内容仍保存在本地
【批量补交】选 [3]：先生成所有未提交的周 → 打开预览文件审核 → 质检 → 确认后批量提交。
【查看归档】选 [5]：从校友邦回读已提交内容，刷新「周志记录/周志记录.md」。
【改配置】  选 [6]：修改 API Key、模型、实习信息、字数要求。
【数据位置】config.json、chrome-profile、周志记录 都在本程序所在文件夹里，
           整个文件夹拷走即可换电脑继续用。
【出问题】  看同目录 _error.log；或用命令行运行 --check 做环境自检。"""


def act_webui():
    """启动本地网页控制台（只监听 127.0.0.1），并尝试打开浏览器。"""
    import webbrowser

    try:
        import webui
    except Exception as exc:
        print("网页控制台组件加载失败：", exc)
        pause()
        return
    try:
        url = webui.start(open_browser=False)
    except Exception as exc:
        print("启动网页控制台失败：", exc)
        pause()
        return
    print()
    print("=" * 62)
    print("  网页控制台已启动：" + url)
    print("  已尝试在浏览器中打开；没弹出的话请手动复制上面的地址。")
    print("  该服务只监听 127.0.0.1，外部无法访问；关闭本窗口即停止。")
    print("=" * 62)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    pause()


def act_help():
    print()
    print(LINE)
    print("  使用说明（要点）")
    print(LINE)
    print(HELP_TEXT)
    print()

    readme = paths.readme_path()
    docs = paths.docs_dir()
    print(f"完整文档：{readme}")
    if docs.exists():
        print(f"文档目录：{docs}")
    print()
    if ask("要现在打开 README 全文吗？(y/n)", "y").lower().startswith("y"):
        try:
            import run
            opened = run.open_document(readme)
        except Exception:
            opened = False
        if opened:
            print("已打开（如果没弹出来，请按上面的路径手动打开）。")
        else:
            print("自动打开失败，请手动打开上面的路径。")
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
    if "--webui" in argv:
        # 直接以网页控制台模式启动（不开命令行菜单），方便双击/做快捷方式
        import webui
        url = webui.start(open_browser=("--no-browser" not in argv))
        print("按 Ctrl+C 或关闭窗口停止服务：" + url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            webui.stop()
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

    login_state = probe_login()

    while True:
        clear()
        cfg = load_cfg()
        print(LINE)
        print(f"  {TITLE}  v{VERSION}")
        print("  自动生成实习周志 → 预览确认 → 提交校友邦")
        print(LINE)
        print(f"  登录状态：{login_line(login_state)}")
        print(f"  AI Key ：{key_status(cfg)}")
        print(f"  配置文件：{CONFIG}")
        print(f"  运行方式：{'EXE 免安装版' if FROZEN else '源码版（Python）'}")
        print("-" * 62)
        print("  [1] 登录 / 检查登录状态")
        print("  [2] 写本周周志（生成 → 预览 → 修改或提交）")
        print("  [3] 批量补交历史周（先生成预览，确认后再提交）")
        print("  [4] 质检已生成的内容")
        print("  [5] 刷新本地归档（从校友邦回读）")
        print("  [6] 修改配置（AI Key / 实习信息 / 字数）")
        print("  [7] 查看使用说明")
        print("  [8] 打开网页控制台（浏览器操作界面）")
        print("  [0] 退出")
        print("-" * 62)
        try:
            choice = input("请输入编号后按回车：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice == "1":
            act_login()
            login_state = probe_login()
        elif choice == "2":
            act_write_current(cfg)
            login_state = probe_login(force=True)
        elif choice == "3":
            act_backfill(cfg)
            login_state = probe_login(force=True)
        elif choice == "4":
            act_qc()
        elif choice == "5":
            act_sync()
            login_state = probe_login(force=True)
        elif choice == "6":
            act_config(cfg)
        elif choice == "7":
            act_help()
        elif choice == "8":
            act_webui()
        elif choice == "0":
            print("\n再见。")
            return 0
        else:
            print("没有这个选项，请输入 0-8。")
            pause()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已退出。")
        sys.exit(0)

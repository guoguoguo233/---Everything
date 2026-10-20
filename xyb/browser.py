# -*- coding: utf-8 -*-
"""
校友邦客户端：Playwright 驱动本机 Chrome，复用用户真实登录态，
在页面上下文里调用站点自己的签名模块，直接调业务接口。

设计要点：
- 不保存账号密码，不实现签名算法（动态 import 站点自带 secret chunk，天然跟随后端升级）
- 所有请求走页面内 fetch，自带 cookie 与鉴权
- 持久化 Chrome profile，登录一次可长期复用（cookie 2 小时滑动续期）
"""
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

import paths  # noqa: E402

WORK = paths.app_dir()
PROFILE_DIR = paths.profile_dir()
SESSION_FILE = paths.session_path()
COOKIE_SNAPSHOT = paths.app_dir() / "_cookies.json"

PERSONAL = "https://www.xybsyw.com/personal/"

# 页面内调用：拼签名头 + form 表单提交
_JS_API = r"""
async ({path, params, secretUrl, credentials}) => {
  // 1) 懒加载站点自己的签名工具（ESM chunk，页面上下文可直接 import）
  if (!window.__xybSecret || window.__xybSecretChunk !== secretUrl) {
    const mod = await import(secretUrl);
    window.__xybSecret = mod.s;
    window.__xybSecretChunk = secretUrl;
  }
  const S = window.__xybSecret;

  // 2) 完全复刻前端拦截器：n/m/v/t/s
  const n = S.nocheckArrs(params).join(',');
  const sig = S.getTokenData(params, path);
  const headers = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'n': n,
    'm': sig.md5,
    'v': '1.6.40',
    't': String(sig.tstr),
    's': sig.iArrStr,
  };
  // 3) cookie token -> Authorization（顺便续期 2 小时，和前端一致）
  const m = document.cookie.match(/(?:^|;\s*)token=([^;]*)/);
  if (m) {
    const token = decodeURIComponent(m[1]);
    headers['Authorization'] = token;
    document.cookie = 'token=' + m[1] + ';expires=' + new Date(Date.now() + 2 * 3600 * 1000).toUTCString();
  }

  // 4) qs.stringify 等价实现
  const enc = v => encodeURIComponent(String(v));
  const body = Object.keys(params).map(k => enc(k) + '=' + enc(params[k])).join('&');

  const resp = await fetch(path, {method: 'POST', headers, body, credentials: credentials || 'include'});
  const text = await resp.text();
  try {
    return {http: resp.status, json: JSON.parse(text)};
  } catch (e) {
    return {http: resp.status, json: null, raw: text.slice(0, 400)};
  }
}
"""


class XybError(RuntimeError):
    pass


class XybClient:
    def __init__(self, headless: bool = False, slow_mo: int = 0):
        self.headless = headless
        self.slow_mo = slow_mo
        self._pw = None
        self._ctx = None
        self.page = None
        self.secret_url = None

    # ---------- 生命周期 ----------
    def __enter__(self):
        from playwright.sync_api import sync_playwright
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=self.headless,
            slow_mo=self.slow_mo,
            viewport={"width": 1360, "height": 900},
            locale="zh-CN",
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._restore_cookies()
        return self

    def __exit__(self, *exc):
        """关闭浏览器。

        安全软件可能在运行中结束 Playwright 的 driver/node 进程，此时 close() 会抛
        "Connection closed while reading from the driver"。这类异常与业务无关，
        不能让整个程序崩溃（否则窗口闪退、cookie 也可能没落盘），因此全部吞掉。
        """
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        finally:
            try:
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass

    # ---------- Cookie 快照（登录态兜底） ----------
    def save_cookies(self) -> bool:
        """把当前 cookie 存成快照，供下次启动恢复（防止 Chrome 被安全软件结束导致掉登录）。"""
        try:
            cookies = self._ctx.cookies()
            COOKIE_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
            COOKIE_SNAPSHOT.write_text(
                json.dumps({"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cookies": cookies},
                           ensure_ascii=False),
                encoding="utf-8",
            )
            return True
        except Exception:
            return False

    def _restore_cookies(self) -> bool:
        """启动时把上次保存的 cookie 注回浏览器（尽力而为，失败不影响流程）。"""
        try:
            if not COOKIE_SNAPSHOT.exists():
                return False
            data = json.loads(COOKIE_SNAPSHOT.read_text(encoding="utf-8-sig"))
            cookies = data.get("cookies") or []
            if not cookies:
                return False
            cleaned = []
            for ck in cookies:
                item = {
                    "name": ck.get("name"),
                    "value": ck.get("value"),
                    "domain": ck.get("domain"),
                    "path": ck.get("path") or "/",
                }
                if not item["name"] or not item["domain"]:
                    continue
                if ck.get("expires") and ck["expires"] > 0:
                    item["expires"] = ck["expires"]
                if "httpOnly" in ck:
                    item["httpOnly"] = bool(ck["httpOnly"])
                if "secure" in ck:
                    item["secure"] = bool(ck["secure"])
                same_site = str(ck.get("sameSite") or "").capitalize()
                if same_site in ("Strict", "Lax", "None"):
                    item["sameSite"] = same_site
                cleaned.append(item)
            if cleaned:
                self._ctx.add_cookies(cleaned)
                return True
        except Exception:
            pass
        return False

    # ---------- 登录 ----------
    def goto(self, route: str):
        self.page.goto(PERSONAL + route, wait_until="domcontentloaded", timeout=60000)

    def current_token(self) -> str:
        m = re.search(r"(?:^|;\s*)token=([^;]*)", self.page.evaluate("document.cookie") or "")
        return m.group(1) if m else ""

    def has_token(self) -> bool:
        return bool(self.current_token())

    def ensure_login(self, timeout: int = 300, on_tip=None) -> None:
        """登录态复用；没有就打开登录页等用户扫码/输入。"""
        self.goto("#/workbench")
        self.page.wait_for_timeout(3000)
        if not self.has_token():
            self.goto("#/login")
            self.page.wait_for_timeout(2500)
        if self.has_token():
            return

        if on_tip:
            on_tip()
        print("\n" + "=" * 60)
        print(" 需要登录：已弹出 Chrome 窗口，请在里面完成登录")
        print(" （微信扫码 / 企业微信扫码 / 账号密码都行，登录完不用管）")
        print("=" * 60 + "\n")
        deadline = time.time() + timeout
        last = 0
        while time.time() < deadline:
            if self.has_token():
                # 等前端把用户信息等其它 cookie 写完
                self.page.wait_for_timeout(1500)
                self.goto("#/workbench")
                self.page.wait_for_timeout(2500)
                if self.has_token():
                    print("登录成功，登录态已保存到本地 Chrome profile。")
                    self.cache_secret_url()
                    return
            if time.time() - last > 20:
                print(f"  等待扫码中… 剩余 {int(deadline - time.time())} 秒")
                last = time.time()
            self.page.wait_for_timeout(1200)
        raise XybError("登录超时。请重新运行本程序后再试。")

    # ---------- 签名模块地址 ----------
    def cache_secret_url(self) -> str:
        if self.secret_url:
            return self.secret_url
        html = self.page.evaluate(
            "async () => (await fetch('/personal/', {credentials:'include'})).text()"
        )
        m = re.search(r"studentAssets/secret-[A-Za-z0-9_\-]+\.js", html or "")
        if not m:
            raise XybError("未能识别站点签名模块地址，站点可能改版了。")
        self.secret_url = "https://static.xybsyw.com/" + m.group(0)
        if SESSION_FILE.parent.exists() or True:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(
                json.dumps({"secret_url": self.secret_url, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return self.secret_url

    # ---------- 业务接口 ----------
    def api(self, path: str, params: dict[str, Any], credentials: str = "include",
            retries: int = 2) -> dict:
        if not self.secret_url:
            self.cache_secret_url()
        str_params = {k: ("" if v is None else str(v)) for k, v in params.items()}
        last = None
        for attempt in range(retries + 1):
            res = self.page.evaluate(
                _JS_API,
                {"path": path, "params": str_params, "secretUrl": self.secret_url,
                 "credentials": credentials},
            )
            if not isinstance(res, dict):
                raise XybError(f"接口返回异常：{res!r}")
            data = res.get("json")
            if data is None:
                last = f"非 JSON（HTTP {res.get('http')}）：{res.get('raw')}"
                self._recover()
                continue
            # 701 = 服务端要求刷新页面重新同步安全凭证
            if str(data.get("code")) == "701" and attempt < retries:
                last = f"701 {data.get('msg')}"
                self._recover()
                continue
            return data
        raise XybError(f"{path} 调用失败：{last}")

    def _recover(self):
        """刷新页面让站点重新下发安全凭证，然后重试。"""
        try:
            self.page.reload(wait_until="domcontentloaded", timeout=60000)
        except Exception:
            try:
                self.goto("#/workbench")
            except Exception:
                pass
        self.page.wait_for_timeout(5000)
        try:
            self.secret_url = None
            self.cache_secret_url()
        except Exception:
            pass

    def api_ok(self, path: str, params: dict[str, Any]) -> dict:
        """code==200 时返回 data，否则抛错。"""
        data = self.api(path, params)
        code = str(data.get("code", ""))
        if code != "200":
            msg = data.get("msg") or data.get("message") or "未知错误"
            if code == "205":
                raise XybError("登录已失效，请重新运行程序并扫码登录。")
            raise XybError(f"{path} 调用失败：code={code} msg={msg}")
        return data.get("data")

    # ---------- 页面 ----------
    def open_weekly_list(self):
        self.goto("#/weeklyJounal?tab=1")
        self.page.wait_for_timeout(3000)

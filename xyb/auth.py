# -*- coding: utf-8 -*-
"""
登录态管理：
- 检测是否已登录（/login/personalLoginerInfo.action 返回真实 studentId）
- 未登录时启动「自建扫码会话 + 本地网页实时二维码」，等用户用校友邦小程序扫码
登录态保存在本地 Chrome profile，正常可复用很多天；失效时会自动再弹扫码页。
"""
import io
import json
import random
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import paths  # noqa: E402

WORK = paths.app_dir()
QR_PNG = paths.qr_png_path()
PORT = 8765
CHARSET = "0123456789ABCDEF"
CODE_REFRESH_SECONDS = 100
WAIT_SECONDS = 1200

_STATE = {"png": b"", "status": "初始化中"}


def log(*a):
    print(*a, flush=True)


# ---------------- 本地二维码展示页 ----------------
class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/qr.png"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(_STATE["png"])
            return
        html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>校友邦扫码登录</title><meta http-equiv="refresh" content="5">
<style>body{{font-family:system-ui,'Microsoft YaHei';text-align:center;background:#f6f7fb;margin:0;padding:32px}}
.card{{background:#fff;display:inline-block;padding:28px 40px;border-radius:18px;box-shadow:0 8px 28px rgba(0,0,0,.09)}}
h1{{font-size:21px;margin:0 0 8px}} p{{color:#666;font-size:14px;margin:6px 0}}
img{{width:320px;height:320px;image-rendering:pixelated;margin:14px 0;border:1px solid #eee;border-radius:8px}}
.tip{{color:#d9534f;font-weight:600}} .st{{color:#2f7d32;font-weight:600}}</style></head>
<body><div class="card"><h1>校友邦学生版小程序扫码登录</h1>
<p>请用「<b>校友邦学生版小程序</b>」里的<b>扫一扫</b>（不是微信扫一扫）</p>
<img src="/qr.png?t={int(time.time())}" alt="二维码">
<p class="tip">本页 5 秒自动刷新，二维码自动更新，扫到的一定是最新的</p>
<p class="st">状态：{_STATE['status']}</p></div></body></html>"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv
    except OSError as e:
        log(f"（本地二维码端口 {PORT} 被占用，跳过后台网页，仅打印二维码路径：{e}）")
        return None


def _render_qr(code: str, key: str) -> bytes:
    import qrcode
    payload = json.dumps({"code": code, "key": key}, separators=(",", ":"))
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q, box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _new_qr(client, tries: int = 4) -> bool:
    last = None
    for i in range(tries):
        try:
            r = client.api("/login/scan/sessionMd5Detail.action", {})
        except Exception as e:
            last = f"异常 {str(e)[:100]}"
            r = {}
        code = str((r or {}).get("code"))
        key = (r or {}).get("data")
        if code == "200" and isinstance(key, str) and len(key) >= 8:
            code_str = "".join(random.choice(CHARSET) for _ in range(32))
            png = _render_qr(code_str, key)
            QR_PNG.parent.mkdir(parents=True, exist_ok=True)
            QR_PNG.write_bytes(png)
            _STATE.update(png=png, code=code_str, key=key, status="等待扫码")
            return True
        last = f"code={code} msg={str((r or {}).get('msg'))[:60]}"
        time.sleep(1.5)
    log("  生成登录二维码失败：", last)
    return False


# ---------------- 登录检测 ----------------
def is_logged_in(client, verbose: bool = False) -> bool:
    try:
        r = client.api("/login/personalLoginerInfo.action", {})
        data = r.get("data") or {}
        ok = bool(str(r.get("code")) == "200" and data.get("studentId"))
        if verbose:
            log(f"  登录检测：code={r.get('code')} studentId={data.get('studentId')}")
        return ok
    except Exception as e:
        if verbose:
            log("  登录检测异常：", str(e)[:120])
        return False


def loginer_info(client) -> dict:
    try:
        return (client.api("/login/personalLoginerInfo.action", {}) or {}).get("data") or {}
    except Exception:
        return {}


# ---------------- 扫码登录 ----------------
def qr_login(client, wait_seconds: int = WAIT_SECONDS) -> bool:
    _serve()
    client.goto("#/workbench")
    client.page.wait_for_timeout(2500)
    client.cache_secret_url()

    if not _new_qr(client):
        log("!! 无法生成登录二维码，站点可能改版了。")
        return False

    log("")
    log("=" * 66)
    log(" 需要登录。请用浏览器打开下面地址，并用「校友邦学生版小程序」扫码：")
    log(f"     http://127.0.0.1:{PORT}/")
    log(f" 二维码图片也已保存： {QR_PNG}")
    log("=" * 66)

    started = last_refresh = time.time()
    while time.time() - started < wait_seconds:
        if time.time() - last_refresh > CODE_REFRESH_SECONDS:
            _new_qr(client)
            last_refresh = time.time()
        try:
            r = client.api("/login/scan/scanCode.action", {"code": _STATE["code"]})
        except Exception:
            time.sleep(2)
            continue
        data = r.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        if data.get("cancelScan"):
            _STATE["status"] = "已取消，换新码"
            _new_qr(client)
            last_refresh = time.time()
            continue
        if data.get("scanOver"):
            _STATE["status"] = "已扫描，等待手机确认"
        if data.get("success"):
            _STATE["status"] = "登录成功"
            try:
                client.api("/login/login.action",
                           {"loginType": "SCANCODE", "code": _STATE["code"], "userType": "PERSON"})
            except Exception as e:
                log("login.action 异常:", str(e)[:150])
            client.page.wait_for_timeout(2000)
            if is_logged_in(client):
                log("登录成功，登录态已保存。")
                return True
            log("login.action 已返回，但登录态校验未通过，继续等待…")
        time.sleep(3)

    log("!! 扫码登录超时。")
    return False


def ensure_login(client, wait_seconds: int = WAIT_SECONDS) -> bool:
    client.goto("#/workbench")
    client.page.wait_for_timeout(2500)
    client.cache_secret_url()
    for _ in range(3):
        if is_logged_in(client, verbose=True):
            return True
        client.page.wait_for_timeout(2000)
    return qr_login(client, wait_seconds)

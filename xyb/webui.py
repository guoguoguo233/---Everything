# -*- coding: utf-8 -*-
"""
本地网页控制台（webui）

- 纯标准库 http.server，单页 HTML（无外部 CDN 依赖），只监听 127.0.0.1
- 复用现有逻辑：run.py 的非交互参数（--gen-only / --submit-pending / --gen-batch /
  --submit-batch / --qc / --login-only）与 sync_archive
- 任务串行执行（Chrome/Playwright 不能在同一进程并发跑多个实例）
- 生成后可直接在网页里看预览、输入修改意见重写、确认提交

启动方式：
    python xyb\\webui.py                 # 打开浏览器并常驻
    python xyb\\webui.py --no-browser    # 只起服务
菜单里也有入口：[8] 打开网页控制台
"""
import argparse
import contextlib
import io
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8766
VERSION = "1.0.0"

SERVER = None
SERVER_PORT = None
_REAL_STDOUT = sys.stdout
_JOBS = {}
_JOB_ORDER = []
_JOB_SEQ = 0
_JOB_LOCK = threading.Lock()
_QUEUE = []
_QUEUE_EVENT = threading.Event()
_WORKER = None


# ----------------------------------------------------------------------------
# 任务执行
# ----------------------------------------------------------------------------
class _JobWriter(io.TextIOBase):
    """把任务的 stdout 同时写进任务日志。"""

    encoding = "utf-8"

    def __init__(self, job):
        self.job = job
        self._real = _REAL_STDOUT

    def write(self, s):
        if not s:
            return 0
        with _JOB_LOCK:
            self.job["log"].append(s)
            if len(self.job["log"]) > 4000:
                self.job["log"] = self.job["log"][-3000:]
        return len(s)

    def flush(self):
        pass

    def reconfigure(self, *args, **kwargs):
        """模块导入时会调用 sys.stdout.reconfigure(encoding='utf-8')，这里做空实现兼容。"""
        return None

    def isatty(self):
        return False

    def writable(self):
        return True

    def fileno(self):
        """交给底层库（如 subprocess）时，用真实控制台的 fd。"""
        try:
            return self._real.fileno()
        except Exception as exc:  # pragma: no cover
            raise OSError(str(exc)) from None


def _preload_modules():
    """在重定向 stdout 之前先导入业务模块（它们导入时会 reconfigure stdout）。"""
    for name in ("browser", "auth", "ai_writer", "run", "sync_archive"):
        try:
            __import__(name)
        except Exception:
            pass


def _new_job(action: str, label: str) -> dict:
    global _JOB_SEQ
    with _JOB_LOCK:
        _JOB_SEQ += 1
        job = {
            "id": _JOB_SEQ,
            "action": action,
            "label": label,
            "status": "queued",
            "log": [],
            "result": None,
            "created": time.time(),
            "started": None,
            "finished": None,
        }
        _JOBS[job["id"]] = job
        _JOB_ORDER.append(job["id"])
        if len(_JOB_ORDER) > 50:
            old = _JOB_ORDER[:-50]
            for i in old:
                _JOBS.pop(i, None)
            del _JOB_ORDER[:-50]
    return job


def _run_main(args: list) -> int:
    """在进程内调用 run.main（与 cli.call 等价，但只用于非交互参数）。"""
    import run

    old = sys.argv
    sys.argv = ["run.py"] + [str(a) for a in args]
    try:
        return int(run.main() or 0)
    except SystemExit as exc:
        try:
            return int(exc.code or 0)
        except (TypeError, ValueError):
            return 0
    finally:
        sys.argv = old


def _dispatch(action: str, params: dict) -> tuple[bool, str]:
    """返回 (成功?, 结果说明)。"""
    p = params or {}

    if action == "login":
        from browser import XybClient
        import auth
        try:
            with XybClient(headless=True) as c:
                ok = auth.ensure_login(c, wait_seconds=int(p.get("wait") or 300))
                if ok:
                    info = auth.loginer_info(c) or {}
                    _write_login_state(True, info.get("loginer") or "", info.get("schoolName") or "")
                    return True, f"登录成功：{info.get('loginer') or '?'} / {info.get('schoolName') or '?'}"
            _write_login_state(False)
            return False, "登录未完成（超时或未扫码）"
        except Exception as exc:
            return False, f"登录失败：{exc}"

    if action == "check":
        import auth
        from browser import XybClient
        try:
            with XybClient(headless=True) as c:
                c.goto("#/workbench")
                c.page.wait_for_timeout(2500)
                c.cache_secret_url()
                if auth.is_logged_in(c):
                    info = auth.loginer_info(c) or {}
                    _write_login_state(True, info.get("loginer") or "", info.get("schoolName") or "")
                    return True, f"已登录：{info.get('loginer') or '?'} / {info.get('schoolName') or '?'}"
                _write_login_state(False)
                return False, "未登录"
        except Exception as exc:
            return False, f"检查失败：{exc}"

    if action == "gen":
        notes = str(p.get("notes") or "").strip()
        feedback = str(p.get("feedback") or "").strip()
        merged = notes
        if feedback:
            merged = (notes + "。" if notes else "") + "【用户修改意见】" + feedback
        args = ["--gen-only", "--no-open"]
        if merged:
            args += ["--notes", merged]
        rc = _run_main(args)
        return (rc == 0), (f"生成完成（退出码 {rc}）" if rc == 0 else f"生成失败（退出码 {rc}）")

    if action == "submit_pending":
        rc = _run_main(["--submit-pending", "--yes"])
        if rc == 0:
            _sync_quiet()
        return (rc == 0), (f"提交完成（退出码 {rc}）" if rc == 0 else f"提交失败（退出码 {rc}）")

    if action == "batch_gen":
        weeks = str(p.get("weeks") or "").strip()
        args = ["--gen-batch", "--no-open"]
        if weeks:
            args += ["--weeks", weeks]
        rc = _run_main(args)
        if rc == 8:
            return False, "没有需要补交的周次（都提交过了）"
        return (rc == 0), (f"批量生成完成（退出码 {rc}）" if rc == 0 else f"批量生成失败（退出码 {rc}）")

    if action == "batch_submit":
        rc = _run_main(["--submit-batch", "--yes"])
        if rc == 0:
            _sync_quiet()
        return (rc == 0), (f"批量提交完成（退出码 {rc}）" if rc == 0 else f"批量提交失败（退出码 {rc}）")

    if action == "qc":
        rc = _run_main(["--qc"])
        if rc == 6:
            return True, "没有待提交批次（请先批量生成或写本周周志）"
        return (rc == 0), f"质检完成（退出码 {rc}）"

    if action == "sync":
        rc = _sync_quiet()
        return (rc == 0), f"归档已刷新（退出码 {rc}）"

    if action == "discard":
        try:
            paths.pending_path().unlink(missing_ok=True)
        except Exception:
            pass
        return True, "已放弃本次生成内容"

    return False, f"未知操作：{action}"


def _sync_quiet() -> int:
    import sync_archive

    try:
        return int(sync_archive.main() or 0)
    except SystemExit as exc:
        try:
            return int(exc.code or 0)
        except (TypeError, ValueError):
            return 0
    except Exception:
        traceback.print_exc()
        return 1


def _write_login_state(logged_in: bool, name: str = "", school: str = "", error: str = ""):
    data = {"logged_in": logged_in, "name": name, "school": school, "checked_at": time.time()}
    if error:
        data["error"] = error
    try:
        (paths.app_dir() / "_login_state.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _worker_loop():
    while True:
        _QUEUE_EVENT.wait(timeout=1.0)
        while True:
            with _JOB_LOCK:
                if not _QUEUE:
                    _QUEUE_EVENT.clear()
                    break
                job = _QUEUE.pop(0)
                job["status"] = "running"
                job["started"] = time.time()
            writer = _JobWriter(job)
            ok, message = False, ""
            try:
                with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                    ok, message = _dispatch(job["action"], job["params"])
            except Exception as exc:  # noqa: BLE001
                message = f"执行异常：{exc}"
                with contextlib.redirect_stdout(writer):
                    traceback.print_exc()
            with _JOB_LOCK:
                job["status"] = "done" if ok else "error"
                job["result"] = message
                job["finished"] = time.time()
            print(f"[job {job['id']}] {job['label']} -> {job['status']}: {message}", flush=True)


def _ensure_worker():
    global _WORKER
    if _WORKER is None or not _WORKER.is_alive():
        _WORKER = threading.Thread(target=_worker_loop, daemon=True, name="xyb-webui-worker")
        _WORKER.start()


def submit_job(action: str, params: dict | None = None) -> dict:
    _ensure_worker()
    labels = {
        "login": "扫码登录",
        "check": "检查登录状态",
        "gen": "生成周志",
        "submit_pending": "提交周志",
        "batch_gen": "批量生成",
        "batch_submit": "批量提交",
        "qc": "内容质检",
        "sync": "刷新归档",
        "discard": "放弃内容",
    }
    job = _new_job(action, labels.get(action, action))
    job["params"] = params or {}
    with _JOB_LOCK:
        _QUEUE.append(job)
    _QUEUE_EVENT.set()
    return job


def job_snapshot(job_id: int) -> dict | None:
    with _JOB_LOCK:
        job = _JOBS.get(int(job_id))
        if not job:
            return None
        return {
            "id": job["id"],
            "action": job["action"],
            "label": job["label"],
            "status": job["status"],
            "result": job["result"],
            "log": "".join(job["log"])[-20000:],
            "created": job["created"],
            "started": job["started"],
            "finished": job["finished"],
        }


def latest_jobs(limit: int = 8) -> list[dict]:
    with _JOB_LOCK:
        ids = _JOB_ORDER[-limit:]
        out = []
        for i in reversed(ids):
            job = _JOBS.get(i)
            if job:
                out.append({
                    "id": job["id"], "action": job["action"], "label": job["label"],
                    "status": job["status"], "result": job["result"],
                    "created": job["created"], "finished": job["finished"],
                })
        return out


# ----------------------------------------------------------------------------
# 状态与配置
# ----------------------------------------------------------------------------
def _cfg() -> dict:
    import ai_writer
    try:
        return ai_writer.load_config()
    except Exception:
        return {}


def _mask_key(key: str) -> str:
    key = key or ""
    if len(key) <= 8:
        return "****" if key else ""
    return key[:6] + "****" + key[-4:]


def _login_state() -> dict:
    try:
        p = paths.app_dir() / "_login_state.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            data["age_seconds"] = int(time.time() - float(data.get("checked_at") or 0))
            return data
    except Exception:
        pass
    return {"logged_in": None, "age_seconds": None}


def _pending() -> dict | None:
    try:
        p = paths.pending_path()
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        text = data.get("content_text") or ""
        return {
            "week": data.get("week"),
            "startDate": data.get("startDate"),
            "endDate": data.get("endDate"),
            "title": data.get("title"),
            "words": len([c for c in text if c.strip()]),
            "text": text,
            "generated_at": data.get("generated_at"),
        }
    except Exception:
        return None


def _status() -> dict:
    cfg = _cfg()
    ai = cfg.get("ai") or {}
    qr = paths.qr_png_path()
    try:
        qr_mtime = qr.stat().st_mtime
    except Exception:
        qr_mtime = 0
    return {
        "version": VERSION,
        "mode": "EXE 免安装版" if paths.is_frozen() else "源码版（Python）",
        "app_dir": str(paths.app_dir()),
        "config_path": str(paths.config_path()),
        "key_masked": _mask_key(str(ai.get("api_key") or "")),
        "key_configured": bool(ai.get("api_key")) and str(ai.get("api_key")) != "sk-在这里填你的DeepSeekKey",
        "model": ai.get("model") or "",
        "login": _login_state(),
        "pending": _pending(),
        "qr_mtime": qr_mtime,
        "qr_exists": qr.exists(),
        "jobs": latest_jobs(),
        "busy": any(j["status"] in ("queued", "running") for j in latest_jobs(50)),
        "record_dir": str(paths.app_dir() / "周志记录"),
    }


def _save_config(payload: dict) -> dict:
    import ai_writer

    cfg = _cfg()
    ai = cfg.setdefault("ai", {})
    intern = cfg.setdefault("internship", {})
    style = cfg.setdefault("style", {})

    key = str(payload.get("api_key") or "").strip()
    if key:
        if len(key) < 20 or not key.startswith("sk-"):
            return {"ok": False, "error": "Key 看起来不完整（应以 sk- 开头、30 多字符）"}
        ai["api_key"] = key
    model = str(payload.get("model") or "").strip()
    if model:
        import re
        if not re.fullmatch(r"[A-Za-z0-9._:/\-]{2,80}", model):
            return {"ok": False, "error": "模型名不合法（只允许字母数字 . _ : / -）"}
        ai["model"] = model
    for field in ("company", "position", "work_summary"):
        if field in payload:
            intern[field] = str(payload.get(field) or "").strip()
    for field, key_name in (("min_words", "min_words"), ("max_words", "max_words")):
        if payload.get(field):
            try:
                style[key_name] = int(payload[field])
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{field} 必须是数字"}

    try:
        paths.config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "error": f"保存失败：{exc}"}
    return {"ok": True}


# ----------------------------------------------------------------------------
# HTTP 层
# ----------------------------------------------------------------------------
HTML_PAGE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>校友邦周志助手 · 网页控制台</title>
<style>
:root{--bg:#f4f6fb;--card:#fff;--fg:#1f2430;--muted:#69707f;--pri:#3b6ef6;--ok:#16a34a;--warn:#d97706;--err:#dc2626;--line:#e6e9f2}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg)}
header{background:#fff;border-bottom:1px solid var(--line);padding:14px 22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{font-size:18px;margin:0}
header .ver{color:var(--muted);font-size:12px}
main{max-width:1180px;margin:0 auto;padding:18px;display:grid;grid-template-columns:1.25fr .95fr;gap:16px}
@media(max-width:960px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:0 2px 10px rgba(20,30,60,.04)}
.card h2{font-size:15px;margin:0 0 12px}
.badges{display:flex;gap:10px;flex-wrap:wrap}
.badge{font-size:13px;padding:6px 10px;border-radius:999px;background:#eef2ff;color:#334}
.badge.ok{background:#e8f7ee;color:#15803d}
.badge.warn{background:#fef3c7;color:#92400e}
.badge.err{background:#fee2e2;color:#991b1b}
.btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
button{font:inherit;border:1px solid var(--line);background:#fff;border-radius:10px;padding:9px 14px;cursor:pointer;color:var(--fg)}
button:hover{border-color:#c3cbe4}
button.primary{background:var(--pri);border-color:var(--pri);color:#fff}
button.ok{background:var(--ok);border-color:var(--ok);color:#fff}
button.warn{background:var(--warn);border-color:var(--warn);color:#fff}
button.ghost{background:#f7f8fc}
button:disabled{opacity:.5;cursor:not-allowed}
label{display:block;font-size:13px;color:var(--muted);margin:10px 0 4px}
input,textarea,select{width:100%;font:inherit;padding:9px 10px;border:1px solid var(--line);border-radius:9px;background:#fbfcff}
textarea{min-height:110px;resize:vertical}
pre{background:#0f172a;color:#dbe3f4;border-radius:12px;padding:12px;font-size:12px;line-height:1.55;overflow:auto;max-height:320px;white-space:pre-wrap;word-break:break-word;margin:10px 0 0}
.preview{white-space:pre-wrap;line-height:1.8;font-size:14px;background:#fbfcff;border:1px dashed var(--line);border-radius:10px;padding:12px;max-height:380px;overflow:auto}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.muted{color:var(--muted);font-size:12px}
.qrbox{display:flex;gap:14px;align-items:center;margin-top:10px}
.qrbox img{width:180px;height:180px;border:1px solid var(--line);border-radius:10px;background:#fff;image-rendering:pixelated}
.msg{margin-top:10px;font-size:13px;min-height:18px}
.msg.ok{color:var(--ok)} .msg.err{color:var(--err)}
.right{display:flex;flex-direction:column;gap:16px}
.tabs{display:flex;gap:6px;margin-bottom:8px}
.tabs button{padding:6px 10px;font-size:13px}
.tabs button.on{background:#eef2ff;border-color:#c7d2fe}
.hide{display:none}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:500}
</style>
</head>
<body>
<header>
  <h1>校友邦周志助手 · 网页控制台</h1>
  <span class="ver" id="ver">v-</span>
  <span class="ver" id="mode"></span>
  <span style="flex:1"></span>
  <span class="muted" id="appdir"></span>
</header>

<main>
  <div class="left">
    <div class="card">
      <h2>当前状态</h2>
      <div class="badges">
        <span class="badge" id="loginBadge">登录状态：检测中…</span>
        <span class="badge" id="keyBadge">AI Key：-</span>
        <span class="badge" id="modelBadge">模型：-</span>
      </div>
      <div class="btns">
        <button class="primary" onclick="act('login')">扫码登录 / 检查状态</button>
        <button onclick="act('check')">只检查状态</button>
        <button onclick="act('sync')">刷新归档</button>
      </div>
      <div id="loginExtra"></div>
    </div>

    <div class="card" style="margin-top:16px">
      <h2>写本周周志</h2>
      <label>本周真实素材（可留空；写几句实际做的事，内容会更像你自己写的）</label>
      <textarea id="notes" placeholder="例如：本周主要在做排序算法的边界情况处理，和同事讨论后补了两处判断"></textarea>
      <div class="btns">
        <button class="primary" onclick="gen()">生成本周周志</button>
        <button class="ghost" onclick="clearJob()">清空日志</button>
      </div>
      <div class="msg" id="genMsg"></div>

      <div id="pendingBox" class="hide" style="margin-top:14px">
        <h2 style="font-size:14px">待提交内容（还没提交）</h2>
        <div class="row">
          <div class="muted" id="pendingMeta"></div>
          <span style="flex:1"></span>
          <button class="ok" onclick="submitPending()">提交到校友邦</button>
          <button class="warn" onclick="revise()">按修改意见重写</button>
          <button class="ghost" onclick="discard()">放弃</button>
        </div>
        <label>修改意见（点"按修改意见重写"前填写；例如：多写具体细节、语气再朴素些、少写套话）</label>
        <input id="feedback" placeholder="填写修改意见后点“按修改意见重写”">
        <div class="preview" id="pendingText" style="margin-top:10px"></div>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <h2>运行日志</h2>
      <div class="muted" id="jobLabel">空闲</div>
      <pre id="log">（点击上面的按钮开始操作）</pre>
    </div>
  </div>

  <div class="right">
    <div class="card">
      <h2>批量补交</h2>
      <label>要补的周次（如 1-9，留空=全部未提交）</label>
      <input id="weeks" placeholder="1-9">
      <div class="btns">
        <button onclick="act('batch_gen',{weeks:val('weeks')})">批量生成</button>
        <button onclick="act('qc')">质检</button>
        <button class="ok" onclick="act('batch_submit')">批量提交</button>
      </div>
      <div class="muted" style="margin-top:8px">建议顺序：批量生成 → 打开 <code>周志记录\待提交预览_*.md</code> 审核 → 质检 → 批量提交。</div>
    </div>

    <div class="card">
      <h2>配置</h2>
      <label>DeepSeek API Key（留空=不修改；粘贴后请核对长度）</label>
      <input id="cfgKey" placeholder="sk-..." autocomplete="off">
      <label>模型</label>
      <input id="cfgModel" placeholder="deepseek-chat">
      <label>实习单位（留空=自动从校友邦读取）</label>
      <input id="cfgCompany" placeholder="自动读取">
      <label>岗位（留空=自动读取）</label>
      <input id="cfgPosition" placeholder="自动读取">
      <label>主要工作内容（一句话）</label>
      <input id="cfgSummary" placeholder="例如：参与后端接口开发与维护">
      <div class="row">
        <div style="flex:1"><label>每篇最少字数</label><input id="cfgMin" type="number" min="100" step="10"></div>
        <div style="flex:1"><label>每篇最多字数</label><input id="cfgMax" type="number" min="200" step="10"></div>
      </div>
      <div class="btns">
        <button class="primary" onclick="saveCfg()">保存配置</button>
        <button class="ghost" onclick="loadCfg()">重新载入</button>
      </div>
      <div class="msg" id="cfgMsg"></div>
      <div class="muted" id="cfgPath" style="margin-top:8px"></div>
    </div>

    <div class="card">
      <h2>说明</h2>
      <div class="muted" style="line-height:1.75">
        · 本页面只监听 127.0.0.1，外部无法访问；关闭命令行窗口即停止。<br>
        · 登录二维码会在扫码登录时显示在下方；必须用「校友邦学生版小程序」扫一扫。<br>
        · 生成后先看预览，确认无误再提交；提交前请核对内容真实性。<br>
        · 数据（配置/登录态/归档）都在程序目录，换电脑拷走整个文件夹即可。
      </div>
      <div class="qrbox" id="qrbox">
        <img id="qrImg" class="hide" alt="登录二维码">
        <div class="muted" id="qrTip">扫码登录时，这里会显示二维码</div>
      </div>
    </div>
  </div>
</main>

<script>
let currentJob = null, pollTimer = null;

function val(id){return (document.getElementById(id).value||'').trim();}
function esc(s){return (s||'').replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function fmt(n){return new Date(n*1000).toLocaleTimeString();}

async function api(path, method, body){
  const opt = {method: method||'GET', headers:{'Content-Type':'application/json'}};
  if(body) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  return await r.json();
}

async function refreshStatus(){
  try{
    const s = await api('/api/status');
    document.getElementById('ver').textContent = 'v' + s.version;
    document.getElementById('mode').textContent = s.mode;
    document.getElementById('appdir').textContent = s.app_dir;
    document.getElementById('cfgPath').textContent = '配置文件：' + s.config_path;

    const lb = document.getElementById('loginBadge');
    const L = s.login || {};
    if(L.logged_in === true){
      lb.className = 'badge ok';
      lb.textContent = '登录状态：已登录 — ' + (L.name||'?') + ' / ' + (L.school||'?');
    } else if(L.logged_in === false){
      lb.className = 'badge err';
      lb.textContent = '登录状态：未登录（点左侧按钮扫码）';
    } else {
      lb.className = 'badge warn';
      lb.textContent = '登录状态：未检测';
    }

    const kb = document.getElementById('keyBadge');
    kb.className = 'badge ' + (s.key_configured ? 'ok' : 'err');
    kb.textContent = 'AI Key：' + (s.key_configured ? ('已配置 ' + (s.key_masked||'')) : '未配置');
    document.getElementById('modelBadge').textContent = '模型：' + (s.model || '-');

    // 待提交内容
    if(s.pending){
      document.getElementById('pendingBox').classList.remove('hide');
      document.getElementById('pendingMeta').textContent =
        '第' + s.pending.week + '周 · ' + s.pending.title + ' · 约' + s.pending.words + '字 · 生成于 ' + (s.pending.generated_at||'');
      document.getElementById('pendingText').textContent = s.pending.text || '';
    } else {
      document.getElementById('pendingBox').classList.add('hide');
    }

    // 二维码
    const img = document.getElementById('qrImg');
    if(s.qr_exists && s.qr_mtime > 0){
      img.classList.remove('hide');
      document.getElementById('qrTip').textContent = '用「校友邦学生版小程序」扫一扫（不是微信扫一扫）；二维码会自动更新';
      img.src = '/qr.png?t=' + Math.floor(s.qr_mtime);
    } else {
      img.classList.add('hide');
      document.getElementById('qrTip').textContent = '扫码登录时，这里会显示二维码';
    }
    return s;
  }catch(e){
    document.getElementById('loginBadge').textContent = '连接本地服务失败：' + e;
  }
}

async function loadCfg(){
  const s = await refreshStatus();
  document.getElementById('cfgModel').value = s.model || '';
  try{
    const c = await api('/api/config');
    document.getElementById('cfgCompany').value = (c.internship&&c.internship.company)||'';
    document.getElementById('cfgPosition').value = (c.internship&&c.internship.position)||'';
    document.getElementById('cfgSummary').value = (c.internship&&c.internship.work_summary)||'';
    document.getElementById('cfgMin').value = (c.style&&c.style.min_words)||320;
    document.getElementById('cfgMax').value = (c.style&&c.style.max_words)||620;
    document.getElementById('cfgKey').value = '';
  }catch(e){}
}

async function saveCfg(){
  const body = {
    api_key: val('cfgKey'), model: val('cfgModel'),
    company: val('cfgCompany'), position: val('cfgPosition'), work_summary: val('cfgSummary'),
    min_words: val('cfgMin'), max_words: val('cfgMax')
  };
  const r = await api('/api/config','POST',body);
  const m = document.getElementById('cfgMsg');
  if(r.ok){ m.className='msg ok'; m.textContent='已保存配置'; }
  else { m.className='msg err'; m.textContent='保存失败：' + (r.error||''); }
  refreshStatus();
}

async function act(action, params){
  document.getElementById('genMsg').textContent = '';
  const r = await api('/api/action','POST', Object.assign({action:action}, params||{}));
  if(!r.ok){ document.getElementById('log').textContent = '提交任务失败：' + (r.error||''); return; }
  currentJob = r.job_id;
  document.getElementById('jobLabel').textContent = '任务 #' + r.job_id + ' 运行中…';
  document.getElementById('log').textContent = '';
  poll();
  setTimeout(refreshStatus, 500);
}

function gen(){
  document.getElementById('genMsg').textContent = '正在生成，请稍候（约 15~40 秒）…';
  act('gen', {notes: val('notes')});
}
function revise(){
  const fb = val('feedback');
  if(!fb){ alert('请先在“修改意见”里写一句要求'); return; }
  document.getElementById('genMsg').textContent = '正在按修改意见重写…';
  act('gen', {notes: val('notes'), feedback: fb});
}
function submitPending(){ if(confirm('确认把这篇提交到校友邦？')) act('submit_pending'); }
function discard(){ if(confirm('放弃这次生成的内容？（不会提交）')) act('discard'); }
function clearJob(){ document.getElementById('log').textContent=''; }

async function poll(){
  if(!currentJob){ clearTimeout(pollTimer); return; }
  try{
    const j = await api('/api/jobs/' + currentJob);
    const log = document.getElementById('log');
    log.textContent = j.log || '';
    log.scrollTop = log.scrollHeight;
    document.getElementById('jobLabel').textContent =
      '任务 #' + j.id + ' · ' + j.label + ' · ' + ({queued:'排队中',running:'运行中',done:'完成',error:'出错'}[j.status] || j.status);
    if(j.status === 'done' || j.status === 'error'){
      document.getElementById('genMsg').textContent = (j.result || '');
      document.getElementById('genMsg').className = 'msg ' + (j.status === 'done' ? 'ok' : 'err');
      currentJob = null;
      refreshStatus();
      if(j.action === 'submit_pending' || j.action === 'discard' || j.action === 'batch_submit') refreshStatus();
      return;
    }
  }catch(e){ /* ignore */ }
  pollTimer = setTimeout(poll, 900);
}

(async function init(){
  await refreshStatus();
  loadCfg();
  setInterval(refreshStatus, 3000);
})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "xyb-webui/1.0"

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/":
            self._send(200, HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/qr.png":
            p = paths.qr_png_path()
            if p.exists():
                self._send(200, p.read_bytes(), "image/png")
            else:
                self._send(404, b"no qr", "text/plain; charset=utf-8")
            return
        if path == "/api/status":
            self._json(_status())
            return
        if path == "/api/config":
            cfg = _cfg()
            self._json(cfg)
            return
        if path.startswith("/api/jobs/"):
            try:
                jid = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self._json({"error": "bad job id"}, 400)
                return
            snap = job_snapshot(jid)
            if not snap:
                self._json({"error": "job not found"}, 404)
                return
            self._json(snap)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/action":
            action = str(payload.get("action") or "")
            params = {k: v for k, v in payload.items() if k != "action"}
            job = submit_job(action, params)
            self._json({"ok": True, "job_id": job["id"]})
            return
        if path == "/api/config":
            self._json(_save_config(payload))
            return
        self._json({"ok": False, "error": "not found"}, 404)


def start(port: int = DEFAULT_PORT, open_browser: bool = True, quiet: bool = False) -> str:
    """启动网页控制台（后台线程），返回访问地址。"""
    global SERVER, SERVER_PORT
    if SERVER is not None:
        return f"http://{HOST}:{SERVER_PORT}/"

    last_err = None
    for p in range(port, port + 10):
        try:
            srv = ThreadingHTTPServer((HOST, p), Handler)
            SERVER, SERVER_PORT = srv, p
            break
        except OSError as exc:
            last_err = exc
            continue
    if SERVER is None:
        raise RuntimeError(f"无法启动本地网页服务（端口 {port}-{port + 9} 都被占用）：{last_err}")

    _ensure_worker()
    _preload_modules()
    threading.Thread(target=SERVER.serve_forever, daemon=True, name="xyb-webui-server").start()
    url = f"http://{HOST}:{SERVER_PORT}/"
    if not quiet:
        print(f"网页控制台已启动：{url}")
        print("（只监听 127.0.0.1，局域网/外网无法访问；关闭该窗口即停止）")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return url


def stop():
    global SERVER
    if SERVER is not None:
        try:
            SERVER.shutdown()
            SERVER.server_close()
        except Exception:
            pass
        SERVER = None


def main() -> int:
    ap = argparse.ArgumentParser(description="校友邦周志助手 · 本地网页控制台")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    url = start(port=args.port, open_browser=not args.no_browser)
    print(f"在浏览器打开：{url}")
    print("按 Ctrl+C 停止服务。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在停止…")
        stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

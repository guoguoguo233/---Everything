# -*- coding: utf-8 -*-
"""
校友邦实习周志 · 自动填写工具

流程：复用登录态 → 自动读取实习计划/单位/岗位/周次 → AI 生成周志正文
     → 终端预览确认 → 提交到校友邦 → 追加写入本地 MD 归档

用法示例：
    python run.py                      # 写“当前这一周”（已提交则跳过）
    python run.py --week 9             # 指定第 9 周
    python run.py --all                # 把所有未提交的周依次补上
    python run.py --notes "这周主要调了排序算法的边界case"   # 给 AI 加点真实素材
    python run.py --dry-run            # 只生成+预览，不提交
    python run.py --yes                # 跳过确认（谨慎）
    python run.py --test-submit        # 自检：用草稿态验证提交链路，随后删除
"""
import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser import XybClient, XybError  # noqa: E402
import auth  # noqa: E402
import ai_writer  # noqa: E402
import paths  # noqa: E402

ROOT = paths.app_dir()
PLACEHOLDER = "sk-在这里填你的DeepSeekKey"
PENDING_PATH = paths.pending_path()
BATCH_PATH = paths.batch_path()


def parse_weeks_arg(text: str, all_weeks: list[int]) -> list[int]:
    """'1-9' / '1,3,5' / '9-1' -> 周次列表"""
    text = (text or "").strip()
    if not text:
        return all_weeks
    out = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                step = 1 if b >= a else -1
                out.extend(range(a, b + step, step))
            except ValueError:
                continue
        elif part.isdigit():
            out.append(int(part))
    return [w for w in all_weeks if w in set(out)]


# ---------------- 工具 ----------------
def log(*a):
    print(*a, flush=True)


def parse_date(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def to_dot(s: str) -> str:
    """2026-10-19 -> 2026.10.19"""
    d = parse_date(s)
    return d.strftime("%Y.%m.%d") if d else s


def ask_yes(question: str, default: bool = False) -> bool:
    tip = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"{question} {tip} ").strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes", "是", "好", "确认")


def ensure_ai_key(cfg: dict) -> dict:
    key = str((cfg.get("ai") or {}).get("api_key") or "").strip()
    if key and key != PLACEHOLDER:
        return cfg
    log("")
    log("还没有配置 DeepSeek API Key。")
    log("  1) 直接粘贴 Key（会写入本地 xyb/config.json，不会上传到任何地方）")
    log("  2) 留空跳过（本次无法生成正文）")
    try:
        import getpass
        val = getpass.getpass("请粘贴 DeepSeek API Key（输入不回显）: ").strip()
    except Exception:
        val = input("请粘贴 DeepSeek API Key: ").strip()
    if val:
        cfg.setdefault("ai", {})["api_key"] = val
        path = ROOT / "xyb" / "config.json"
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"已保存到 {path}")
    return cfg


# ---------------- 读取实习上下文 ----------------
def pick_term(terms_resp: dict, today: date) -> tuple[str, str]:
    """返回 (schoolTermId, 学期名)"""
    years = ((terms_resp.get("data") or {}).get("datas")) or []
    cands = []
    for y in years:
        for s in (y.get("sons") or []):
            cands.append({
                "id": str(s.get("id")),
                "name": f"{y.get('name')} {s.get('name')}",
                "start": parse_date(s.get("startDate")),
                "end": parse_date(s.get("endDate")),
            })
    for c in cands:
        if c["start"] and c["end"] and c["start"] <= today <= c["end"]:
            return c["id"], c["name"]
    past = [c for c in cands if c["start"] and c["start"] <= today]
    if past:
        c = max(past, key=lambda x: x["start"])
        return c["id"], c["name"]
    return "", ""


def fetch_weeks(client, trainee_id: int) -> list[dict]:
    ym = client.api("/practice/student/blogs/loadWeeklyYearMonthDates!loadYearMonths.action",
                    {"traineeId": trainee_id})
    weeks = []
    for y in (ym.get("data") or []):
        for mo in (y.get("months") or []):
            r = client.api("/practice/student/blogs/loadWeeklyYearMonthDates!loadDates.action",
                           {"traineeId": trainee_id, "year": y.get("id"), "month": mo.get("id")})
            for w in (r.get("data") or []):
                weeks.append(w)
    weeks.sort(key=lambda w: (w.get("week") or 0))
    return weeks


def fetch_unit_info(client, project_id) -> dict:
    if not project_id:
        return {}
    try:
        r = client.api("/practice/student/practiceplan/independent/loadTrainPostDetail.action",
                       {"projectId": project_id, "page": 1, "pageSize": 10})
        data = r.get("data") or {}
        rows = data.get("list") if isinstance(data, dict) else None
        if isinstance(rows, list) and rows:
            return rows[0]
        if isinstance(data, dict) and data.get("enterpriseName"):
            return data
    except Exception as e:
        log("  读取岗位信息失败（不影响生成）:", str(e)[:80])
    return {}


def resolve_context(client) -> dict:
    info = auth.loginer_info(client)
    student_id = info.get("studentId")
    if not student_id:
        raise XybError("未能获取登录身份，请重新运行并扫码登录。")
    log(f"登录身份：{info.get('loginer') or '?'} | {info.get('schoolName') or '?'} | 学号ID {student_id}")

    today = date.today()
    terms = client.api("/common/plugin/term/loadTerms.action", {"type": 27})
    term_id, term_name = pick_term(terms, today)
    if not term_id:
        raise XybError("找不到当前学期，站点数据结构可能变了。")

    tasks = client.api("/practice/student/blogs/loadTaskList.action", {"schoolTermId": term_id, "type": "w"})
    rows = tasks.get("data") if isinstance(tasks.get("data"), list) else []
    row = next((r for r in rows if str(r.get("needWrite")).lower() in ("true", "1") or r.get("needWrite") is True), None)
    if not row:
        row = rows[0] if rows else None
    if not row:
        raise XybError("当前学期没有可写的实习计划（可能是还没开始或已结束）。")
    trainee_id = row.get("traineeId")
    log(f"实习计划：{row.get('plan')} | 项目：{row.get('project')} | traineeId={trainee_id}")
    log(f"实习时间：{row.get('startDate')} ~ {row.get('endDate')} | 状态：{row.get('remark')}")

    ti = (client.api("/practice/student/blogs/loadTraineeInfo.action",
                     {"traineeId": trainee_id, "type": "w"}).get("data")) or {}
    log(f"周志要求：至少 {ti.get('wordCount')} 字 | 周期 {ti.get('cycle')} 周 | 标题要求：{ti.get('remarkFormat')}")

    # 单位/岗位
    unit = {}
    try:
        pt = client.api("/practice/student/task/loadPracticeTask.action", {})
        plans = ((pt.get("data") or {}).get("list")) or []
        if plans:
            unit = fetch_unit_info(client, plans[0].get("projectId"))
    except Exception as e:
        log("  读取实习任务失败（不影响生成）:", str(e)[:80])
    if unit:
        log(f"实习单位：{unit.get('enterpriseName')} | 岗位：{unit.get('postName')} | "
            f"地点：{unit.get('location')} | 职责：{unit.get('responsibilities')}")

    weeks = fetch_weeks(client, trainee_id)
    log(f"周次共 {len(weeks)} 周，未提交 {sum(1 for w in weeks if is_unsubmitted(w))} 周")

    return {
        "today": today,
        "term_id": term_id, "term_name": term_name,
        "trainee_id": trainee_id, "row": row,
        "trainee_info": ti, "unit": unit, "weeks": weeks,
    }


def is_unsubmitted(w: dict) -> bool:
    blog = w.get("blogCount") or 0
    try:
        blog = int(blog)
    except (TypeError, ValueError):
        blog = 0
    return blog == 0 and str(w.get("status")) == "2"


def week_label(w: dict) -> str:
    return f"第{w.get('week')}周（{w.get('startDate')} ~ {w.get('endDate')}）"


# ---------------- 生成 ----------------
def build_cfg(cfg: dict, ctx: dict) -> dict:
    cfg = json.loads(json.dumps(cfg))  # 深拷贝
    unit = ctx.get("unit") or {}
    intern = cfg.setdefault("internship", {})
    if unit.get("enterpriseName"):
        intern["company"] = unit["enterpriseName"]
    if unit.get("postName"):
        intern["position"] = unit["postName"]
    if unit.get("responsibilities"):
        intern["work_summary"] = unit["responsibilities"].replace("\n", "；")
    if unit.get("industry") and not intern.get("industry"):
        intern["industry"] = unit["industry"]

    style = cfg.setdefault("style", {})
    least = ctx["trainee_info"].get("wordCount")
    try:
        least = int(least)
    except (TypeError, ValueError):
        least = 0
    if least > 0:
        style["min_words"] = max(int(style.get("min_words") or 0), least + 20)
    # 标题模板：从学校要求里抠出「生产实习第X周」
    import re
    fmt = str(ctx["trainee_info"].get("remarkFormat") or "")
    m = re.search(r"([\u4e00-\u9fa5A-Za-z]{2,10})第\s*[Xx×]\s*周", fmt)
    if m:
        style["title_pattern"] = f"{m.group(1)}第{{index}}周"
    return cfg


def generate_one(cfg: dict, ctx: dict, week: dict, notes: str) -> dict:
    week_ctx = {
        "index": week.get("week"),
        "startDate": to_dot(week.get("startDate")),
        "endDate": to_dot(week.get("endDate")),
        "deadline": ctx["row"].get("deadline") or "",
    }
    return ai_writer.generate(cfg, week_ctx, notes=notes)


# ---------------- 提交 ----------------
def submit_journal(client, ctx: dict, week: dict, title: str, content_html: str, status: str = "1") -> dict:
    params = {
        "type": "w",
        "traineeId": ctx["trainee_id"],
        "title": title,
        "content": content_html,
        "status": status,
        "visicty": "2",
        "startDate": to_dot(week.get("startDate")),
        "endDate": to_dot(week.get("endDate")),
        "addFilejson": "[]",
    }
    res = client.api("/practice/student/blogs/save.action", params)
    if str(res.get("code")) != "200":
        raise XybError(f"提交失败：code={res.get('code')} msg={res.get('msg')}")
    try:
        client.api("/practice/student/DeliverValue!blog.action", {"traineeId": ctx["trainee_id"]})
    except Exception:
        pass
    return res


def verify_submitted(client, ctx: dict, title: str) -> dict | None:
    r = client.api("/practice/student/blogs/loadBlogsList.action",
                   {"schoolTermId": ctx["term_id"], "type": "w", "page": 1, "pageSize": 30})
    for item in ((r.get("data") or {}).get("list") or []):
        if (item.get("title") or "").strip() == title.strip():
            return item
    return None


# ---------------- MD 归档 ----------------
def verify_draft(client, ctx: dict, title: str) -> dict | None:
    r = client.api("/practice/student/blogs/loadDraftList.action",
                   {"schoolTermId": ctx["term_id"], "type": "w"})
    rows = r.get("data") if isinstance(r.get("data"), list) else []
    for item in rows:
        if (item.get("title") or "").strip() == title.strip():
            return item
    return None


def append_record(cfg: dict, week: dict, title: str, content_text: str, extra: dict):
    rec = cfg.get("record") or {}
    folder = ROOT / (rec.get("dir") or "周志记录")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (rec.get("file") or "周志记录.md")
    if not path.exists():
        path.write_text("# 实习周志记录\n\n> 本文件由本地自动填写工具生成，用于回溯已提交的周志内容。\n\n---\n\n",
                        encoding="utf-8")
    words = len([ch for ch in content_text if ch.strip()])
    block = [
        f"## 第{week.get('week')}周（{to_dot(week.get('startDate'))} ~ {to_dot(week.get('endDate'))}）\n",
        f"- 标题：{title}",
        f"- 字数：约 {words} 字",
        f"- 提交时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 接口返回：code={extra.get('code')}，{extra.get('msg')}",
        f"- 系统状态：{extra.get('review_status') or '已提交'}",
        "",
        content_text.strip(),
        "",
        "---",
        "",
    ]
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")
    return path


# ---------------- 主流程 ----------------
def write_preview_md(cfg: dict, ctx: dict, items: list[dict]) -> Path:
    rec = cfg.get("record") or {}
    folder = ROOT / (rec.get("dir") or "周志记录")
    folder.mkdir(parents=True, exist_ok=True)
    idxs = [int(i["week"]) for i in items]
    path = folder / f"待提交预览_第{min(idxs)}-{max(idxs)}周.md"
    lines = [
        "# 周志批量预览（尚未提交）\n",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 实习单位：{(ctx.get('unit') or {}).get('enterpriseName') or cfg['internship'].get('company')}",
        f"- 岗位：{(ctx.get('unit') or {}).get('postName') or cfg['internship'].get('position')}",
        f"- 篇数：{len(items)}",
        f"- 字数要求：≥ {cfg['style'].get('min_words')} 字",
        "",
        "---",
        "",
    ]
    for it in items:
        words = len([c for c in it["content_text"] if c.strip()])
        lines += [
            f"## 第{it['week']}周（{it['startDate']} ~ {it['endDate']}）",
            "",
            f"- 标题：{it['title']}",
            f"- 字数：约 {words} 字",
            "",
            it["content_text"].strip(),
            "",
            "---",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


ALLOWED_EN = {"python", "c", "cpp", "cplusplus", "vs", "ide", "bug", "bugfix"}
CJK = re.compile(r"[\u4e00-\u9fa5]")


def qc_items(items: list[dict], min_words: int) -> list[str]:
    """质检：字数、英文残留、中文占比、相互重复度"""
    problems = []
    import difflib
    for it in items:
        txt = it.get("content_text") or ""
        w = it.get("words") or len([c for c in txt if c.strip()])
        if w < min_words:
            problems.append(f"第{it['week']}周 字数不足：{w} < {min_words}")
        stray = [m for m in re.findall(r"[A-Za-z]{3,}", txt) if m.lower() not in ALLOWED_EN]
        if stray:
            problems.append(f"第{it['week']}周 疑似英文残留：{sorted(set(stray))[:6]}")
        cjk = len(CJK.findall(txt))
        if txt and cjk / max(1, len(txt)) < 0.55:
            problems.append(f"第{it['week']}周 中文占比偏低：{cjk}/{len(txt)}")
        if "…" in txt or "..." in txt:
            problems.append(f"第{it['week']}周 正文含省略号，可能被截断")
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a = (items[i].get("content_text") or "")[:400]
            b = (items[j].get("content_text") or "")[:400]
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if ratio > 0.72:
                problems.append(f"第{items[i]['week']}周 与 第{items[j]['week']}周 重复度过高：{ratio:.0%}")
    return problems


def run_batch(args, cfg: dict, ctx: dict, client) -> int:
    weeks = ctx["weeks"]
    all_idx = [int(w.get("week") or 0) for w in weeks]
    wanted = set(parse_weeks_arg(args.weeks, all_idx))

    # ---- 质检 ----
    if args.qc:
        if not BATCH_PATH.exists():
            log("没有待提交批次。")
            return 6
        batch = json.loads(BATCH_PATH.read_text(encoding="utf-8"))
        items = batch.get("items") or []
        probs = qc_items(items, int(cfg["style"].get("min_words") or 0))
        log(f"待提交 {len(items)} 篇，质检结果：")
        if probs:
            for p in probs:
                log("  [问题]", p)
        else:
            log("  全部通过（字数、英文残留、重复度均正常）")
        return 0

    # ---- 重生成某一周 ----
    if args.regen_week:
        if not BATCH_PATH.exists():
            log("没有待提交批次。")
            return 6
        batch = json.loads(BATCH_PATH.read_text(encoding="utf-8"))
        items = batch.get("items") or []
        target = next((it for it in items if int(it["week"]) == args.regen_week), None)
        week = next((w for w in weeks if int(w.get("week") or 0) == args.regen_week), None)
        if not target or not week:
            log(f"批次里没有第{args.regen_week}周。")
            return 3
        log(f"重新生成第{args.regen_week}周…")
        gen = generate_one(cfg, ctx, week, args.notes)
        target.update({"title": gen["title"], "content_html": gen["content_html"],
                       "content_text": gen["content_text"],
                       "words": len([c for c in gen["content_text"] if c.strip()])})
        BATCH_PATH.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        md = write_preview_md(cfg, ctx, items)
        log(f"  新标题：{gen['title']} | 字数：约 {target['words']}")
        log(f"  已更新批次与预览文件：{md}")
        return 0

    if args.gen_batch:
        targets = [w for w in weeks
                   if int(w.get("week") or 0) in wanted and is_unsubmitted(w)]
        if not targets:
            log("没有需要生成的周次。")
            return 8
        log(f"将生成 {len(targets)} 篇：" + "、".join(f"第{int(w.get('week'))}周" for w in targets))
        items = []
        for w in targets:
            idx = int(w.get("week") or 0)
            log("")
            log(f"—— 生成第{idx}周 ——")
            try:
                gen = generate_one(cfg, ctx, w, args.notes)
            except ai_writer.AIWriterError as e:
                log(f"  第{idx}周生成失败：{e}")
                continue
            words = len([c for c in gen["content_text"] if c.strip()])
            log(f"  标题：{gen['title']} | 字数：约 {words}")
            log(f"  首句：{gen['content_text'].strip()[:60]}…")
            items.append({
                "week": idx,
                "startDate": to_dot(w.get("startDate")),
                "endDate": to_dot(w.get("endDate")),
                "traineeId": ctx["trainee_id"],
                "title": gen["title"],
                "content_html": gen["content_html"],
                "content_text": gen["content_text"],
                "words": words,
            })
        if not items:
            log("全部生成失败。")
            return 5
        BATCH_PATH.write_text(json.dumps({"generated_at": datetime.now().isoformat(),
                                          "items": items}, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        md = write_preview_md(cfg, ctx, items)
        log("")
        log(f"已生成 {len(items)} 篇，预览文件：{md}")
        log(f"待提交数据：{BATCH_PATH}")
        log("看过没问题后执行： python xyb\\run.py --submit-batch --yes")
        return 0

    # ---- 提交整批 ----
    if not BATCH_PATH.exists():
        log("没有找到待提交批次，请先运行 --gen-batch。")
        return 6
    batch = json.loads(BATCH_PATH.read_text(encoding="utf-8"))
    items = batch.get("items") or []
    log(f"待提交 {len(items)} 篇：" + "、".join(f"第{i['week']}周" for i in items))
    if not args.yes and not ask_yes(f"确认把这 {len(items)} 篇全部提交到校友邦？"):
        log("已取消。")
        return 0

    ok, failed = [], []
    for it in items:
        week = {"week": it["week"], "startDate": it["startDate"], "endDate": it["endDate"]}
        log("")
        log(f"—— 提交第{it['week']}周：{it['title']} ——")
        try:
            res = submit_journal(client, ctx, week, it["title"], it["content_html"], status="1")
        except Exception as e:
            log(f"  失败：{str(e)[:160]}")
            failed.append({"week": it["week"], "reason": str(e)[:160]})
            continue
        log(f"  提交返回：code={res.get('code')} msg={res.get('msg')}")
        item = verify_submitted(client, ctx, it["title"])
        review = (item or {}).get("reviewStatus")
        if item:
            log(f"  已确认：{item.get('title')} | {review or '已提交'}")
        else:
            log("  列表暂未查到（可能缓存）")
        append_record(cfg, week, it["title"], it["content_text"],
                      {"code": res.get("code"), "msg": res.get("msg"), "review_status": review})
        ok.append(it["week"])
        time.sleep(1.2)

    log("")
    log("=" * 66)
    log(f"成功 {len(ok)} 篇：{ok}")
    if failed:
        log(f"失败 {len(failed)} 篇：{failed}")
    log("=" * 66)
    return 0 if not failed else 7


def main():
    ap = argparse.ArgumentParser(description="校友邦实习周志自动填写")
    ap.add_argument("--week", type=int, help="指定第几周（默认当前周）")
    ap.add_argument("--all", action="store_true", help="补齐所有未提交的周")
    ap.add_argument("--notes", default="", help="本周真实素材，供 AI 参考")
    ap.add_argument("--dry-run", action="store_true", help="只生成预览，不提交")
    ap.add_argument("--yes", action="store_true", help="跳过人工确认")
    ap.add_argument("--test-submit", action="store_true", help="用草稿态验证提交链路后删除")
    ap.add_argument("--fake-gen", action="store_true", help=argparse.SUPPRESS)  # 自检用：跳过 AI
    ap.add_argument("--gen-only", action="store_true", help="只生成并保存待提交内容（不提交）")
    ap.add_argument("--submit-pending", action="store_true", help="提交上次 --gen-only 生成的内容")
    ap.add_argument("--gen-batch", action="store_true", help="批量生成所有未提交周次，输出预览文件（不提交）")
    ap.add_argument("--submit-batch", action="store_true", help="提交 --gen-batch 生成的整批内容")
    ap.add_argument("--weeks", default="", help="限定周次，如 1-9 或 1,3,5")
    ap.add_argument("--regen-week", type=int, help="重新生成批次里的某一周（修正用）")
    ap.add_argument("--qc", action="store_true", help="质检待提交批次：字数/英文残留/重复度")
    ap.add_argument("--login-only", action="store_true", help="只检查/完成登录，然后退出")
    args = ap.parse_args()

    cfg = ai_writer.load_config()

    with XybClient(headless=True) as client:
        if not auth.ensure_login(client):
            log("登录失败，退出。")
            return 2

        if args.login_only:
            info = auth.loginer_info(client)
            log("")
            log(f"登录成功：{info.get('loginer') or '?'} | {info.get('schoolName') or '?'}")
            log("登录态已保存在本机，之后可直接使用其它功能。")
            return 0

        if not args.fake_gen:
            cfg = ensure_ai_key(cfg)

        ctx = resolve_context(client)
        cfg = build_cfg(cfg, ctx)

        intern = cfg.get("internship") or {}
        if not (intern.get("company") or "").strip() or not (intern.get("position") or "").strip():
            log("")
            log("提示：没能从校友邦读到「实习单位 / 岗位」，生成的正文会比较泛。")
            log("      可打开 start.bat 菜单 [6] 手动填写单位、岗位和主要工作内容。")

        if args.gen_batch or args.submit_batch or args.qc or args.regen_week:
            return run_batch(args, cfg, ctx, client)

        weeks = ctx["weeks"]

        # 选择目标周
        if args.week:
            targets = [w for w in weeks if int(w.get("week") or 0) == args.week]
            if not targets:
                log(f"找不到第 {args.week} 周。")
                return 3
        elif args.all:
            targets = [w for w in weeks if is_unsubmitted(w)]
        else:
            today = ctx["today"]
            cur = [w for w in weeks
                   if parse_date(w.get("startDate")) and parse_date(w.get("endDate"))
                   and parse_date(w.get("startDate")) <= today <= parse_date(w.get("endDate"))]
            if cur:
                targets = [cur[0]]
            else:
                done = [w for w in weeks if parse_date(w.get("endDate")) and parse_date(w.get("endDate")) <= today]
                targets = [done[-1]] if done else [weeks[0]]

        if not targets:
            log("没有需要写的周次（可能都已提交）。")
            return 0

        for week in targets:
            log("")
            log("=" * 66)
            log(f"目标：{week_label(week)}" + ("（系统显示已提交）" if not is_unsubmitted(week) else ""))
            if not is_unsubmitted(week) and not (args.test_submit or args.dry_run or args.gen_only):
                log("这篇已提交过，跳过。")
                continue

            if args.submit_pending:
                pending = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
                if str(pending.get("traineeId")) != str(ctx["trainee_id"]) or \
                        str(pending.get("week")) != str(week.get("week")):
                    log("待提交内容与当前目标周不一致，请重新 --gen-only 生成。")
                    continue
                gen = {"title": pending["title"], "content_html": pending["content_html"],
                       "content_text": pending["content_text"]}
                log("使用上次生成的内容（--submit-pending）")
            else:
                log("生成正文中（调用 DeepSeek）…")
                try:
                    if args.fake_gen:
                        idx = week.get("week")
                        body = (f"本周是实习第{idx}周，主要工作是围绕家具制造业务的数据处理做算法实现。"
                                f"周一到周三我在用 C++ 复现并优化一个数据清洗流程，把原来需要人工核对的部分改成批量处理，"
                                f"过程中发现边界条件考虑不全，和导师讨论后补了两处判断，跑通后效率明显提升。"
                                f"周四到周五用 Python 写了配套的统计脚本，把每周的处理结果汇总成表格，方便业务同事查看。"
                                f"本周的困难主要是对业务口径不熟悉，有些字段含义要反复确认；下周计划继续完善脚本的异常处理，"
                                f"并把这套流程整理成文档，方便后续交接。")
                        gen = {
                            "title": f"生产实习第{idx}周",
                            "content_html": "".join(f"<p>{p}。</p>" for p in body.split("。") if p.strip()),
                            "content_text": body,
                        }
                    else:
                        gen = generate_one(cfg, ctx, week, args.notes)
                except ai_writer.AIWriterError as e:
                    log("生成失败：", str(e))
                    return 4

            title = gen["title"]
            html = gen["content_html"]
            text = gen["content_text"]
            log("")
            log(f"标题：{title}")
            log(f"字数：约 {len([c for c in text if c.strip()])} 字（要求 ≥ {cfg['style'].get('min_words')}）")
            log("-" * 66)
            log(text)
            log("-" * 66)

            if args.gen_only:
                PENDING_PATH.write_text(json.dumps({
                    "week": week.get("week"),
                    "startDate": to_dot(week.get("startDate")),
                    "endDate": to_dot(week.get("endDate")),
                    "traineeId": ctx["trainee_id"],
                    "title": title,
                    "content_html": html,
                    "content_text": text,
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                log(f"已保存待提交内容：{PENDING_PATH}")
                log("确认无误后执行： python xyb\\run.py --submit-pending --yes")
                continue

            if args.dry_run:
                log("（--dry-run，不提交）")
                continue

            if not args.yes and not ask_yes("确认提交这一篇到校友邦？"):
                log("已跳过。")
                continue

            status = "0" if args.test_submit else "1"
            try:
                res = submit_journal(client, ctx, week, title, html, status=status)
            except XybError as e:
                log("提交失败：", str(e))
                continue

            log(f"提交返回：code={res.get('code')} msg={res.get('msg')}")
            item = verify_submitted(client, ctx, title)
            review = (item or {}).get("reviewStatus")
            if item:
                log(f"已在校友邦列表中确认：{item.get('title')} | 状态：{review or '已提交'}")
            else:
                log("列表暂未查到（可能有缓存，稍后刷新列表即可）。")

            if args.test_submit:
                draft = verify_draft(client, ctx, title)
                if draft:
                    log(f"草稿箱已确认：id={draft.get('id')} {draft.get('title')}")
                    d = client.api("/practice/student/blogs/deleteBlogs.action", {"id": draft.get("id")})
                    log(f"自检草稿已删除：code={d.get('code')} {d.get('msg')}")
                else:
                    log("!! 草稿箱未找到刚提交的草稿，请手动检查")
                continue

            path = append_record(cfg, week, title, text,
                                 {"code": res.get("code"), "msg": res.get("msg"), "review_status": review})
            log(f"已归档到：{path}")

    log("")
    log("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

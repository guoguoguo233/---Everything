# -*- coding: utf-8 -*-
"""
从校友邦回读已提交的周志正文，重建本地归档 `周志记录/周志记录.md`。

用途：归档丢了/想和系统对齐时，跑一次就能拿到系统里的权威内容和提交时间。
用法： python xyb\sync_archive.py
"""
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser import XybClient  # noqa: E402
import auth  # noqa: E402
import run  # noqa: E402
import paths  # noqa: E402

ROOT = paths.app_dir()
OUT_MD = ROOT / "周志记录" / "周志记录.md"


def html_to_text(html: str) -> str:
    if not html:
        return ""
    s = re.sub(r"(?i)<br\s*/?>", "\n", html)
    s = re.sub(r"(?i)</p\s*>", "\n\n", s)
    s = re.sub(r"(?i)<p[^>]*>", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def fmt_ms(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def main():
    with XybClient(headless=True) as c:
        if not auth.ensure_login(c):
            print("未登录，请先运行 run.py 扫码登录。")
            return 2
        c.goto("#/workbench")
        c.page.wait_for_timeout(4000)

        terms = c.api("/common/plugin/term/loadTerms.action", {"type": 27})
        term_id, term_name = run.pick_term(terms, date.today())
        if not term_id:
            print("找不到当前学期。")
            return 3
        print("当前学期:", term_name, "| schoolTermId =", term_id)

        blogs = c.api("/practice/student/blogs/loadBlogsList.action",
                      {"schoolTermId": term_id, "type": "w", "page": 1, "pageSize": 40})
        rows = ((blogs.get("data") or {}).get("list")) or []
        print("系统内周志篇数:", len(rows))

        records = []
        for it in rows:
            bid = it.get("id")
            detail = c.api("/practice/student/blogs/loadBlogsDetail.action", {"id": bid, "type": "w"})
            d = detail.get("data") or {}
            info = c.api("/practice/student/blogs/loadBlogsInfo.action", {"id": bid, "type": "w"})
            inf = info.get("data") or {}
            text = html_to_text(d.get("content") or inf.get("content") or "")
            title = d.get("title") or inf.get("title") or it.get("title") or ""
            m = re.search(r"第\s*(\d+)\s*周", title)
            week = int(m.group(1)) if m else 0
            start = it.get("startDate") or inf.get("startDate") or ""
            end = it.get("endDate") or inf.get("endDate") or ""
            when = inf.get("editTime") or fmt_ms(it.get("commitTime") or it.get("createTime"))
            records.append({
                "week": week, "title": title, "period": f"{start} ~ {end}",
                "words": len([ch for ch in text if ch.strip()]),
                "when": when, "status": it.get("reviewStatus") or "已提交", "text": text,
            })
            print(f"  第{week}周 {title} | {records[-1]['words']}字 | {records[-1]['status']}")

    records.sort(key=lambda r: r["week"])
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 实习周志记录",
        "",
        "> 本文件由本地工具从校友邦接口回读生成，内容与系统内一致，用于回溯。",
        "",
        f"- 已提交：**{len(records)} 篇**",
        "",
        "## 总览",
        "",
        "| 周次 | 周期 | 标题 | 字数 | 提交时间 | 状态 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in records:
        lines.append(f"| 第{r['week']}周 | {r['period']} | {r['title']} | {r['words']} | {r['when']} | {r['status']} |")
    lines += ["", "---", ""]
    for r in records:
        lines += [
            f"## 第{r['week']}周（{r['period']}）",
            "",
            f"- 标题：{r['title']}",
            f"- 字数：约 {r['words']} 字",
            f"- 提交时间：{r['when']}",
            f"- 系统状态：{r['status']}",
            "",
            r["text"],
            "",
            "---",
            "",
        ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("\n归档已更新:", OUT_MD)
    print("篇数:", len(records), "| 字符数:", len(OUT_MD.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""校友邦实习周志 —— 提示词模板与标题渲染。

本模块只负责“怎么说”，不负责网络请求和校验，方便单独调整文案。
被 ai_writer.py 调用：
    build_messages(cfg, week, notes="", feedback=None) -> list[dict]
    build_title(cfg, week) -> str
"""

from __future__ import annotations

import re
from typing import Any

# 校友邦周志标题限制：1~40 字（这里留 0 字余量，截断到 40）
MAX_TITLE_LEN = 40

# 缺省标题模板，style.title_pattern 为空时使用
DEFAULT_TITLE_PATTERN = "{position}实习周志（第{index}周）"

# 系统提示词：把“不许编造”放在最前面并且写死，任何配置都改不掉
SYSTEM_PROMPT = """你正在帮一名在校大学生撰写「校友邦」平台上的实习周志正文，内容会直接提交给学校老师审核。

写作时必须遵守以下规则（优先级从高到低）：
一、绝对不允许编造事实：
1. 不得虚构公司名、部门名、项目名、产品名、客户名、同事或领导的人名；
2. 不得编造任何具体数字指标，例如“效率提升 30%”“完成了 12 个接口”“节省了 2 小时”“覆盖 500 名用户”；
3. 不得编造具体日期、版本号、金额、合同、奖项、考核结果；
4. 不得编造不存在的技术细节或业务背景；不确定的细节一律用概括表述，例如“参与了部分接口的联调”“在同事指导下完成了相关修改”“看了几份文档”。
二、内容只能来自用户给出的「实习信息」「本周信息」和「本周要点」，可以在此基础上合理展开日常性的实习工作（熟悉代码、看文档、开会、调试、改问题、向同事请教、写测试等）。
三、第一人称，像学生自己整理的本周记录；语气平实、具体，不要写成总结报告、宣传稿或公文，不要空泛抒情和口号。
四、语言为中文；不使用 emoji；不使用 Markdown 标记（不要 #、*、-、`、表格、代码块、链接）；不要给段落加序号或小标题。
五、只输出正文，不要输出标题、不要输出任何解释或前后缀。
六、段落之间用一个空行分隔，段落数量严格按用户要求。"""

# 重试时追加给模型的“不合格说明”模板
RETRY_FEEDBACK_TEMPLATE = (
    "你上一次输出的正文只有约 {words} 个字，没有达到要求的 {min_words} 个字。"
    "请重新写一版：把内容写具体、写充实（多写清楚具体做了什么、遇到什么问题、怎么排查、怎么解决、有什么体会、下周打算怎么做），"
    "把字数写到 {target} 字左右，不得少于 {min_words} 字。"
    "注意仍然绝对不能编造任何具体数字、项目名、客户名和人名，不确定的地方用概括表述。"
)


def _as_dict(value: Any) -> dict:
    """安全地取 dict，类型不对时返回空 dict。"""
    return value if isinstance(value, dict) else {}


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _style(cfg: dict) -> dict:
    return _as_dict(cfg.get("style"))


def _internship(cfg: dict) -> dict:
    return _as_dict(cfg.get("internship"))


def word_limits(cfg: dict) -> tuple[int, int, int]:
    """返回 (min_words, max_words, target_words)，并做兜底修正。"""
    style = _style(cfg)
    min_w = _int(style.get("min_words"), 350)
    max_w = _int(style.get("max_words"), 500)
    min_w = max(100, min(min_w, 3000))
    max_w = max(150, min(max_w, 4000))
    if max_w <= min_w:
        max_w = min_w + 100
    target = (min_w + max_w) // 2
    return min_w, max_w, target


def _week_value(week: dict, *keys: str, default: str = "") -> str:
    """按多个可能的键名取周信息，取第一个非空的。"""
    if not isinstance(week, dict):
        return default
    for key in keys:
        value = week.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _format_lines(items: Any, prefix: str = "- ") -> str:
    """把字符串列表渲染成多行；不是列表时按整段文本处理。"""
    if isinstance(items, (list, tuple)):
        lines = [f"{prefix}{str(x).strip()}" for x in items if str(x).strip()]
        return "\n".join(lines) if lines else "（未提供）"
    text = _text(items)
    return text if text else "（未提供）"


def _join_items(items: Any, sep: str = "、") -> str:
    """把字符串列表用顿号连成一行（用于技术栈这类短列表）。"""
    if isinstance(items, (list, tuple)):
        cleaned = [str(x).strip() for x in items if str(x).strip()]
        return sep.join(cleaned) if cleaned else "（未提供）"
    text = _text(items)
    return text if text else "（未提供）"


def build_user_prompt(cfg: dict, week: dict, notes: str = "", feedback: str | None = None) -> str:
    """拼出单条 user 提示词。feedback 非空表示本轮是重试。"""
    intern = _internship(cfg)
    style = _style(cfg)
    min_w, max_w, target = word_limits(cfg)

    company = _text(intern.get("company"), "（未提供）")
    position = _text(intern.get("position"), "（未提供）")
    department = _text(intern.get("department"), "（未提供）")
    work_summary = _text(intern.get("work_summary"), "（未提供）")
    common_tasks = _format_lines(intern.get("common_tasks"))
    tech_stack = _join_items(intern.get("tech_stack"))

    index = _week_value(week, "index", default="1")
    start_date = _week_value(week, "startDate", "start_date")
    end_date = _week_value(week, "endDate", "end_date")
    deadline = _week_value(week, "deadline")
    date_range = f"{start_date} 至 {end_date}" if (start_date or end_date) else "（未提供）"

    tone = _text(style.get("tone"), "平实、具体、第一人称；像学生自己写的")
    structure = _text(
        style.get("format"),
        "分3段：本周工作内容 / 收获与思考 / 存在不足与下周打算",
    )

    notes_text = _text(notes)
    if len(notes_text) > 800:
        notes_text = notes_text[:800] + "……"
    if notes_text:
        notes_block = notes_text
        notes_hint = "请优先围绕这些要点展开，把它们写进本周的工作内容里。"
    else:
        notes_block = "（本周没有额外记录）"
        notes_hint = "用户本周没有留下具体要点，请根据实习信息和周次，合理展开一周日常的实习工作，不要编造具体项目和数据。"

    parts = [
        "请根据下面的信息，写一篇用于提交的实习周志正文。",
        "",
        "【实习信息】",
        f"公司：{company}",
        f"岗位：{position}",
        f"部门：{department}",
        f"日常职责：{work_summary}",
        "常见工作内容：",
        common_tasks,
        f"技术栈：{tech_stack}",
        "",
        "【本周信息】",
        f"周次：第 {index} 周",
        f"起止日期：{date_range}",
        f"提交截止时间：{deadline or '（未提供）'}",
        "",
        "【本周要点（用户随手记录）】",
        notes_block,
        notes_hint,
        "",
        "【写作要求】",
        f"1. 字数：{target} 字左右，不得少于 {min_w} 字，不要超过 {max_w} 字（按中文字符计）。",
        f"2. 语气风格：{tone}",
        f"3. 段落结构：{structure}",
        "4. 段落之间用一个空行分隔，不要给段落加序号或小标题。",
        "5. 只输出正文，不要输出标题、不要输出解释、不要使用 Markdown 和 emoji。",
        "6. 再次强调：不得编造任何具体数字、项目名、客户名、人名和不存在的业务细节。",
    ]

    if feedback:
        parts += ["", "【上一次生成不合格，请重写】", feedback.strip()]

    return "\n".join(parts)


def build_messages(
    cfg: dict,
    week: dict,
    notes: str = "",
    feedback: str | None = None,
) -> list[dict]:
    """构造 OpenAI 兼容的 messages 列表。"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(cfg, week, notes, feedback)},
    ]


def retry_feedback(cfg: dict, words: int) -> str:
    """字数不足时给模型看的中文原因说明。"""
    min_w, _max_w, target = word_limits(cfg)
    return RETRY_FEEDBACK_TEMPLATE.format(
        words=int(words), min_words=min_w, target=target
    )


class _SafeDict(dict):
    """format_map 用：模板里出现未知占位符时渲染成空串而不是抛 KeyError。"""

    def __missing__(self, key: str) -> str:  # noqa: D105
        return ""


def _clean_title(raw: str) -> str:
    """去掉标题里的换行、多余空白、HTML 标签和 emoji，避免平台校验失败。"""
    text = re.sub(r"<[^>]+>", "", str(raw or ""))
    text = re.sub(r"[\r\n\t\u3000]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    # 去掉 emoji 与变体选择符
    text = re.sub(
        "[\U0001f000-\U0001faff\u2600-\u27bf\ufe0e\ufe0f\u200d\u20e3]",
        "",
        text,
    )
    return text.strip()


def build_title(cfg: dict, week: dict) -> str:
    """按 style.title_pattern 渲染标题，保证非空且不超过 40 字。"""
    style = _style(cfg)
    intern = _internship(cfg)
    pattern = _text(style.get("title_pattern")) or DEFAULT_TITLE_PATTERN

    index = _week_value(week, "index", default="1")
    start_date = _week_value(week, "startDate", "start_date")
    end_date = _week_value(week, "endDate", "end_date")
    data = _SafeDict(
        {
            "position": _text(intern.get("position")),
            "company": _text(intern.get("company")),
            "department": _text(intern.get("department")),
            "index": index,
            "startDate": start_date,
            "endDate": end_date,
            "start_date": start_date,
            "end_date": end_date,
            "year": start_date[:4] if len(start_date) >= 4 else "",
            "month": start_date[5:7] if len(start_date) >= 7 else "",
        }
    )

    try:
        title = pattern.format_map(data)
    except (ValueError, IndexError):
        # 模板写坏了（比如多余的大括号），退回默认模板
        title = DEFAULT_TITLE_PATTERN.format_map(data)

    title = _clean_title(title)

    # 标题兜底：模板渲染成空、或岗位等信息全缺时，至少给出一个可提交的标题
    if not title:
        fallback_index = index if index.isdigit() else "1"
        title = f"实习周志（第{fallback_index}周）"
        if _text(intern.get("position")):
            title = f"{_text(intern.get('position'))}实习周志（第{fallback_index}周）"

    # 超长截断（校友邦限制 1~40 字）
    if len(title) > MAX_TITLE_LEN:
        title = title[: MAX_TITLE_LEN - 1] + "…"

    return title

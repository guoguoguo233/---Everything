# -*- coding: utf-8 -*-
"""校友邦实习周志 —— AI 正文生成模块。

对外接口（与上层调用方约定，请勿随意改名）：
    load_config(path=None) -> dict
        读取本文件同目录下的 config.json；不存在时抛 FileNotFoundError 并提示复制 example。
    generate(cfg, week, notes="") -> dict
        调用 OpenAI 兼容接口生成周志，返回 {"title", "content_html", "content_text"}。
    check_api(cfg) -> (ok: bool, message: str)
        连通性自检，任何异常都转换成中文说明，不会抛出堆栈。
    strip_html(html) -> str
        富文本转纯文本，供预览使用。

设计要点：
- 只依赖标准库 + requests（没有 requests 时自动退回 urllib）；
- 生成的 HTML 只允许 <p> / <br>，模型给的 Markdown、emoji、脏标签都会被清洗掉；
- 提示词里硬性禁止编造项目名、人名、数字指标；
- 字数不足时自动重试一次，并把“字数不够”的原因告诉模型；
- 日志与报错里的 API Key 一律打码。
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:  # requests 是可选项，缺失时退回 urllib
    import requests  # type: ignore
except Exception:  # pragma: no cover - 环境没有 requests 时走 urllib
    requests = None

try:  # 可选：仅用于从本地文件路径推断，实际以 __file__ 为准
    from . import prompts  # type: ignore  # 作为包导入时（xyb.ai_writer）
    from . import paths  # type: ignore
except ImportError:  # 作为脚本直接运行时（python ai_writer.py 或 sys.path 里有 xyb）
    import prompts  # type: ignore
    import paths  # type: ignore


# ---------------------------------------------------------------------------
# 基础环境：终端编码、日志、常量
# ---------------------------------------------------------------------------

def _force_utf8_stdout() -> None:
    """PowerShell 下中文输出容易乱码，尽量把标准输出切成 UTF-8。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # 被重定向到不支持编码的流时忽略
                pass


_force_utf8_stdout()

logger = logging.getLogger("xyb.ai_writer")
logger.addHandler(logging.NullHandler())

MODULE_DIR = Path(__file__).resolve().parent
CONFIG_FILENAME = "config.json"
EXAMPLE_FILENAME = "config.example.json"

# 校友邦标题限制 1~40 字
MAX_TITLE_LEN = prompts.MAX_TITLE_LEN

# 只保留这两种富文本标签
ALLOWED_TAGS = ("p", "br")

DEFAULT_MIN_WORDS = 350
DEFAULT_MAX_WORDS = 500


class AIWriterError(RuntimeError):
    """本模块统一的业务异常，message 一定是给用户看的中文说明。"""


# ---------------------------------------------------------------------------
# 配置读取与校验
# ---------------------------------------------------------------------------

def default_config_path() -> Path:
    """默认配置文件路径。
    源码运行 = 仓库根/xyb/config.json；EXE 运行 = EXE 同目录/config.json。"""
    try:
        return paths.config_path()
    except Exception:
        return MODULE_DIR / CONFIG_FILENAME


def _example_config_path(config_path: Path) -> Path:
    """配置模板路径（EXE 里模板被打包进只读资源目录）。"""
    try:
        p = paths.example_config_path()
        if p.exists():
            return p
    except Exception:
        pass
    return config_path.parent / EXAMPLE_FILENAME


def load_config(path: str | os.PathLike | None = None) -> dict:
    """读取配置文件。

    - 默认读取 config.json（源码版在 xyb/ 下，EXE 版在 exe 同目录）；
    - 文件不存在 -> FileNotFoundError，消息里提示复制 config.example.json；
    - JSON 写错 -> AIWriterError，消息里给出行列号。
    """
    config_path = Path(path) if path else default_config_path()
    if not config_path.exists():
        example = _example_config_path(config_path)
        if example.exists():
            hint = f"请先把 {example} 复制为 {config_path}，再填写 ai.api_key 后重试。"
        else:
            hint = f"请新建 {config_path}（内容可参考同目录的 {EXAMPLE_FILENAME}），填写 ai.api_key 后重试。"
        raise FileNotFoundError(f"未找到配置文件：{config_path}。{hint}")

    try:
        raw = config_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AIWriterError(f"读取配置文件失败：{config_path}（{exc}）") from None

    if not raw.strip():
        raise AIWriterError(
            f"配置文件是空的：{config_path}。请复制 {EXAMPLE_FILENAME} 的内容并填写 ai.api_key。"
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIWriterError(
            f"配置文件不是合法的 JSON：{config_path}（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）。"
            f"请对照 {EXAMPLE_FILENAME} 检查逗号、引号和括号。"
        ) from None

    if not isinstance(data, dict):
        raise AIWriterError(
            f"配置文件格式不正确：{config_path} 的最外层必须是一个 JSON 对象（花括号）。"
        )
    return data


# 判定“还是示例里的占位 Key”，避免把占位符当真实 Key 发出去
_PLACEHOLDER_PATTERNS = (
    "在这里",
    "填你的",
    "填入你的",
    "请填写",
    "your_api_key",
    "your-api-key",
    "your_key",
    "yourkey",
    "placeholder",
    "changeme",
    "change_me",
    "replace_me",
    "sk-xxx",
    "sk-你的",
    "<your",
    "示例key",
    "示例-key",
)


def is_placeholder_key(key: str) -> bool:
    """判断 ai.api_key 是否为空或明显还是占位符。"""
    text = str(key or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_PATTERNS)


def mask_key(key: str) -> str:
    """日志/提示里给 Key 打码，绝不输出完整 Key。"""
    text = str(key or "").strip()
    if not text:
        return "（未填写）"
    if len(text) <= 6:
        return "******"
    if len(text) <= 12:
        return text[:2] + "****" + text[-2:]
    return text[:3] + "****" + text[-4:]


_KEY_IN_TEXT_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def _mask_text(text: Any, limit: int = 300) -> str:
    """把任意文本里的疑似 Key 打码并截断，用于拼错误信息。"""
    value = str(text or "").replace("\r", " ").replace("\n", " ")
    value = _KEY_IN_TEXT_RE.sub(lambda m: mask_key(m.group(0)), value)
    value = re.sub(r"\s{2,}", " ", value).strip()
    if len(value) > limit:
        value = value[:limit] + "…"
    return value


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _word_limits(cfg: dict) -> tuple[int, int, int]:
    """返回 (min_words, max_words, target_words)，非法配置时兜底。"""
    style = cfg.get("style") if isinstance(cfg, dict) else None
    style = style if isinstance(style, dict) else {}
    min_w = _to_int(style.get("min_words"), DEFAULT_MIN_WORDS)
    max_w = _to_int(style.get("max_words"), DEFAULT_MAX_WORDS)
    min_w = max(100, min(min_w, 3000))
    max_w = max(150, min(max_w, 4000))
    if max_w <= min_w:
        max_w = min_w + 100
    target = (min_w + max_w) // 2
    return min_w, max_w, target


def _get_ai_cfg(cfg: dict) -> dict:
    """取出并校验 ai 段配置，返回规范化后的 dict。"""
    if not isinstance(cfg, dict):
        raise AIWriterError("配置格式不正确：顶层必须是一个 JSON 对象。")

    ai = cfg.get("ai")
    if not isinstance(ai, dict):
        raise AIWriterError(
            "配置缺少 ai 段。请对照 config.example.json 补全 ai.base_url、ai.model、ai.api_key。"
        )

    base_url = str(ai.get("base_url") or "").strip().rstrip("/")
    model = str(ai.get("model") or "").strip()
    api_key = str(ai.get("api_key") or "").strip()

    if not base_url:
        raise AIWriterError("配置缺少 ai.base_url（例如 https://api.deepseek.com/v1）。")
    if not base_url.lower().startswith(("http://", "https://")):
        raise AIWriterError(f"ai.base_url 必须以 http:// 或 https:// 开头，当前是：{_mask_text(base_url)}")
    if not model:
        raise AIWriterError("配置缺少 ai.model（例如 deepseek-chat）。")
    if is_placeholder_key(api_key):
        raise AIWriterError(
            "还没有填写 AI Key：请打开 xyb/config.json，把 ai.api_key 改成你自己的 DeepSeek Key"
            "（形如 sk-xxxxxxxx），保存后再试。"
        )

    try:
        temperature = float(ai.get("temperature", 0.7))
    except (TypeError, ValueError):
        temperature = 0.7
    temperature = min(2.0, max(0.0, temperature))

    timeout = _to_int(ai.get("timeout"), 60)
    timeout = min(600, max(5, timeout))

    return {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
        "timeout": timeout,
    }


def _endpoint(base_url: str) -> str:
    """拼出 chat/completions 的完整地址。"""
    base = str(base_url).rstrip("/")
    if base.lower().endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


# ---------------------------------------------------------------------------
# HTTP：默认 requests，缺失时退回 urllib
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> tuple[int, str, float]:
    """POST 一个 JSON，返回 (HTTP 状态码, 响应文本, 耗时秒)。网络错误转成 AIWriterError。"""
    started = time.monotonic()

    if requests is not None:
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=(min(10, timeout), timeout),
            )
        except requests.exceptions.Timeout:
            raise AIWriterError(
                f"请求超时：{timeout} 秒内没有收到响应。请检查网络/代理是否正常，"
                "或把 config.json 里的 ai.timeout 调大。"
            ) from None
        except requests.exceptions.SSLError:
            raise AIWriterError(
                "HTTPS 证书校验失败：请检查网络代理设置或系统时间是否正确。"
            ) from None
        except requests.exceptions.ConnectionError as exc:
            raise AIWriterError(
                f"无法连接到 AI 接口 {url}：可能是断网、代理未开启或 ai.base_url 填错。"
                f"（{_mask_text(exc, 200)}）"
            ) from None
        except requests.exceptions.RequestException as exc:
            raise AIWriterError(f"请求 AI 接口失败：{_mask_text(exc, 200)}") from None
        return response.status_code, response.text, time.monotonic() - started

    # ---- urllib 兜底实现 ----
    import urllib.error
    import urllib.request

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
            return response.status, text, time.monotonic() - started
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也当正常响应返回，由上层给友好提示
        text = exc.read().decode("utf-8", "replace")
        return exc.code, text, time.monotonic() - started
    except TimeoutError:
        raise AIWriterError(
            f"请求超时：{timeout} 秒内没有收到响应。请检查网络/代理，或把 ai.timeout 调大。"
        ) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise AIWriterError(
            f"无法连接到 AI 接口 {url}：可能是断网、代理未开启或 ai.base_url 填错。"
            f"（{_mask_text(reason, 200)}）"
        ) from None


def _extract_error_detail(body: str) -> str:
    """从错误响应里尽量挖出服务端的 message，方便用户排查。"""
    detail = ""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("type") or "")
        elif isinstance(error, str):
            detail = error
        elif data.get("message"):
            detail = str(data.get("message"))
    if not detail:
        detail = body
    return _mask_text(detail, 200)


def _friendly_http_error(status: int, body: str) -> str:
    """把 HTTP 错误码翻译成可操作的中文提示。"""
    detail = _extract_error_detail(body)
    if status in (401, 403):
        return (
            f"AI 接口鉴权失败（HTTP {status}）：API Key 无效、已过期或没有该模型的权限。"
            f"请检查 config.json 里的 ai.api_key 是否复制完整（当前 Key：{{key}}）。"
            f"服务端信息：{detail}"
        )
    if status == 402:
        return f"AI 账户余额不足或未开通付费（HTTP 402）。请到服务商后台充值后重试。服务端信息：{detail}"
    if status == 404:
        return (
            f"接口地址或模型不存在（HTTP 404）：请检查 ai.base_url 是否形如 https://api.deepseek.com/v1，"
            f"以及 ai.model 是否拼写正确。服务端信息：{detail}"
        )
    if status == 429:
        return f"请求太频繁被限流（HTTP 429）：请等几十秒后重试，或降低生成频率。服务端信息：{detail}"
    if 500 <= status < 600:
        return f"AI 服务端出错（HTTP {status}）：通常是服务商临时故障，请稍后重试。服务端信息：{detail}"
    return f"AI 接口返回异常状态码 HTTP {status}。服务端信息：{detail}"


def _extract_message_content(data: Any) -> str:
    """从 chat/completions 响应里取出正文文本。"""
    if not isinstance(data, dict):
        raise AIWriterError("AI 接口返回的数据格式不认识（不是 JSON 对象），请检查 ai.base_url 是否指向 OpenAI 兼容接口。")

    if data.get("error"):
        raise AIWriterError(f"AI 接口返回错误：{_extract_error_detail(json.dumps(data, ensure_ascii=False))}")

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AIWriterError(
            "AI 接口返回里没有 choices，没有得到正文。请检查 ai.base_url 是否为 OpenAI 兼容地址"
            "（一般以 /v1 结尾），以及 ai.model 是否正确。"
        )

    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")

    if isinstance(content, list):  # 少数兼容实现返回分段数组
        content = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )

    text = str(content or "").strip()
    if not text:
        raise AIWriterError(
            "AI 模型返回了空正文。可以重试一次；若持续为空，请检查 ai.model 是否为对话模型"
            "（例如 deepseek-chat）。"
        )
    return text


# 兼容旧名（内部使用）
def _friendly_error_with_key(message_template: str, key: str) -> str:
    return message_template.replace("{key}", mask_key(key))


def _chat_completion(
    ai: dict,
    messages: list[dict],
    max_tokens: int,
    temperature: float | None = None,
) -> tuple[str, dict]:
    """调用 /chat/completions，返回 (正文, 元信息)。"""
    url = _endpoint(ai["base_url"])
    payload = {
        "model": ai["model"],
        "messages": messages,
        "temperature": ai["temperature"] if temperature is None else temperature,
        "max_tokens": int(max_tokens),
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {ai['api_key']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    logger.debug("请求 %s（model=%s, key=%s）", url, ai["model"], mask_key(ai["api_key"]))
    status, body, elapsed = _post_json(url, payload, headers, ai["timeout"])
    logger.debug("响应 HTTP %s，耗时 %.0f ms", status, elapsed * 1000)

    if not (200 <= status < 300):
        message = _friendly_http_error(status, body)
        message = _friendly_error_with_key(message, ai["api_key"])
        raise AIWriterError(message)

    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise AIWriterError(
            f"AI 接口返回的不是合法 JSON（HTTP {status}）。请确认 ai.base_url 指向 OpenAI 兼容接口。"
        ) from None

    content = _extract_message_content(data)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return content, {"elapsed_ms": elapsed * 1000, "usage": usage, "status": status}


# ---------------------------------------------------------------------------
# 文本 / 富文本处理
# ---------------------------------------------------------------------------

# Markdown 与脏数据的清理规则
_MD_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+\-]*")
_MD_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*")
_MD_BULLET_RE = re.compile(r"(?m)^[ \t]{0,3}(?:[-*+•·]|\d{1,2}[.、)])[ \t]+")
_MD_QUOTE_RE = re.compile(r"(?m)^[ \t]{0,3}>[ \t]?")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_ITALIC_RE = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])")
_MD_CODE_RE = re.compile(r"`([^`\n]*)`")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\u2600-\u27bf\ufe0e\ufe0f\u200d\u20e3\u2b00-\u2bff]"
)
_HTML_TAG_STRIP_RE = re.compile(r"<[^>]*>")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[._+#/-][A-Za-z0-9]+)*")


def count_words(text: str) -> int:
    """按“中文字符数 + 英文/数字词数”估算字数（与平台口径接近）。

    标点、空格不计入，emoji 已在清洗阶段去掉。
    """
    if not text:
        return 0
    return len(_CJK_RE.findall(text)) + len(_ASCII_TOKEN_RE.findall(text))


def _clean_model_text(raw: str) -> str:
    """把模型输出清洗成纯文本：去 HTML、去 Markdown、去 emoji。"""
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")

    # 代码围栏先去掉标记（内容保留）
    text = _MD_FENCE_RE.sub("", text)

    # 模型如果自作主张输出了 HTML，先按段落/换行降级为纯文本
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)<p[^>]*>", "", text)
    text = _HTML_TAG_STRIP_RE.sub("", text)
    text = html_lib.unescape(text)

    # Markdown 结构标记
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BULLET_RE.sub("", text)
    text = _MD_QUOTE_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_CODE_RE.sub(r"\1", text)

    # 残留的强调符号与不可见字符
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")

    # emoji 与非中文符号
    text = _EMOJI_RE.sub("", text)

    # 行尾空格、首尾空白
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def _join_lines(lines: list[str]) -> str:
    """把段内的多行合成一段：中文直接拼接，英文/数字之间补空格。"""
    result = ""
    for line in lines:
        if not result:
            result = line
            continue
        prev_char = result[-1]
        next_char = line[0]
        if prev_char.isascii() and prev_char.isalnum() and next_char.isascii() and next_char.isalnum():
            result += " " + line
        else:
            result += line
    return result


def _split_paragraphs(text: str) -> list[str]:
    """按空行切段；如果模型只用了单换行，也按行切成段。"""
    text = re.sub(r"\n{3,}", "\n\n", text)
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if not blocks:
        return []

    if len(blocks) == 1:
        lines = [line.strip() for line in blocks[0].split("\n") if line.strip()]
        if len(lines) > 1:
            blocks = lines

    paragraphs: list[str] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        paragraph = _join_lines(lines)
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


def html_from_text(raw: str) -> tuple[str, str]:
    """把模型输出转成 (只含 <p> 的 HTML, 纯文本)。"""
    cleaned = _clean_model_text(raw)
    paragraphs = _split_paragraphs(cleaned)
    if not paragraphs:
        raise AIWriterError("AI 返回的正文清洗后为空，请重试一次。")
    content_html = "".join(
        f"<p>{html_lib.escape(paragraph, quote=False)}</p>" for paragraph in paragraphs
    )
    content_text = "\n\n".join(paragraphs)
    return content_html, content_text


class _HTMLExtractor(HTMLParser):
    """把 HTML 转成纯文本：块级标签换成换行，忽略 script/style。"""

    _BLOCK_TAGS = {
        "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
        "tr", "td", "th", "table", "section", "article", "blockquote", "pre", "hr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def strip_html(html: str) -> str:
    """富文本转纯文本，供预览使用。"""
    if not html:
        return ""
    parser = _HTMLExtractor()
    try:
        parser.feed(str(html))
        parser.close()
    except Exception:  # 极端脏数据时退化为正则去标签
        return _mask_text(_HTML_TAG_STRIP_RE.sub(" ", str(html)), limit=100000).strip()

    text = "".join(parser.parts)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sanitize_html(html: str) -> str:
    """只保留 <p> / <br>，剥掉其它标签和属性，防止富文本编辑器带入脏数据。"""
    if not html:
        return ""
    text = str(html)
    # <br> 保留；</p><p> 之外的标签直接去掉
    text = re.sub(r"(?i)<br\s*/?>", "<br>", text)
    text = re.sub(r"(?i)</?p\b[^>]*>", lambda m: "<p>" if not m.group(0).startswith("</") else "</p>", text)
    text = _HTML_TAG_STRIP_RE.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 对外主接口
# ---------------------------------------------------------------------------

def generate(cfg: dict, week: dict, notes: str = "") -> dict:
    """生成一篇实习周志。

    参数：
        cfg   : load_config() 读到的完整配置
        week  : {"startDate": "2026.10.19", "endDate": "2026.10.23",
                 "index": 6, "deadline": "2026.10.25 23:59:59"}
        notes : 用户当天的中文要点，可为空字符串

    返回：
        {"title": str, "content_html": str, "content_text": str}
        content_html 只含 <p>（必要时 <br>）标签。
    """
    ai = _get_ai_cfg(cfg)
    if week is None:
        week = {}
    if not isinstance(week, dict):
        raise AIWriterError("week 参数必须是字典，例如 {'index': 1, 'startDate': '...', 'endDate': '...'}")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        notes = str(notes)

    min_words, max_words, _target = _word_limits(cfg)
    title = prompts.build_title(cfg, week)
    max_tokens = min(4096, max(1024, int(max_words * 2.2) + 200))

    feedback: str | None = None
    content_html = ""
    content_text = ""
    words = 0

    # 最多两次：首次 + 字数不足时重试一次
    for attempt in (1, 2):
        messages = prompts.build_messages(cfg, week, notes, feedback=feedback)
        logger.info("正在生成周志（第 %d 次请求，模型 %s）…", attempt, ai["model"])
        raw, _meta = _chat_completion(ai, messages, max_tokens)
        content_html, content_text = html_from_text(raw)
        words = count_words(content_text)
        logger.info("第 %d 次生成完成：约 %d 字（要求不少于 %d 字）", attempt, words, min_words)

        if words >= min_words * 0.9:
            break
        if attempt == 1:
            feedback = prompts.retry_feedback(cfg, words)
            logger.warning(
                "正文只有约 %d 字，低于要求下限 %d 字，正在把原因告诉模型并重试一次。",
                words,
                min_words,
            )

    if words < min_words * 0.9:
        logger.warning(
            "重试后正文仍只有约 %d 字（要求不少于 %d 字），先接受本次结果，提交前可手动补充。",
            words,
            min_words,
        )

    return {
        "title": title,
        "content_html": content_html,
        "content_text": content_text,
    }


def check_api(cfg: dict) -> tuple[bool, str]:
    """AI 接口连通性自检。任何异常都转成 (False, 中文说明)，不会抛堆栈。"""
    if not isinstance(cfg, dict):
        return False, "配置格式不正确：顶层必须是一个 JSON 对象。"

    ai_raw = cfg.get("ai")
    if not isinstance(ai_raw, dict):
        return False, "配置缺少 ai 段。请对照 config.example.json 补全 ai.base_url、ai.model、ai.api_key。"

    api_key = str(ai_raw.get("api_key") or "").strip()
    if is_placeholder_key(api_key):
        return False, (
            "还没有填写 AI Key：请打开 xyb/config.json，把 ai.api_key 改成你自己的 DeepSeek Key"
            "（形如 sk-xxxxxxxx），保存后再运行 --check。"
        )

    try:
        ai = _get_ai_cfg(cfg)
    except AIWriterError as exc:
        return False, str(exc)

    messages = [
        {"role": "system", "content": "你是连通性测试助手，请只回复两个字：正常。"},
        {"role": "user", "content": "请只回复：正常"},
    ]

    try:
        content, meta = _chat_completion(ai, messages, max_tokens=16, temperature=0.0)
    except AIWriterError as exc:
        return False, str(exc)
    except Exception as exc:  # 自检绝不让调用方看到堆栈
        return False, f"自检时出现未预期的错误：{_mask_text(exc, 200)}"

    reply = _mask_text(content, 30)
    elapsed_ms = int(meta.get("elapsed_ms") or 0)
    return True, (
        f"连通正常：模型 {ai['model']} 已返回响应（耗时 {elapsed_ms} ms，回复：{reply}；"
        f"Key：{mask_key(ai['api_key'])}）。可以开始生成周志了。"
    )


# ---------------------------------------------------------------------------
# 命令行自检
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_writer.py",
        description="校友邦实习周志 AI 正文生成模块（自带连通性自检）。",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="读取 config.json，检查 AI 接口是否可连通",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="指定配置文件路径（默认：与本文件同目录的 config.json）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出调试日志（Key 已打码）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：python ai_writer.py --check"""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    if not args.check:
        parser.print_help()
        return 0

    try:
        cfg = load_config(args.config)
    except FileNotFoundError as exc:
        print(f"[未配置] {exc}")
        print("提示：把 xyb/config.example.json 复制为 xyb/config.json，填入 ai.api_key 后重新运行 --check。")
        return 2
    except AIWriterError as exc:
        print(f"[配置错误] {exc}")
        return 2

    # 占位 Key 视为“还没配置好”，单独给出退出码 2，和执行失败（1）区分开
    ai_section = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    if is_placeholder_key(ai_section.get("api_key")):
        print(
            "[未配置] 还没有填写 AI Key：请打开 xyb/config.json，"
            "把 ai.api_key 改成你自己的 DeepSeek Key（形如 sk-xxxxxxxx），保存后再运行 --check。"
        )
        return 2

    ok, message = check_api(cfg)
    if ok:
        print(f"[连通正常] {message}")
        return 0
    print(f"[自检失败] {message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

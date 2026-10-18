"""LLM 输出 → JSON 的统一稳健解析。

背景：原先每个 agent 各抄了一份 `_extract_json`，只有"剥 ``` 围栏 → 取首尾括号 →
json.loads"三步，模型偶发畸形输出（尾逗号、字符串内裸换行、前后夹解释文字）时一次
解析失败就把整条任务打成 failed——知识提炼实测 280 章里炸 1 章，整册判失败并丢掉
已完成但尚未取回的结果。

这里集中一份「带修复」的解析，并给出 `chat_json`：解析失败时追加一次"只输出合法
JSON"的重试（比任何文本修复都可靠）。各 agent 的 `_extract_json` 保留为薄封装，
调用点无需改动。
"""
import json
import re

from app.services.llm import llm

_FENCE_OPEN = re.compile(r"^\s*```(?:json|JSON)?\s*")
_FENCE_CLOSE = re.compile(r"\s*```\s*$")
# 非结构性的控制字符（\t\r\n 是合法 JSON 空白，不在此列）
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# 解析失败后的格式纠正提示（重试时追加为新一轮 user 消息）
_RETRY_HINT = (
    "你上一次的输出不是合法 JSON，无法解析。请重新输出："
    "只输出一个 JSON 本身，不要 ``` 围栏、不要任何解释文字；"
    "字符串内部不得出现真实换行，对象/数组末尾不得有多余逗号。"
)


def _strip_fence(text: str) -> str:
    return _FENCE_CLOSE.sub("", _FENCE_OPEN.sub("", text.strip())).strip()


def _slice(text: str, open_ch: str, close_ch: str) -> str | None:
    """截取首尾配对括号之间的内容（容忍模型在 JSON 前后加解释性文字）。"""
    s, e = text.find(open_ch), text.rfind(close_ch)
    return text[s : e + 1] if s != -1 and e > s else None


def _escape_raw_breaks(text: str) -> str:
    """把 JSON 字符串字面量里的真实换行/制表符转成转义序列。

    逐字符扫描并跟踪字符串状态，字符串外的结构性换行与缩进保持原样。
    """
    out: list[str] = []
    in_str = escaped = False
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            out.append(ch)
            escaped = True
        elif ch == '"':
            in_str = not in_str
            out.append(ch)
        elif in_str and ch == "\n":
            out.append("\\n")
        elif in_str and ch == "\r":
            out.append("\\r")
        elif in_str and ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)


def _drop_trailing_commas(text: str) -> str:
    """删掉对象/数组末尾的多余逗号（仅在字符串外生效，不误伤正文）。"""
    out: list[str] = []
    in_str = escaped = False
    for i, ch in enumerate(text):
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if ch == "," and not in_str:
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                continue  # 尾逗号，丢弃
        out.append(ch)
    return "".join(out)


def _repair(frag: str) -> str:
    """低成本文本修复：裸换行 → 转义、尾逗号 → 删除、控制字符 → 清除。"""
    return _CTRL.sub("", _drop_trailing_commas(_escape_raw_breaks(frag)))


def extract_json(text: str, kind: str = "object"):
    """从 LLM 输出中提取 JSON，容忍围栏、前后废话与常见畸形。

    kind: "object" 取 {} / "array" 取 []。
    无法解析时抛 ValueError（消息含原文片段，便于定位）。
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("LLM 返回为空")
    body = _strip_fence(text)
    open_ch, close_ch = ("{", "}") if kind == "object" else ("[", "]")
    frag = _slice(body, open_ch, close_ch)
    if frag is None:
        raise ValueError(f"LLM 未返回 JSON {kind}：{body[:200]}")
    try:
        return json.loads(frag)
    except json.JSONDecodeError as first:
        try:
            return json.loads(_repair(frag))
        except (json.JSONDecodeError, ValueError):
            raise ValueError(
                f"LLM 返回的 JSON 无法解析（{first}）；原文片段：{frag[:200]}"
            ) from first


def extract_json_or_none(text: str, kind: str = "object"):
    """同 extract_json，但解析不了时返回 None（用于"允许模型答非所问"的场景）。"""
    try:
        return extract_json(text, kind=kind)
    except ValueError:
        return None


def chat_json(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    kind: str = "object",
    retries: int = 1,
    **chat_kw,
):
    """调用 LLM 并解析 JSON；解析失败则追加格式纠正提示重试。

    比"文本修复"更可靠：畸形输出往往修不回来，但模型重答一次基本就好。
    重试仍失败时抛 ValueError。
    """
    attempt = list(messages)
    last: Exception | None = None
    for _ in range(retries + 1):
        raw = llm.chat(attempt, model=model, temperature=temperature, **chat_kw)
        try:
            return extract_json(raw, kind=kind)
        except ValueError as e:  # noqa: BLE001 解析失败 → 纠正后重试
            last = e
            attempt = list(messages) + [{"role": "user", "content": _RETRY_HINT}]
    raise last  # type: ignore[misc]

"""生成后按统一字数口径校准篇幅，保留图表与图片引用，不机械截断正文。"""
import re
from app.core.page_plan import count_words


def status(body: str, target: int | None, tolerance: float = .15) -> dict:
    words = count_words(body)
    deviation = (words - target) / target if target and target > 0 else None
    return {"words": words, "target_words": target, "deviation": deviation,
            "within_tolerance": deviation is None or abs(deviation) <= tolerance}


def _protected(body: str) -> tuple:
    return (
        tuple(s.strip() for s in re.findall(r"```mermaid\s*\n(.*?)```", body, re.S)),
        tuple(re.findall(r"\[图片占位[^\]]*\]", body)),
        tuple(re.findall(r"!\[[^\]]*\]\([^)]+\)", body)),
        # 表格可重述，但不能通过删掉响应条目来凑字数。
        sum(1 for line in body.splitlines() if line.strip().startswith("|")
            and line.strip().endswith("|")),
    )


def fit(body: str, target: int | None, rewrite, tolerance: float = .15) -> str:
    """最多两次调整，只采纳更接近目标且未丢失配图/表格的版本。失败保留原正文。"""
    if not target or target <= 0:
        return body
    best = body
    protected = _protected(body)
    for _ in range(2):
        info = status(best, target, tolerance)
        if info["within_tolerance"]:
            break
        try:
            candidate = rewrite(best, info["words"], target, tolerance)
        except Exception as exc:
            print(f"[篇幅校准] 调整失败，保留已有正文：{type(exc).__name__}")
            break
        if not isinstance(candidate, str) or not candidate.strip():
            break
        if _protected(candidate) != protected:
            print("[篇幅校准] 调整改变了配图或表格条目，未采纳")
            break
        if abs(count_words(candidate) - target) >= abs(info["words"] - target):
            break
        best = candidate
    return best

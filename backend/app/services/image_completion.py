"""正文生成后的配图补齐；失败的可合成插图仅记问题，不在正文保留空占位。"""
import json
import re

from app.core.config import settings
from app.services.generated_images import eligible, generate, ImageGenerationError
from app.services.image_retrieval import bind_references, selected_ids


class HistoryUnavailable(ImageGenerationError):
    """历史素材不可用或核验失败：留待人工重新确认，不自动付费生图。"""


def has_requests(ctx: dict) -> bool:
    return any(eligible(n) for n in (ctx.get("outline") or {}).get("image_needs") or [])


def _choose_history(need: dict, refs: list[dict]) -> dict | None:
    if need.get('asset_id'):
        asset = next((a for a in refs if a['asset_id'] == need['asset_id']), None)
        if asset is None:
            raise HistoryUnavailable('思路指定的历史素材当前不可用，请重新确认配图，未自动付费生图')
        return asset
    if not refs:
        return None
    # 同名只是召回信号，不证明内容适用；只有思路明确指定的可见素材ID可直接认领。
    from app.services.llm import llm
    from app.agents.json_util import extract_json

    prompt = {"need": need, "candidates": [
        {k: a.get(k) for k in ("asset_id", "caption", "content", "generated")} for a in refs]}
    try:
        raw = llm.chat([
            {"role": "system", "content": "判断候选图片是否能准确满足配图需求。只能依据给出的图名和视觉描述，"
             "不能把主题略相似当作内容相同，不能把历史材料当作本项目实证。"
             "只有明确匹配才返回其素材ID；不确定或均不匹配返回null。只输出JSON：{\"asset_id\":null}"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ], temperature=0, max_tokens=256, timeout=45)
        identity = extract_json(raw).get("asset_id")
    except Exception:
        raise HistoryUnavailable("历史图片核验失败，未自动付费生图，请稍后重试") from None
    if identity is None:
        return None
    chosen = next((a for a in refs if a["asset_id"] == identity), None)
    if chosen is None:
        raise HistoryUnavailable("历史图片核验返回无效素材ID，未自动生图")
    return chosen


def _place(body: str, name: str, replacement: str, already_present: bool = False) -> str:
    found = False
    kept = False

    def replace(match):
        nonlocal found, kept
        raw = match.group(1).strip()
        label = raw.split("|", 1)[0].strip()
        if label != name and not label.startswith(name + "/") and not label.startswith(name + "／"):
            return match.group(0)
        if match.group(0) == replacement and not kept:
            kept = found = True
            return replacement
        if not kept and not already_present:
            kept = True
            found = True
            return replacement
        found = True
        return f"（参见本节配图：{name}）"

    parts = re.split(r"(```.*?```)", body, flags=re.S)
    result = "".join(part if part.startswith("```") else re.sub(
        r"\[图片占位[：:]\s*([^\]]+)\]", replace, part) for part in parts)
    return result if found or already_present else result.rstrip() + "\n\n" + replacement + "\n"


def complete(ctx: dict, body: str, project_id: int, node_id: int) -> str:
    refs = list(ctx.get("image_refs") or [])
    body = bind_references(body, refs)
    attempted = 0
    errors = []
    reviews = []
    for need in (ctx.get("outline") or {}).get("image_needs") or []:
        if not eligible(need) or need["name"] in (ctx.get("img_skips") or {}):
            continue
        name = str(need["name"]).strip()
        try:
            try:
                if ctx.get("image_search_ok") is False:
                    raise ImageGenerationError("历史素材检索暂不可用")
                asset = _choose_history(need, refs)
            except HistoryUnavailable:
                # 思路点名的历史素材不可用或核验失败：不自动生图，交给人工重新确认。
                raise
            except ImageGenerationError as exc:
                reviews.append({'name':name,'reason':str(exc)+'；已尝试AI示意替代，请核对'})
                asset = None
            if asset is None:
                if attempted >= max(0, settings.image_generation_max_per_chapter):
                    raise ImageGenerationError("已达到本章自动生图数量上限")
                attempted += 1
                asset = generate(project_id, node_id, need, body)
                refs.append(asset)
            identity = asset["asset_id"]
            present = identity in selected_ids(body)
            marker = f"[图片占位：{asset['caption']}|素材ID={identity}]"
            body = _place(body, name, marker, present)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ImageGenerationError) else "配图处理失败，请查看后端日志"
            if isinstance(exc, HistoryUnavailable):
                # 历史图未通过核验时可能已被 bind_references 自动挂上素材ID；
                # 先去绑再移除，避免未经核验的图沿占位流入导出件。
                body = _unbind_placeholder(body, name)
            body, anchor = remove_failed_placeholder(body, name)
            errors.append({"name": name, "reason": reason, "context":anchor})
    ctx["image_refs"] = refs
    ctx["image_generation_errors"] = errors
    ctx["image_review_warnings"] = reviews
    return body


def _unbind_placeholder(body: str, name: str) -> str:
    """去掉与失败需求同名占位上的素材ID，让后续清理能移除此占位。"""
    def replace(match):
        raw = match.group(1).strip()
        label = re.split(r"\|\s*素材ID", raw, maxsplit=1)[0].strip()
        if label == name or label.startswith(name + "/") or label.startswith(name + "／"):
            return f"[图片占位：{label}]"
        return match.group(0)
    parts = re.split(r"(```.*?```)", body, flags=re.S)
    return "".join(part if part.startswith("```") else re.sub(
        r"\[图片占位[：:]\s*([^\]]+)\]", replace, part) for part in parts)


def remove_failed_placeholder(body: str, name: str) -> tuple[str, str]:
    """仅移除本次失败需求的无素材ID占位，不碰其他图、真实材料或代码。"""
    pattern = re.compile(r'\[图片占位[：:]\s*([^\]]+)\]')
    parts = re.split(r'(```.*?```)', body, flags=re.S)
    def replace(m):
        label = m[1].strip()
        if '素材ID=' not in label and (label == name or label.startswith(name+'/') or label.startswith(name+'／')):
            return ''
        return m[0]
    result = ''.join(p if p.startswith('```') else pattern.sub(replace,p) for p in parts)
    paragraphs = [p.strip() for p in result.split('\n\n') if p.strip() and not p.strip().startswith(('```','[图片占位'))]
    anchor = next((p for p in reversed(paragraphs) if not p.startswith('#')), '')[-200:]
    return result, anchor

"""正文生成后的缺图补齐：确认历史候选 → 生成允许合成的插图 → 保留失败占位。"""
import json
import re

from app.core.config import settings
from app.services.generated_images import eligible, generate, ImageGenerationError
from app.services.image_retrieval import bind_references, selected_ids


def has_requests(ctx: dict) -> bool:
    return any(eligible(n) for n in (ctx.get("outline") or {}).get("image_needs") or [])


def _choose_history(need: dict, refs: list[dict]) -> dict | None:
    if need.get('asset_id'):
        asset = next((a for a in refs if a['asset_id'] == need['asset_id']), None)
        if asset is None:
            raise ImageGenerationError('思路指定的历史素材当前不可用，请重新确认配图，未自动付费生图')
        return asset
    if not refs:
        return None
    # 唯一完整图名命中是确定性来源，其他情况需语义核验，不能按相似度强插。
    exact = [a for a in refs if a["caption"] == need["name"]]
    if len({a["asset_id"] for a in exact}) == 1:
        return exact[0]
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
        raise ImageGenerationError("历史图片核验失败，未自动付费生图，请稍后重试") from None
    if identity is None:
        return None
    chosen = next((a for a in refs if a["asset_id"] == identity), None)
    if chosen is None:
        raise ImageGenerationError("历史图片核验返回无效素材ID，未自动生图")
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
    for need in (ctx.get("outline") or {}).get("image_needs") or []:
        if not eligible(need) or need["name"] in (ctx.get("img_skips") or {}):
            continue
        name = str(need["name"]).strip()
        try:
            if ctx.get("image_search_ok") is False:
                raise ImageGenerationError("历史素材检索失败，未把检索失败当作缺图来生成")
            asset = _choose_history(need, refs)
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
            errors.append({"name": name, "reason": reason})
            # 失败仍留下可行动的缺图位置，不能静默吞掉，也不影响整章正文保存。
            if name not in body:
                body = body.rstrip() + f"\n\n[图片占位：{name}]\n"
    ctx["image_refs"] = refs
    ctx["image_generation_errors"] = errors
    return body

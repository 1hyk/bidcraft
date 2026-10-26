"""独立图片检索：领域/归属过滤 → 本地真图 → 章节相关候选 → 确定性归属。

不修改知识页匹配结果；像素摘要作为素材 ID，正文与导出均用这个 ID 绑定。
"""
import json
import math
import os
import re
import threading
import time
from pathlib import Path

from app.core.database import query
from app.services.image_ownership import image_name, NOT_SUGGESTIBLE_KINDS, resolve_image_skips

# 像素摘要要解码整张图；检查页每次刷新会为上百张历史图重复计算，单次能到十几秒。
# 同一个文件内容没变（路径、mtime、大小都相同）就直接复用结果；结果同时落盘，
# 后端重启后也不用重算一遍。键里带 mtime/size，文件被替换时自动失效。
_IDENTITY_CACHE: dict[tuple[str, int, int], str] = {}
_IDENTITY_LOCK = threading.Lock()
_IDENTITY_LOADED = False
_IDENTITY_DIRTY = False
_IDENTITY_SAVED_AT = 0.0
_IDENTITY_CACHE_FILE = ".image_identity_cache.json"


def _cache_path() -> Path:
    from app.core.config import settings
    return Path(settings.uploads_dir) / _IDENTITY_CACHE_FILE


def _load_identity_cache() -> None:
    global _IDENTITY_LOADED
    with _IDENTITY_LOCK:
        if _IDENTITY_LOADED:
            return
        _IDENTITY_LOADED = True
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 缓存缺失/损坏只影响速度，不影响正确性
        return
    if not isinstance(raw, dict):
        return
    with _IDENTITY_LOCK:
        for path, value in raw.items():
            try:
                mtime, size, identity = value
                if isinstance(path, str) and identity.startswith("IMG-"):
                    _IDENTITY_CACHE[(path, int(mtime), int(size))] = identity
            except (TypeError, ValueError, AttributeError):
                continue


def _save_identity_cache(force: bool = False) -> None:
    global _IDENTITY_DIRTY, _IDENTITY_SAVED_AT
    now = time.monotonic()
    if not force and now - _IDENTITY_SAVED_AT < 5:
        return
    with _IDENTITY_LOCK:
        if not _IDENTITY_DIRTY:
            return
        payload = {path: [mtime, size, identity]
                   for (path, mtime, size), identity in _IDENTITY_CACHE.items()}
        _IDENTITY_DIRTY = False
        _IDENTITY_SAVED_AT = now
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:  # noqa: BLE001 写缓存失败不能让主流程失败
        pass


def image_identity(path) -> str:
    import hashlib
    from PIL import Image, ImageOps

    global _IDENTITY_DIRTY

    _load_identity_cache()
    p = Path(path)
    try:
        stat = p.stat()
        key = (str(p.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    if key is not None:
        with _IDENTITY_LOCK:
            cached = _IDENTITY_CACHE.get(key)
        if cached:
            return cached
    with Image.open(path) as image:
        rgb = ImageOps.exif_transpose(image).convert("RGBA")
        digest = hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).hexdigest()
    value = "IMG-" + digest[:24]
    if key is not None:
        with _IDENTITY_LOCK:
            _IDENTITY_CACHE[key] = value
            _IDENTITY_DIRTY = True
            if len(_IDENTITY_CACHE) > 8192:      # 防止长期运行的项目切换把内存撑大
                _IDENTITY_CACHE.clear()
        _save_identity_cache()
    return value


def catalog(project_id: int) -> list[dict]:
    from app.core.domains import domain_of_project
    from app.services.matching import _scope_sql
    from app.services.image_vision import local_path_of

    domain = domain_of_project(project_id)
    wall, params = _scope_sql(project_id)
    rows = query(
        "SELECT p.id, p.doc_id, p.title, p.content FROM knowledge_page p"
        " LEFT JOIN knowledge_doc d ON d.id = p.doc_id"
        " WHERE p.domain = %s AND p.status != 'deprecated' AND " + wall
        + " ORDER BY p.id", (domain, *params),
    )
    out, identities = [], {}
    for row in rows:
        content = row.get("content") or {}
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (ValueError, TypeError):
                continue
        if not isinstance(content, dict):
            continue
        for image in content.get("images") or []:
            caption = image_name(image)
            if not caption or image.get("kind") in NOT_SUGGESTIBLE_KINDS:
                continue
            path = local_path_of(str(image.get("url") or ""))
            if path is None:
                continue
            try:
                if path not in identities:
                    identities[path] = image_identity(path)
                asset_id = identities[path]
            except Exception:
                continue  # 损坏或无法解码的图片不能作为可复用素材
            out.append({
                "asset_id": asset_id, "caption": caption, "url": image["url"],
                "kind": image.get("kind"), "content": image.get("content") or image.get("description") or "",
                "page_id": row["id"], "doc_id": row["doc_id"],
                "source_title": row.get("title") or "", "path": path,
            })
    from app.services.generated_images import catalog as generated_catalog

    result = out + generated_catalog(project_id)
    _save_identity_cache(force=True)
    return result


def _terms(text: str) -> set[str]:
    text = re.sub(r"[^\w\u3400-\u9fff]", "", str(text or "").lower())
    return {text[i:i + 2] for i in range(len(text) - 1)}


def candidates(node: dict, assets: list[dict], limit: int = 6) -> list[dict]:
    outline = node.get("outline") or {}
    needs = outline.get("image_needs") or []
    # material 也要召回历史候选：这类需求常直接点名一张历史图（如「无人机实飞测试现场（一）」），
    # 只把它当"待补真实材料"会让整章候选为空，连人工挑图都没有可选项。
    # 是否可作为本项目实证由下游（写手与 image_completion）决定，检索层不替它做结论。
    historical = [n for n in needs if isinstance(n, dict) and n.get("source") in {"history", "illustration", "material"}]
    planned_ids = {n.get('asset_id') for n in historical if n.get('asset_id')}
    if (needs and not historical) or (not needs and outline.get("need_image") is False):
        return []
    need = " ".join([node.get("title") or "", str(outline.get("thinking") or ""),
                     " ".join(map(str, outline.get("key_points") or []))])
    need += " " + " ".join(str(n.get("name") or "") + " " + str(n.get("purpose") or "")
                             for n in historical)
    terms = _terms(need)
    title_terms = _terms(node.get("title") or "")
    ranked = []
    from app.services.generated_images import VISUAL_CHECK_VERSION
    for asset in assets:
        # 老版本生成图保留给已有正文导出，但没有视觉核验的不能作为新稿候选复用。
        if asset.get('generated') and (not asset.get('validated') or asset.get('visual_check_version',0)<VISUAL_CHECK_VERSION):
            continue
        description = _terms(asset["caption"] + " " + str(asset["content"]))
        score = len(terms & description) / math.sqrt(max(1, len(terms) * len(description)))
        caption_terms = _terms(asset["caption"])
        # 长思路不能稀释短图名的主题信号。
        title_score = len(title_terms & caption_terms) / math.sqrt(
            max(1, len(title_terms) * len(caption_terms)))
        score = max(score, .7 * title_score)
        # 相似度只用于召回，是否真的使用由写手结合本项目判断；绝不按相似图名直接插图。
        if asset["caption"] in need:
            score += 1
        if asset['asset_id'] in planned_ids:
            score += 10
        if score >= .12:
            ranked.append((score, asset))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].get("page_id") or 0, pair[1]["asset_id"]))
    result, seen = [], set()
    for score, asset in ranked:
        if asset["asset_id"] in seen:
            continue
        seen.add(asset["asset_id"])
        result.append({k: v for k, v in asset.items() if k != "path"})
        if len(result) == limit:
            break
    return result


def project_plan(project_id: int) -> dict[int, list[dict]]:
    """批量开写前算一次，单章也按全目录分配，避免并发抢图。"""
    from app.services.section_service import get_tree

    assets = catalog(project_id)
    nodes, refs = [], {}

    def walk(tree, parent=None):
        for node in tree:
            nid = node["id"]
            refs[nid] = candidates(node, assets)
            nodes.append({"id": nid, "title": node["title"], "parent_id": parent,
                          "sort_order": len(nodes),
                          "captions": [a["asset_id"] for a in refs[nid]]})
            walk(node.get("children") or [], nid)

    walk(get_tree(project_id))
    skips = resolve_image_skips(nodes)
    return {nid: [a for a in images if a["asset_id"] not in (skips.get(nid) or {})]
            for nid, images in refs.items()}


def bind_references(body: str, refs: list[dict]) -> str:
    """按已核定候选绑定素材 ID；有 ID 时强制恢复原图名，不做模糊认领。"""
    by_id = {a["asset_id"]: a for a in refs}

    def replace(match):
        raw = match.group(1).strip()
        bound = re.search(r"素材ID\s*[=:：]\s*(IMG-[0-9a-f]{24})", raw)
        asset = None
        if "素材ID" in raw:
            asset = by_id.get(bound.group(1)) if bound else None
        else:
            names = [raw] + [raw[:m.start()].strip() for m in re.finditer(r"[/／]", raw)]
            for name in names:
                hits = [a for a in refs if a["caption"] == name]
                if len({a["asset_id"] for a in hits}) == 1:
                    asset = hits[0]
                    break
        if asset:
            return f"[图片占位：{asset['caption']}|素材ID={asset['asset_id']}]"
        # 无效或不属于本章候选的 ID 不可信，不能冒充已绑定素材流入导出件。
        clean_name = re.split(r"\|\s*素材ID", raw, maxsplit=1)[0].strip()
        return f"[图片占位：{clean_name}]"

    # 代码围栏里的示例是字面文本，不能当成实际配图消费。
    parts = re.split(r"(```.*?```)", body, flags=re.S)
    return "".join(part if part.startswith("```") else re.sub(
        r"\[图片占位[：:]\s*([^\]]+)\]", replace, part) for part in parts)


def selected_ids(body: str) -> set[str]:
    text = re.sub(r"```.*?```", "", body, flags=re.S)
    return set(re.findall(r"\[图片占位[^\]]*素材ID\s*[=:：]\s*(IMG-[0-9a-f]{24})[^\]]*\]", text))

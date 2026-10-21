"""目录、思路、正文共同使用的招标依据；评分项不冒充明确规定的目录。"""
from copy import deepcopy
import re

from app.core.chapter_title import normalize
from app.core.database import query
from app.services.requirement_service import _lot_filter


def load_requirements(project_id: int) -> list[dict]:
    # id 保留本次抽取的原始条目顺序；人工确认状态不能改变评分项的目录顺序。
    return _lot_filter(query(
        "SELECT id,category,title,content,weight,suggested_chapter,source_file,"
        "source_location,applicable_lots FROM tender_requirement"
        " WHERE project_id=%s AND status != 'ignored' ORDER BY id", (project_id,)), project_id)


def is_technical_score(row: dict) -> bool:
    """评审定标方法不是技术评分项，不能成为技术方案的固定章节。"""
    from app.services.requirement_matrix import response_scope
    if row.get('category') != '评分办法' or response_scope(row) == 'other_documents':
        return False
    title = str(row.get('title') or '').strip()
    if re.search(r'最低评审价|最低价法|最低投标价|价格评审|价格评分|报价评分|经评审的最低', title):
        return False
    return normalize(title) not in {'评标方法', '评审方法', '评标办法', '综合评分法'}


def scope_chapters(tender_toc: dict | None, requirements: list[dict], scope: str) -> list[dict]:
    """规定目录优先，其次技术评分项；没有技术评分项时按技术要求组织工作章节。"""
    if not scope:
        return []
    roots = (tender_toc or {}).get("chapters") or []
    exact = [c for c in roots if normalize(c.get("title")) == normalize(scope)]
    if len(exact) > 1:
        raise ValueError("招标目录中存在多个同名编制范围")
    if exact and exact[0].get("children"):
        return deepcopy(exact[0]["children"])
    chapters, seen = [], set()
    candidates = [r for r in requirements if is_technical_score(r)]
    if not candidates:
        from app.services.requirement_matrix import response_scope
        candidates = [r for r in requirements if r.get('category') == '技术要求'
                      and response_scope(r) == 'technical']
    for r in candidates:
        title = str(r.get("suggested_chapter") or r.get("title") or "").strip()
        name = normalize(title)
        if not name or name == normalize(scope) or name in seen:
            continue
        seen.add(name)
        chapters.append({"title": title, "children": []})
    # 已抽取成平铺的本册目录（而非整本响应文件）仍具有顺序优先权。
    if roots and all(normalize(c.get("title")) in seen for c in roots):
        by_name = {normalize(c["title"]): c for c in chapters}
        ordered = [dict(by_name[normalize(c["title"])], title=c["title"]) for c in roots]
        prescribed = {normalize(c["title"]) for c in roots}
        it = iter(ordered)
        chapters = [next(it) if normalize(c["title"]) in prescribed else c for c in chapters]
    return chapters


def validate_scope_coverage(toc: dict, rows: list[dict], tender_toc: dict | None, scope: str) -> None:
    """本轮承载要求的主章必须保留，错误或空目录不能以成功状态替换旧稿。"""
    if not scope:
        return
    required = scope_chapters(tender_toc, rows, scope)
    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get('children') or [])
    nodes = list(walk(toc.get('chapters') or []))
    root = next((n for n in nodes if normalize(n.get('title')) == normalize(scope)), None)
    if not root:
        raise ValueError('目录缺少当前编制范围，原目录未替换')
    actual = {normalize(n.get('title')) for n in walk(root.get('children') or [])}
    missing = [r['title'] for r in required if normalize(r['title']) not in actual]
    if missing:
        raise ValueError('目录缺少招标要求对应主章：'+'、'.join(missing)+'；原目录未替换')


def requirements_text(rows: list[dict], titles: list[str] | None = None) -> str:
    """原文、来源和条目 ID 一起传递；不再只传评分项名称。"""
    from app.core.chapter_title import related
    selected = rows if titles is None else [r for r in rows if any(
        related(t, r.get("suggested_chapter") or r.get("title")) for t in titles)]
    return "\n".join(
        f"- [要求#{r['id']}][{r['category']}] {r['title']}"
        f"\n  来源：{r.get('source_file') or ''} {r.get('source_location') or ''}"
        f"\n  原文要求：{r.get('content') or ''}" for r in selected)


def explicit_stage_requirements(rows):
    for r in rows:
        content=str(r.get('content') or '')
        match=re.search(r'(?:必须|应|需|须)[^。；\n]{0,10}?(?:覆盖|包括|包含)'
                        r'([^。；\n]{1,160}?)[0-9一二三四五六七八九十]+个?阶段',content)
        if not match:
            continue
        stages=[re.sub(r'(?:阶段)?(?:实施)?方案$|阶段$','',s.strip())
                for s in re.split(r'[、，,]',match[1])]
        if not 2<=len(stages)<=12 or any(not s or len(s)>16 for s in stages):
            continue
        yield r, normalize(r.get('suggested_chapter') or r.get('title')), stages


def required_stage_children(title, rows):
    for row, target, stages in explicit_stage_requirements(rows):
        if normalize(title) == target:
            return [{"title": stage, "children": []} for stage in stages]
    return []


def validate_stage_coverage(toc: dict, rows: list[dict]) -> None:
    """明确列举的阶段必须能在对应子树定位，漏项不能成功替换原目录。"""
    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get('children') or [])
    nodes=list(walk(toc.get('chapters') or []))
    for r, target, stages in explicit_stage_requirements(rows):
        branch=next((n for n in nodes if normalize(n['title'])==target),None)
        if not branch:
            raise ValueError(f"要求#{r['id']}缺少对应章节，原目录未替换")
        titles='\n'.join(n['title'] for n in walk(branch.get('children') or []))
        missing=[s for s in stages if s not in titles]
        if missing:
            raise ValueError(f"要求#{r['id']}目录缺少阶段：{'、'.join(missing)}，原目录未替换")

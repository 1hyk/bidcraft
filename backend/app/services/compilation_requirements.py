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


_CLAUSE_RESPONSE = re.compile(r'响应|应答|偏离|符合性|满足')
_CLAUSE_TARGET = re.compile(r'条款|逐条|技术指标|技术要求|▲|★')


def is_clause_response_score(row: dict) -> bool:
    """判断评分项是否在对“附件N条款”整体考察响应/偏离。

    抽取结果不一定给出 suggested_chapter；目录归属不能只看映射字段，
    但也不能把所有空白映射的评分项都塞进技术方案。这里要求同时出现
    附件编号、条款对象和响应语义，避免把普通方案评分误判成整册条款响应。
    """
    if not is_technical_score(row):
        return False
    content = str(row.get('content') or '')
    return (bool(attachment_key(content))
            and bool(_CLAUSE_TARGET.search(content))
            and bool(_CLAUSE_RESPONSE.search(content)))


def scope_chapters(tender_toc: dict | None, requirements: list[dict], scope: str) -> list[dict]:
    """规定目录优先，其次技术评分项；没有技术评分项时按技术要求组织工作章节。"""
    if not scope:
        return []
    roots = (tender_toc or {}).get("chapters") or []
    exact = [c for c in roots if normalize(c.get("title")) == normalize(scope)]
    if len(exact) > 1:
        raise ValueError("招标目录中存在多个同名编制范围")
    if exact and exact[0].get("children"):
        children = deepcopy(exact[0]["children"])
        # 招标目录写的是“必须包含”的下限，不是穷举。对整册技术方案单独计分的评分项
        # （如“技术指标响应情况考察”）不属于任何规定子章，不能因为目录有子节就被吞掉。
        prescribed = {normalize(c.get("title")) for c in children}
        extras = []
        for r in requirements:
            if not is_technical_score(r):
                continue
            mapped = normalize(r.get("suggested_chapter"))
            # 招标目录已规定子章时，映射到本册的评分项要补进来；
            # 抽取把整册“附件N条款响应”的映射留空时，也要按招标正文识别，
            # 不能因为某个字段没抽到就让必评项消失。其他章节的映射不在这里展开。
            if mapped != normalize(scope) and not (
                    not mapped and is_clause_response_score(r)):
                continue
            title = str(r.get("title") or "").strip()
            name = normalize(title)
            # 同名评分项不能嵌成“技术方案 > 技术方案”；它没有提供新的章节信息。
            if not name or name == normalize(scope) or name in prescribed:
                continue
            prescribed.add(name)
            extras.append({"title": title, "children": []})
        return extras + children
    chapters, seen = [], set()
    candidates = [r for r in requirements if is_technical_score(r)]
    if not candidates:
        from app.services.requirement_matrix import response_scope
        candidates = [r for r in requirements if r.get('category') == '技术要求'
                      and response_scope(r) == 'technical']
    for r in candidates:
        title = str(r.get("suggested_chapter") or "").strip()
        # 评分项被抽取到编制范围本身（suggested_chapter=技术方案）时，它是整册评分项，
        # 章名应取条目名称；直接丢掉会让“技术指标响应情况考察”这类必评项消失。
        # 技术要求映射到编制范围时仍只是本册内容，不能逐条变成主章。
        if is_technical_score(r) and (not title or normalize(title) == normalize(scope)):
            title = str(r.get("title") or "").strip()
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
    """原文、来源和条目名称一起传递；不把内部 ID 交给模型，避免原文编号泄漏进正文。"""
    from app.core.chapter_title import related
    selected = rows if titles is None else [r for r in rows if any(
        related(t, r.get("suggested_chapter") or r.get("title")) for t in titles)]
    def label(r: dict) -> str:
        return re.sub(
            r'\s+', ' ',
            str(r.get('source_location') or r.get('title') or '采购文件'),
        ).strip()
    return "\n".join(
        f"- [{label(r)}][{r['category']}] {r['title']}"
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


_CN_NUM = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
           '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}


def attachment_key(text) -> str:
    """把“附件1/附件一/附件 1”统一成编号，供条款归属比对。"""
    match = re.search(r'附件\s*([0-9]+|[一二三四五六七八九十]+)', str(text or ''))
    if not match:
        return ''
    number = match.group(1)
    return _CN_NUM.get(number, number)


def required_response_children(title, rows, scope: str = ""):
    """整册条款响应评分项按本册技术条款逐项展开，避免只写总表、不落具体响应。

    只有“评分项 + 附件N条款响应/偏离”的条目才展开；展开范围限于**同一附件的技术条款**，
    且跳过已由其他规定章节承载的条款（否则工期、验收交付物会被重复成响应子节）。
    普通技术方案章节仍交给模型组织。
    """
    target = normalize(title)
    if not target:
        return []
    def is_response_item(r):
        if not is_technical_score(r):
            return False
        if normalize(r.get("suggested_chapter")) not in {"", normalize(scope)}:
            return False
        # 只认条款响应/偏离/应答，不认泛指；附件编号在下面统一归一化。
        return is_clause_response_score(r)
    trigger = next((r for r in rows
                    if normalize(r.get("title")) == target and is_response_item(r)), None)
    if not trigger:
        return []
    ref = attachment_key(trigger.get("content"))
    from app.services.requirement_matrix import response_scope
    # 其他技术评分项对应的章节已经承载的条款，不再重复展开。
    # 章名可能取评分项标题，也可能取 suggested_chapter（如“售后服务与保障方案”），两者都算。
    covered = set()
    for r in rows:
        if not is_technical_score(r) or normalize(r.get("title")) == target:
            continue
        covered.add(normalize(r.get("title")))
        chapter = normalize(r.get("suggested_chapter"))
        if chapter:
            covered.add(chapter)
    children, seen = [], set()
    for r in rows:
        if r.get("category") != "技术要求":
            continue
        # 最高限价、付款方式等虽可能被抽成“技术要求”，但不是技术响应条款。
        if response_scope(r) != "technical":
            continue
        # 评分项说的是“附件1条款”，抽取自采购公告的工期/质保/知识产权不能算进来。
        # 附件号优先看来源位置；抽取只把附件号写进正文时再从正文兜底。
        row_ref = (attachment_key(r.get("source_location"))
                   or attachment_key(r.get("content")))
        if row_ref != ref:
            continue
        mapped = normalize(r.get("suggested_chapter"))
        if mapped and mapped != normalize(scope) and mapped in covered:
            continue
        name = normalize(r.get("title"))
        if not name or name in seen:
            continue
        seen.add(name)
        base = str(r.get("title")).strip()
        # 条款名称本身已以“响应/应答”结尾时不再叠词。
        child_title = base if base.endswith(("响应", "应答")) else f"{base}响应"
        children.append({"title": child_title, "children": []})
    return children


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

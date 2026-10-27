"""编排/规划智能体（M5-4, P0）。

职责：招标要求/评分办法提取 → 章节覆盖检查清单 → 目录生成 → 逐章编写思路。

目录装配（方案 3.2）：
- 当前招标目录优先；无规定目录时按当前要求组织；
- 历史素材仅用于相关章节内部的方法细化，不确定目录范围。

目录来源：当前招标规定的目录，或当前技术要求与评分办法；缺少依据时停止。
"""
import json
import re

from app.agents.json_util import extract_json
from app.core.domains import fill_genre
from app.services.deliverable_policy import prompt_suffix, sanitize_obj
from app.services.llm import llm

# 章级骨架**不再由本模块自带**——这里原先有两张写死的表（`SKELETON_CHAPTERS` 与
# `requirement_agent.SUGGESTED_CHAPTERS`），内容是 11 个铁路土建章名。它们的注释写着
# 「跨工程类型通用」，写的时候也确实通用——因为那时候只有一个领域。2026-10-15 删掉。
#
# 目录以当前招标规定、技术要求和评分办法为依据。
# 历史知识只在已确定章节内辅助细化，不提供固定领域章名。

def _chapters_block(chapters: list[str] | None, tender_led: bool = False) -> str:
    """兼容旧调用，不再将领域或历史样本的章名注入自动目录生成。"""
    return ""


# （`TOC_SYSTEM` 与 `generate_toc` 已于 2026-10-15 删除：它们是"一次调用出章+节"的旧
#   实现，早已被下面两阶段的 `generate_toc_titles` / `generate_toc_children` 取代，
#   全仓零调用点。留着只会继续把写死的章名表散播到更多地方。）

from app.services.contact_policy import CONTACT_POLICY

OUTLINE_SYSTEM = CONTACT_POLICY + '\n' + """你是资深{G_FULL}编制专家，负责为指定章节生成编写思路（提纲）。

要求：
1. 基于该章节的类型、本项目事实、可参考的历史知识页，给出该章节的编写思路；
2. 思路要落到"这一章写什么、按什么顺序、用哪些材料、突出什么"，而不是泛泛而谈；
3. 只输出 JSON。
4. 明确列出本章真正需要的配图 image_needs：name 为图名，purpose 为用途，source
   为 mermaid（根据本章做法绘制关系图）、history（检索历史真实图片作参考示例）、
   illustration（非关系图的概念场景、空间布置效果等，历史没有时可生成示意插图）或
   material（必须取得本项目的真实材料）。illustration 可补充 prompt 描述画面。
   history 仅在内容属于可合成示意时设 allow_generated=true，真实照片、截图、证照、
   检测报告等必须设 false；不得为获取配图而把真实材料改称概念示意。
   图表应服务于内容，不要为了配图而凑图。
   每项补充 content_origin：proposal_design（我方方案安排）或 external_evidence（外部真实材料）；
   data_structure 按实际内容填写：records（记录）、assignment（岗位/班次分配）、comparison（对照）、
   measurements（数据）、timeline（任务时间跨度）、process（流程）、hierarchy（层级）、
   relationships（关系）、physical_scene（真实物体/场地的外观与摆位）、
   spatial_layout（分区、航线、覆盖、高度层、位置等空间信息）。不能只根据图名猜类型。
   分配、记录、对照和数据内容用可编辑表格；任务跨度用甘特图；关系用 Mermaid。
   空间类内容先判断信息载体：把示意线、色块、箭头和标注全部去掉后，这张图要说明的信息还在不在？
   - 还在：信息只靠真实物体和场地的外观承载 → physical_scene，visual_style=photorealistic。
   - 不在：信息只靠边界、分区、航线、覆盖范围等现实中不可见的图示符号承载 → spatial_layout，
     visual_style=spatial_illustration（俯视平面示意）。
   - 两者都有（既要表现现场实物，又要表达航线、覆盖范围、分区或点位）→ **必须拆成两条
     image_needs**：一条 physical_scene 现场实景图，一条 spatial_layout 空间信息图。不要合并成
     一张，也不要在写实图上叠加符号。本章内容确实只需要其中一方面的，才只规划一条。
   判断依据是信息载体，不是图名里有没有“示意/图”等字；不要根据行业词表套画风。
   写实质感不代表实拍证据。写实场景只用真实物体及其位置表达关系，不叠加虚线、箭头、引线、
   圈注、覆盖扇区或发光连线；这些内容应另列一条空间信息图表达。
   场景插图的 prompt 明确主体、数量或相对规模、位置关系、视角、关键细节，不放整段正文，
   不虚构具体型号或实测状态；图名在正文单独排版，不画进图片。
   没有需求就给空数组；need_image 与 image_needs 是否为空保持一致。

输出 JSON 结构：
{
  "chapter_title": "章节标题",
  "chapter_type": "骨架章节类型",
  "thinking": "编写思路（300-800字）",
  "key_points": ["本节要覆盖的要点"],
  "refs": [{"chapter_type": "可参考的知识页类型", "usage": "引用说明"}],
  "need_table": true,
  "need_image": false,
  "image_needs": [{"name": "图名", "purpose": "用途", "content_origin": "proposal_design|external_evidence", "data_structure": "records|assignment|comparison|measurements|timeline|process|hierarchy|relationships|physical_scene|spatial_layout", "source": "mermaid|history|illustration|material", "asset_id": "history选中候选时填写原始asset_id，其他留空", "allow_generated": false, "visual_style": "photorealistic|spatial_illustration", "prompt": "仅合成示意图填写画面要求"}]
}""" + prompt_suffix()


def _extract_json(text: str) -> dict:
    """薄封装：统一走 json_util（带畸形修复）。"""
    return extract_json(text)


# ---------------------------------------------------------------- 目录流式（两阶段）

TOC_TITLES_SYSTEM = """你是资深{G_INTRO}编制专家，负责拟定目录的**章级骨架**。

要求：
1. 只输出章标题列表（不展开子节）；
2. 【最高优先级】若提供了"招标文件规定的{G_SHORT}目录"，必须以它为骨架原样保留其章；
3. 没有规定目录时，根据当前招标的技术要求、工作范围、交付与验收要求及技术评分内容组织章节。
   每项要求都必须有承接位置，可以将相关要求合并在同一章；不得套用领域固定章名。
   不因历史材料中出现某章就新增本项目没有依据的内容；历史素材不足也不能省略本项目要求。
4. 按当前招标明确的顺序组织，未规定顺序时按工作逻辑排列；只输出 JSON，不要解释。

输出 JSON：{"chapters": [{"title": "第一章标题"}]}""" + prompt_suffix()

TOC_CHILDREN_SYSTEM = """你是资深{G_FULL}编制专家。为目录中指定的某一章生成子节列表。

要求：
1. 招标已列出的大项必须保持原顺序，只有未规定的部分才按编制惯例安排；
   子节不得与父章同名，同层标题不得重复，不自行添加章节序号；
2. **数量由内容决定，不要凑数**——内容单薄的章（函件、声明、单一表格）可以只有 1 节，
   甚至不分子节；内容厚的章（施工方案、技术方案）才需要展开。原先是「按惯例列 2~8 个」，
   那是明着让模型凑，任何章名都能编出两节来；
3. 历史子节仅供方法细化参考，必须核对本次招标是否需要；出现次数不代表本项目必须设置该节。
4. 给了「招标文件格式中列出的大项」就**必须逐一保留**（那是招标的格式红线），可以归并、
   可以在其下再分子节，但不能漏、不能改变规定大项的相对顺序；两者冲突时以招标要求为准；
5. 结合项目实际专业，只列本项目真正需要的内容；最低评审价法、综合评分法等定标方法不是技术工作章节。
   没有技术评分项时，仍须依据技术要求、工作范围、实施与验收要求展开技术内容，不得用价格评审方法替代；
6. 只输出 JSON，不要解释。

输出 JSON：{"children": [{"title": "第X节标题"}]}""" + prompt_suffix()


def _toc_context(project_info: str, professions: list[str] | None,
                 tender_requirements: str, tender_toc: dict | None) -> str:
    prof = professions or []
    user = f"项目信息：{project_info}\n本项目涉及的专业：{prof if prof else '待识别'}\n"
    if tender_toc and tender_toc.get("chapters"):
        user += ("招标文件规定的{G_SHORT}目录（必须以此为准）：\n"
                 + json.dumps(tender_toc["chapters"], ensure_ascii=False) + "\n")
    if tender_requirements:
        user += f"招标要求与评分办法：\n{tender_requirements}\n"
    return user


def require_tender_basis(tender_requirements, tender_toc):
    """依据不足时在模型调用和目录写入前停止，不以项目描述或历史模板代替。"""
    from app.core.config import settings
    from app.core.chapter_title import normalize
    meaningful_toc = any(isinstance(c, dict) and str(c.get('title') or '').strip()
                        and (c.get('children') or normalize(c['title']) != normalize(settings.generation_root_title or ''))
                        for c in (tender_toc or {}).get('chapters') or [])
    if not str(tender_requirements or '').strip() and not meaningful_toc:
        raise ValueError('招标依据不足，未生成或替换目录。请核对招标解析原文、技术要求和评分办法后重试')


def generate_toc_titles(project_info: str, professions: list[str] | None = None,
                        tender_requirements: str = "", tender_toc: dict | None = None,
                        chapters: list[str] | None = None, domain: str = "") -> list[dict]:
    """只根据当前招标目录、技术要求和评分办法生成；chapters旧参数不再生效。"""
    require_tender_basis(tender_requirements, tender_toc)
    from app.core.config import settings
    from app.core.generation_scope import select_roots

    scope = settings.generation_root_title
    if scope:
        # 原标题含原章节编号，直接保留；其下规定的子节仍由 _tender_children 原样取回。
        specified = select_roots((tender_toc or {}).get("chapters") or [], scope)
        if len(specified) > 1:
            raise ValueError("招标目录中存在多个同名编制范围，请先核对目录")
        return [{"title": specified[0]["title"] if specified else scope}]

    user = _toc_context(project_info, professions, tender_requirements, tender_toc)
    user += "\n请生成章级骨架（只列章标题）。"
    raw = llm.chat(
        [
            {"role": "system", "content": fill_genre(TOC_TITLES_SYSTEM, domain)},
            {"role": "user", "content": fill_genre(user, domain)},
        ],
        temperature=0.3,
        max_tokens=2000,
    )
    return [c for c in _extract_json(raw).get("chapters") or []
            if isinstance(c, dict) and str(c.get("title") or "").strip()]


def _tender_children(tender_toc: dict | None, chapter_title: str) -> list[dict]:
    """招标规定目录里这一章的子节（**原样**）；没有这一章、或它没规定子节，返回空。

    先按归一化后**相等**找，找不到才用 related 的包含匹配兜底：章名之间的包含关系比子节
    之间常见得多（「八、法人代表授权书、法定代表人身份证明、被授权人身份证明」这种长名
    容易被别的章蹭上），严格的那一层先挡。
    """
    from app.core import chapter_title as ct

    target = ct.normalize(chapter_title)
    if not target:
        return []
    def flatten(nodes):
        for node in nodes:
            yield node
            yield from flatten(node.get("children") or [])

    chapters = list(flatten((tender_toc or {}).get("chapters") or []))
    hit = next((c for c in chapters if ct.normalize(c.get("title")) == target), None)
    if hit is None:
        hit = next((c for c in chapters if ct.related(c.get("title"), chapter_title)), None)
    if hit is None:
        return []
    from copy import deepcopy
    return [dict(deepcopy(k), title=str(k.get("title") or "").strip())
            for k in hit.get("children") or [] if str(k.get("title") or "").strip()]


def _ordered_children(children: list[dict], specified: list[dict], parent_title: str) -> list[dict]:
    """模型只补内容，招标已列大项的相对顺序由程序守住。"""
    from app.core.chapter_title import normalize
    from copy import deepcopy

    clean = []
    seen = {normalize(parent_title)}
    for child in children:
        name = normalize(child.get("title"))
        if not name or name in seen:
            continue
        seen.add(name)
        # 模型自行编的章节序号重排后会失效；目录层级与编号由展示/导出处理。
        clean.append(dict(child, title=name))
    wanted = [c for c in specified if normalize(c.get("title")) != normalize(parent_title)]
    by_name = {normalize(c["title"]): c for c in clean}
    ordered = []
    for item in wanted:
        name = normalize(item["title"])
        node = by_name.get(name, deepcopy(item))
        node["title"] = item["title"]
        ordered.append(node)
    names = {normalize(c["title"]) for c in ordered}
    # 额外内容保持其原位置；仅替换规定大项的槽位，不按模型的序号排序。
    it = iter(ordered)
    result = [next(it) if normalize(c["title"]) in names else c for c in clean]
    result.extend(it)
    return result


def generate_toc_children(chapter_title: str, project_info: str,
                          professions: list[str] | None = None,
                          tender_requirements: str = "",
                          tender_toc: dict | None = None,
                          skeleton: list[dict] | None = None, domain: str = "") -> list[dict]:
    """阶段二：为某一章生成子节（逐章完成后前端即可实时看到该章展开）。

    **招标规定了这一章的子节，就原样照抄，一次模型都不调。**

    这是「招标规定目录优先」在**子节层**的落实。阶段一（`generate_toc_titles`）早就立了
    这条规矩——系统提示词里写着「【最高优先级】若提供了"招标文件规定的施组目录"，必须以
    它为骨架原样保留其章」——可它只作用于**章**；子节那一层一直拿不到 `tender_toc`，
    只能照提示词「按惯例列 2~8 个」现编。

    实测后果（项目 4，2026-10-15）：九个章的子节**无一与招标规定相符**。招标文件里
    「一、响应函」只规定了一节正文，生成出四节（凭空编出「投标函/投标保证金缴纳凭证/
    廉洁承诺与保密承诺书」）；「四、技术方案」招标规定 7 节，生成 8 节（多一节"大安机场
    资源协调与工程化交付实施计划"）；「三、技术标准…对照表」6 节的**名字全被换掉**。

    招标文件的子节其实**早就抽出来存进 `project.toc_hint` 了**（抽取侧一直在写，连
    `_clean_requirements` 的下沉判据都用它），只是从没传到这一步。原样照抄还有个附带好处：
    章名与子节一起才是完整的规定目录，抽取侧把招标要求下沉到子节时才挂得上。

    `skeleton` 是**知识库骨架**（`matching.chapter_skeleton`）：同类标书里这一章实际
    用过哪些子节、各被写到多少次。招标格式平铺（本项目就是）时，子节只能靠模型现编，
    而知识库里其实躺着一份现成的、对得上评审办法的骨架——此前目录生成阶段压根拿不到
    知识页，它从来没机会参与。有了骨架就摆进 prompt 让它装配，没有则照旧自由生成。
    """
    specified = _tender_children(tender_toc, chapter_title)
    skel = skeleton or []
    # 当前招标已规定子节时直接保留，历史结构不能改变其顺序或数量。
    if specified:
        return specified
    prof = professions or []
    user = (
        f"项目信息：{project_info}\n"
        f"本项目涉及的专业：{prof if prof else '待识别'}\n"
        + (f"招标要求与评分办法：\n{tender_requirements}\n" if tender_requirements else "")
    )
    if skel:
        user += "历史子节仅供本章方法细化参考，不得为迁就历史资料增加无关章节或改变招标要求。\n"
        rules = "、".join(f"{s['name']}（{s['pages']}页）" for s in skel)
        user += f"同类标书中本章的子节（页数=该子节在知识库里被写到的次数，多者为惯例）：\n{rules}\n"
    if specified:
        user += "招标文件格式中列出的大项（必须逐一保留）：\n" + "\n".join(
            f"- {c['title']}" for c in specified) + "\n"
    user += f"\n请为章节「{chapter_title}」生成子节。"
    raw = llm.chat(
        [
            {"role": "system", "content": fill_genre(TOC_CHILDREN_SYSTEM, domain)},
            {"role": "user", "content": fill_genre(user, domain)},
        ],
        temperature=0.3,
        max_tokens=1500,
    )
    parsed = _extract_json(raw)
    if not isinstance(parsed.get('children'), list):
        raise ValueError('子目录响应缺少有效的 children 列表')
    children = [{"title": str(c["title"]).strip()}
            for c in parsed['children']
            if isinstance(c, dict) and str(c.get("title") or "").strip()]
    # 有些招标只抽出本册内部大项，没有再包一层“技术方案”。仅当所有大项
    # 都能在模型结果精确找到时，认作本层顺序依据，避免把整本商务目录带进来。
    if not specified:
        from app.core.config import settings
        from app.core.chapter_title import normalize
        top = (tender_toc or {}).get("chapters") or []
        names = {normalize(c["title"]) for c in children}
        if (normalize(chapter_title) == normalize(settings.generation_root_title)
                and top and all(normalize(c.get("title")) in names for c in top)):
            specified = top
    return _ordered_children(children, specified, chapter_title)


def generate_outline(
    chapter_title: str,
    chapter_type: str,
    project_facts: list[dict] | None = None,
    knowledge_pages: list[dict] | None = None,
    experiences: list[dict] | None = None,
    reqs_text: str = "",
    lot_brief: str = "",
    domain: str = "",
    project_id: int | None = None,
) -> dict:
    """为指定章节生成编写思路。knowledge_pages 为匹配到的历史知识页；experiences 为已确认用户经验（优先级更高）；
    reqs_text：按章相关的招标要求（评分办法/技术要求/编制要求/废标风险），作为编写依据。
    lot_brief：本标段工程构成摘要——思路只能围绕本标段实际包含的工程展开（同目录裁剪规则）。"""
    from app.services.evidence_roles import technical_facts, ROLE_RULE
    facts = json.dumps(technical_facts(project_facts or []), ensure_ascii=False)
    # 2026-09-08 决策(A)：思路生成不参考历史页 method/key_points 内容，只列出相关页标题
    pages = json.dumps(
        [{"title": p.get("title")} for p in (knowledge_pages or [])],
        ensure_ascii=False,
    )
    exp_text = "\n".join(f"- [{e.get('exp_type', '经验')}] {e.get('content', '')}" for e in (experiences or []))
    user = (
        ROLE_RULE + "\n" + f"章节标题：{chapter_title}\n"
        f"章节类型：{chapter_type}\n"
        + (f"【本标段工程构成（思路只能围绕这些工程写；未列出的工程类型不得出现）】\n{lot_brief}\n"
           if lot_brief else "")
        + (f"用户经验与偏好（必须体现到思路中）：\n{exp_text}\n" if exp_text else "")
        + (f"本章相关的招标要求（编写依据，必须响应）：\n{reqs_text}\n" if reqs_text else "")
        + f"本项目事实（可引用；缺失时按拟实施方案处理，不编造真实数据）：{facts}\n"
        f"相关的历史知识页标题（仅供提示本类章节存在，其内容不作为编写参考）：{pages}\n"
    )
    assets = []
    if project_id is not None:
        from app.services.image_planning import outline_assets
        assets = outline_assets(project_id, chapter_title, reqs_text)
    if assets:
        user += '\n【可复用图片候选（已按领域、归属过滤，含视觉描述）】\n' + json.dumps([
            {k: a.get(k) for k in ('asset_id','caption','kind','content','source_title')}
            for a in assets], ensure_ascii=False)
        user += ('\n先判断这些图片是否确实适合本章，适合才以 source=history、原始 caption 为 name、'
                 '原始 asset_id 编入 image_needs；不得将其他项目图片当作本项目实证，也不要为复用而硬选。'
                 '只有候选均不满足，才规划新的概念插图；表格与矩阵使用 need_table，不要文生图。')
    raw = llm.chat(
        [
            {"role": "system", "content": fill_genre(OUTLINE_SYSTEM, domain)},
            {"role": "user", "content": fill_genre(user, domain)},
        ],
        temperature=0.3,
    )
    from app.services.image_planning import normalize_outline
    return sanitize_obj(normalize_outline(_extract_json(raw), assets))

"""编排/规划智能体（M5-4, P0）。

职责：招标要求/评分办法提取 → 章节覆盖检查清单 → 目录生成 → 逐章编写思路。

目录装配（方案 3.2）：
- 固定骨架 + 按本项目实际专业动态装配子目录 + 招标文件定制章节；
- 目录模板库作参考输入，最终以用户确认为准。

章级骨架的来源只有三个，按优先级（2026-10-15 起，见下方注释）：招标规定的目录 >
本领域声明的候选骨架 > 模型按项目描述自编。
"""
import json
import re

from app.agents.json_util import extract_json
from app.core.domains import fill_genre
from app.services.llm import llm

# 章级骨架**不再由本模块自带**——这里原先有两张写死的表（`SKELETON_CHAPTERS` 与
# `requirement_agent.SUGGESTED_CHAPTERS`），内容是 11 个铁路土建章名。它们的注释写着
# 「跨工程类型通用」，写的时候也确实通用——因为那时候只有一个领域。2026-10-15 删掉。
#
# 章级骨架按**固定优先级**由调用方组装，再以 `chapters` 参数传进来：
#
#     ① 招标文件规定的施组目录（project.toc_hint）——评标格式红线，最高
#     ② 本领域声明的候选骨架（app/core/domains.py 的 `Domain.chapters`）
#     ③ 都没有 → 传 None，让模型按项目描述自编
#
# 第 ③ 档不是"降级"：一个刚建、没传招标、语料也还没入库的项目，项目描述里那点信息
# 比撞一张不知道哪个领域的写死章名表更靠谱。**任何一档都不许回退到别的领域**——
# 静默跨域兜底正是这套表出问题的成因。
#
# 专业词表同样按领域取，本模块也不自带（调用方以 professions 参数传入）。


def _chapters_block(chapters: list[str] | None, tender_led: bool = False) -> str:
    """章级骨架的提示词片段；没给骨架时**整段不出现**（而不是留个空标题）。

    留空标题会让模型以为"骨架是空的"，进而自己编一套又不敢说——不如干脆不提此事。

    措辞是实测调出来的（2026-10-15，项目 4 低空）：写成「参考骨架（…可增删调整）」
    时，模型**照单全收这五章之后，又把它自己那套通用施组模板补了回来**（编制依据与原则 /
    施工组织安排 / 资源配置 / 控制与重难点工程 / 管理措施），10 章里 5 章是没人要的。
    那五章在低空语料里数不出任何子节，正文只能写空话。所以这里把"参考"改成了
    "以它为准落地"，并**点名禁止**补通用模板章——补章这个动作模型自己是意识不到的。

    `tender_led`：本次有没有拿到**招标要求与评分办法**。拿到了就必须换一套说法——
    `Domain.chapters` 是**从某一份招标文件里读出来的**候选骨架（lowalt 那五支就是从
    本项目评审办法的技术评审项抄的），它是**兜底**，不是这个领域的定律。再拿"以它为准
    落地"去压一份新招标文件，就是把**上一份招标文件**的答案当成这一份的红线：
    新招标要求写的内容被这五支挤掉，表现成"目录被强制收缩成五章"。
    所以招标要求在的时候，**招标文件是唯一判据**，骨架降级为补漏参考（见
    `TOC_TITLES_SYSTEM` 第 3 条第一种情形）。
    """
    if not chapters:
        return ""
    if tender_led:
        return ("本领域同类标书的**实际章结构**（**仅供补漏**：本次有招标要求与评分办法，"
                "**以它为准**——见系统提示词第 3 条第一种情形，这一份不得覆盖或压缩"
                "招标文件点名要求的内容）："
                + " / ".join(chapters) + "\n")
    return ("参考骨架（本领域同类标书的**实际章结构**，以它为准落地：项目确实不涉及的可以删，"
            "但**不要另补本领域通用模板里的章**——补出来的章没有语料支撑，正文只能是空话）："
            + " / ".join(chapters) + "\n")

# （`TOC_SYSTEM` 与 `generate_toc` 已于 2026-10-15 删除：它们是"一次调用出章+节"的旧
#   实现，早已被下面两阶段的 `generate_toc_titles` / `generate_toc_children` 取代，
#   全仓零调用点。留着只会继续把写死的章名表散播到更多地方。）

OUTLINE_SYSTEM = """你是资深{G_FULL}编制专家，负责为指定章节生成编写思路（提纲）。

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
  "image_needs": [{"name": "图名", "purpose": "用途", "source": "mermaid|history|illustration|material", "asset_id": "history选中候选时填写原始asset_id，其他留空", "allow_generated": false, "prompt": "仅合成示意图填写画面要求"}]
}"""


def _extract_json(text: str) -> dict:
    """薄封装：统一走 json_util（带畸形修复）。"""
    return extract_json(text)


# ---------------------------------------------------------------- 目录流式（两阶段）

TOC_TITLES_SYSTEM = """你是资深{G_INTRO}编制专家，负责拟定目录的**章级骨架**。

要求：
1. 只输出章标题列表（不展开子节）；
2. 【最高优先级】若提供了"招标文件规定的{G_SHORT}目录"，必须以它为骨架原样保留其章；
3. 没有招标规定目录时，按下面三种情形之一定章：
   - **同时给了"招标要求与评分办法"和"参考骨架"**：**以招标文件为准**。招标文件里
     点名要写的内容（编制要求、技术要求、评审办法各评分项下要求的内容）在目录里
     **一项都不许少**，每项都要有承载它的章（把几项并进同一章可以，"一项都不许少"
     依然成立，只是落点是同一章）；"参考骨架"只用来补招标文件没点到的常规章，
     **不得拿它覆盖、替换或压缩招标文件点名要求的内容**；两者对不上时一律以招标文件为准；
   - **只给了"参考骨架"**：那是本领域同类标书的**实际章结构**，以它为准逐章落地；项目
     确实不涉及的可以删，但**不要另补本领域通用模板里的章**（如"编制依据""资源配置"
     "施工组织安排"这类放之四海皆准的章节）——它们在本项目语料里没有对应内容，
     正文只能写空话，评审专家一眼看得出没按本项目编；
   - **都没给**：照项目信息自己判断本项目该有哪几章——项目是什么、要交付什么、
     评审办法考什么，都在项目信息里写着；
   三种情形下**本项目里不存在的工程类型一律不要单独立章**：把别的项目才有的工程
   写进目录，等于暴露"没按本项目编制"，比少一章更糟；
4. 按本领域的编制惯例排序，只输出 JSON，不要解释。

输出 JSON：{"chapters": [{"title": "第一章标题"}]}"""

TOC_CHILDREN_SYSTEM = """你是资深{G_FULL}编制专家。为目录中指定的某一章生成子节列表。

要求：
1. 子节按该章的编制惯例排序，标题规范；
2. **数量由内容决定，不要凑数**——内容单薄的章（函件、声明、单一表格）可以只有 1 节，
   甚至不分子节；内容厚的章（施工方案、技术方案）才需要展开。原先是「按惯例列 2~8 个」，
   那是明着让模型凑，任何章名都能编出两节来；
3. 给了「同类标书中本章的子节」就**优先照它装配**——那是历史标书里实际用过的章节结构，
   括号里的页数是它在知识库里被写到的次数，**页数越多越是行业惯例**（评审专家按惯例找内容）；
4. 给了「招标文件格式中列出的大项」就**必须逐一保留**（那是招标的格式红线），可以归并、
   可以在其下再分子节，但不能漏；两者冲突时以招标要求为准；
5. 结合项目实际专业，只列本项目真正需要的内容；
6. 只输出 JSON，不要解释。

输出 JSON：{"children": [{"title": "第X节标题"}]}"""


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


def generate_toc_titles(project_info: str, professions: list[str] | None = None,
                        tender_requirements: str = "", tender_toc: dict | None = None,
                        chapters: list[str] | None = None, domain: str = "") -> list[dict]:
    """阶段一：只生成章级骨架（快，先让用户看到目录轮廓）。

    `chapters`：章级骨架候选，由调用方按上面那个三级优先级组装好再传进来
    （`compile_graph.node_generate_toc` 是唯一调用点）。**没传＝第三档**，让模型照
    项目信息自己判断该有哪几章——此处刻意不设任何写死的兜底章名表：一张不知道属于
    哪个领域的章名表，比模型看着项目描述编还差（2026-10-15，见模块顶部注释）。

    招标规定了目录时（`tender_toc`）调用方**不该再传 chapters**：招标骨架是红线，
    再给一份领域参考只会让模型在两者之间摇摆。

    有招标要求（`tender_requirements`）时，`chapters` 降级成**补漏参考**
    （`tender_led=True`）——`Domain.chapters` 是从某一份招标文件里读出来的候选骨架，
    拿它给另一份招标文件"以它为准落地"，正是"目录被强制收缩"的成因。见 `_chapters_block`。
    """
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
    user += _chapters_block(chapters, tender_led=bool((tender_requirements or "").strip()))
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
    chapters = (tender_toc or {}).get("chapters") or []
    hit = next((c for c in chapters if ct.normalize(c.get("title")) == target), None)
    if hit is None:
        hit = next((c for c in chapters if ct.related(c.get("title"), chapter_title)), None)
    if hit is None:
        return []
    return [{"title": str(k.get("title") or "").strip()}
            for k in hit.get("children") or [] if str(k.get("title") or "").strip()]


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
    # 招标真规定了子节、知识库又没给出惯例 → 照抄，一次模型都不调（最忠实也最便宜）。
    # **但知识库有骨架时不能直接照抄**：本项目的招标「附件2 响应文件格式」是平铺的
    # 九个章名，`toc_hint` 里那些子节是抽取模型自己推的，照抄等于把一次推演固化成
    # 招标规定；而知识库骨架恰恰对得上评审办法。两者都摆给模型，让它择优合并。
    if specified and not skel:
        return specified
    prof = professions or []
    user = (
        f"项目信息：{project_info}\n"
        f"本项目涉及的专业：{prof if prof else '待识别'}\n"
        + (f"招标要求与评分办法：\n{tender_requirements}\n" if tender_requirements else "")
    )
    if skel:
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
    return [{"title": str(c["title"]).strip()}
            for c in _extract_json(raw).get("children") or []
            if isinstance(c, dict) and str(c.get("title") or "").strip()]


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
    facts = json.dumps(project_facts or [], ensure_ascii=False)
    # 2026-09-08 决策(A)：思路生成不参考历史页 method/key_points 内容，只列出相关页标题
    pages = json.dumps(
        [{"title": p.get("title")} for p in (knowledge_pages or [])],
        ensure_ascii=False,
    )
    exp_text = "\n".join(f"- [{e.get('exp_type', '经验')}] {e.get('content', '')}" for e in (experiences or []))
    user = (
        f"章节标题：{chapter_title}\n"
        f"章节类型：{chapter_type}\n"
        + (f"【本标段工程构成（思路只能围绕这些工程写；未列出的工程类型不得出现）】\n{lot_brief}\n"
           if lot_brief else "")
        + (f"用户经验与偏好（必须体现到思路中）：\n{exp_text}\n" if exp_text else "")
        + (f"本章相关的招标要求（编写依据，必须响应）：\n{reqs_text}\n" if reqs_text else "")
        + f"本项目事实（可引用，无则标注待补充）：{facts}\n"
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
    return normalize_outline(_extract_json(raw), assets)

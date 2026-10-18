"""编写智能体（M5-5, P0）。

职责：逐章生成正文，流式写入编辑器（不进对话框）。

硬约束（方案 3.3）：
- 依据优先：上下文 = 章节思路 + 该章知识页 + 事实子集（按节注入）+ 同级章节信息；
- 数字必须来自全局事实表或知识页出处，无依据标 [待补充]；
- 图片只给"配什么"不给"原图"：知识页的图只取图名与用途注入，正文在需配图处放
  [图片占位：图名/用途]，不插入任何真实图片（历史图属于别的项目，与数字同理不照搬）。
  **例外是 Mermaid**（规则 9）：**内容能由本章正文自己推出来的图**（流程/层级/组织/时序/因果/
  进度/逻辑判定/对比关系/结构/分类/模式/方案/方法/规划等我方的做法与安排）由模型直接画成
  ```mermaid 代码块——它不带任何历史项目的图片与数据，是从本章正文现推的结构图，因此不受
  "不照搬"那条约束。判据全文收在 `_IMG_SOURCE_RULE` 一处，规则 5、规则 9(4)、【建议图片】段
  都插它（**别再各写一份枚举**，2026-10-16 就是这么漏图的）。
  导出时由 `app/services/docx_export.py` 调 `mermaid_render` 渲成真实图片；
- 提示词缓存（方案 3.10）：system 稳定规则 → 大而稳定上下文前置 → 任务差异放最后。
"""
import json
import re

from app.core.domains import fill_genre
from app.services.image_ownership import NOT_SUGGESTIBLE_KINDS, image_name
from app.services.llm import llm

#: 「这张图该用 Mermaid 画、还是留成 [图片占位]」的**唯一判据** —— 规则 5 与
#: 【建议图片】段（`_build_context`）插它的全文，规则 9(4) 指向它。**三处不再各写一份枚举。**
#:
#: 2026-10-16 真实翻车：三份枚举互不一致（规则 9 有「组织/因果/对比关系」，规则 5 和
#: 【建议图片】段没有），「组织架构图」这类图名就从缝里漏成 [图片占位]；而且三份都漏掉了
#: 语料里真实存在的 `服务模式图` / `应对策略图` / `标准化维度图` / `测试思维导图` /
#: `建设规划图` / `管理实施方法图`（全库 88 条结构类图名里的一批）。
#: **改成按"内容从哪来"判、枚举只当例子**，语料再长出新类也不会重演同一种漏。
#:
#: 常量里没有 `{`，所以不影响 `core.domains.fill_genre`（它是 `str.replace`，不是 `format`）。
_IMG_SOURCE_RULE = (
    "**判据是这张图的内容从哪来，不是图名里有没有某个字**："
    "内容能由本章正文自己推出来且适合用节点与连线表达的（流程、层级、组织、时序、因果、进度、逻辑判定、对比关系、"
    "结构、分类、模式、方案、方法、规划等**我方自己的做法与安排**）→ 按规则 9 画成 ```mermaid；"
    "内容必须是**外部真实存在的东西**（现场照片、实物图、界面截图、仪器仪表图、扫描件、证照、"
    "检测报告、客户提供的材料、实测数据）→ 在需配图处放 [图片占位：图名/用途]。"
    "概念场景、空间布置效果等不适合 Mermaid 的示意插图，按思路中的 illustration 需求"
    "在合适位置放 [图片占位：完整图名]，历史没有对应图时由生图服务补齐；不得冒充真实材料。"
    "**判不准就按占位**——占位人工一眼就能处理，画错的图是错信息。"
)

WRITER_SYSTEM = """你是资深{G_INTRO}编制专家，负责撰写章节正文。

硬性规则（必须严格遵守）：
1. 只能基于提供的材料撰写：本项目事实、本项目文件原文片段、工艺工法条目；
   历史知识页与【历史原文参考】可借鉴其行文、通用表述与结构（见规则7），但**项目特定数据一律不得沿用**；
2. 所有数字（工期/人数/机械/造价/工程量等）必须来自「本项目事实」或「本项目文件原文」；
   两者都找不到的数值一律标注 [待补充]，**禁止**借用历史知识页/历史原文里的数值（那是别的项目，不适用本项目）；来自项目文件原文的数字须在括号内标注出处文件名；
3. 冲突裁决：本项目文件原文 > 本项目事实表 > 历史知识页/工法（本项目文件是本项目的上层约束，指导性{G_SHORT}章节内容必须保持一致）；
   招标要求只证明“必须做到什么”，不证明“我方已经具备或已经完成”。没有本单位真实材料，
   不得宣称已有业绩、现成人员、资质证书、检测结论或“经核查零负偏离”。拟采用的做法写为计划、
   拟配置、拟执行；已有能力与业绩缺证据时明确标注 [待补充：所需真实依据]。
   对招标硬性指标可写明确响应承诺，同时说明落实路径；不得把承诺伪装成已测实绩。
   必须保持主体归属：采购方/客户联系人不得写成我方项目经理或技术负责人；
   招标文件要求提供的设备、资源、人员，不等于我方已有。归属不明时不指定实名人员。
4. 输出 Markdown 正文，段落结构清晰；标题用 ## 三级起（正文内的小节用 ###）。
   **篇幅由【篇幅要求】给定；若参考历史是短表/清单类内容，则尽量模仿其紧凑表格形式排布，禁止为凑字数加导语、备注列或无关扩写；**
5. 不输出任何解释性文字、不输出"以下是正文"之类的引导语，直接输出正文内容；
   若本章思路标注"需表格/需配图"：表格按【建议表格】给结构填；需配图处按这条判据办——
""" + _IMG_SOURCE_RULE + """
6. 不要重复其他章节已写的内容（同级章节信息已提供，注意区分边界）；
7. **历史参考：通用文字可借鉴，项目数据严禁照搬**：【历史原文参考】【历史知识页】可以借鉴其行文结构、
   专业措辞、组织顺序，其**通用性文字表述**（通用工艺描述、通用质量/安全/环保措施、通用管理要求等
   不依赖具体项目的文字）可改写甚至直接沿用；但其中一切**项目特定且可量化的内容**——项目名、标段、
   地点位置、日期工期、工程量与数量、造价、人员机械台数、对象名称、单位与指标——**全部属于历史项目，
   严禁照搬进本项目正文**——本项目一律以【本项目事实】【本项目文件原文】【本项目标段】为准；
   本项目缺依据就写 [待补充]，不得拿历史数据顶替；
8. 正文默认即为所选标段内容，**不要**在每章反复写"本标段/第X标段"之类强调；措辞自然，
   仅"编制范围/工程概况"等确需交代范围起止/对象时点名标段即可；
9. **Mermaid 关系图**：本章内容天然是**流程、层级、组织、时序、因果、时间进度或对比关系**时
   （实施流程、组织架构、交付节点、进度安排、问题闭环路径、能力矩阵等），**直接输出一个
   ```mermaid 代码块把图画出来**，不要再写成 [图片占位：…]。
   图与正文互补：图前用一两句交代它表达什么，图后可接着展开；
   **图里只放本章正文已经写到的节点与关系**，不得引入正文没有的数据。
   （1）图型只用这几种，**按内容对号入座**：`flowchart`（流程、判定分支、逻辑关系，最常用）、
   `sequenceDiagram`（交互时序）、`timeline`（里程碑）、`gantt`（**工期与进度安排**——
   正文里说"横道图""进度计划表""分阶段排期"的，就是它，别拿 flowchart 代替）、
   `mindmap`（要点分层）；
   （2）节点文字必须是**简短中文**（≤14 字），且**不得出现 ( ) [ ] { } " | 与半角逗号**——
   它们是 mermaid 的语法元字符，会让整张图解析失败；需要括注就改用「」；
   （3）一张图 6~14 个节点；宁可画两张小图，不要画一张巨图；
   sequenceDiagram 的参与者 ID 统一用 P1、P2 等，通过 as 设置中文名称，不使用 Link 等保留词作 ID；
   （4）**不要为了配图而配图**：纯叙述性段落、没有关系可画就不放图，全章 0~2 张为宜。
   但反过来同样要守：本章思路点名的配图，**只要内容属于规则 5 那条判据里"能由本章正文自己
   推出来"的一类，就必须画出来，不得留成 [图片占位：…]**；各处内容互不相同就各画一张
   （上限 2 张），不要为了省额度而把该画的留成占位——同一个坑换成占位不会变得更像"没在凑数"；
   （5）代码块里只写 mermaid 源码本身，不要写解释文字、不要在源码里写注释行。"""


def _build_context(outline: dict, facts: list[dict], knowledge: list[dict], siblings: list[str],
                   experiences: list[dict] | None = None, methods: list[dict] | None = None,
                   file_refs: list[dict] | None = None, lot_info: str = "",
                   extra_requirements: str = "", reqs: str = "",
                   lot_profile: dict | None = None,
                   target_words: int | None = None,
                   domain: str = "",
                   skip_images: dict[str, str] | None = None,
                   image_refs: list[dict] | None = None) -> str:
    """组装编写上下文（稳定上下文前置，利于命中缓存）。

    本次特别要求（对话触发生成时用户在对话里提出的侧重点）最优先；
    经验条目（用户偏好级，已确认）次之；项目文件原文片段（按章取材）排在事实之后。
    lot_profile：当前标段档案（字段由本域声明，见 `domains.lot_fields`）→ 生成"白名单"段，
    约束正文只写本标段对象。
    target_words：本章目标字数（项目「页数」设置按目标页数分摊到章的额度）；None = 不注入，
    退回看参考知识页的篇幅。**显式传参而非去查库**：同步 endpoint 跑在线程池里，
    隐式读全局状态会串号（同 docx_export 的 DocxStyle 约定）。
    domain：项目所属领域（`domains` key）。只有它决定"标段档案里哪些字段算数"，
    lot_profile 的字段名/显示名都按域取。缺省等于没给→默认域（该域若没声明标段字段，
    这一段整个不注入），不会跨域兜底。
    skip_images：`{图名: 归属章标题}`——**本章要让出的图**。知识页是按章独立匹配的，
    容器章常和它的子章匹配到同一批页，于是同一个图名进了两章的建议图片，模型各画一张，
    成稿里就是两张一样的图（用户 2026-10-16：「确实不能出现两张一样的图」）。
    归属表由 `services/image_ownership` 在开写前按目录结构一次算定（不能用运行期抢图，
    批量路径是并发 10 章，抢图结果不确定）。空/缺省 = 不做任何干预。
    """
    parts = []
    # 标段白名单放最前：这是"只能写什么"的硬边界，比其他素材优先级更高。
    # **字段名/显示名/词表一律按域取**（domains.lot_fields）——这里不该认识"里程"
    # 「构造物」「大临」里任何一个，原先那三行就是这么把铁路字段名带进每个项目的。
    if lot_profile:
        from app.services.lot_profile_service import profile_text, whitelist_terms

        seg = []
        if lot_profile.get("lot_code"):
            seg.append(f"标段：{lot_profile['lot_code']} {lot_profile.get('lot_name', '')}".strip())
        scope = profile_text(lot_profile, domain)
        if scope:
            seg.append(scope)
        terms = whitelist_terms(lot_profile, domain)
        if terms:
            seg.append("本标段清单（**正文只允许出现下列对象**）：" + "、".join(terms))
        parts.append(
            "【本标段范围（白名单，硬边界）】\n" + "\n".join(seg) + "\n"
            "**严禁**把清单之外的对象写进本标段正文（其他标段的内容尤其如此）；"
            "项目文件里出现其他标段的内容时，只能当作全线背景、不得描述成本标段的工程。"
        )
    if extra_requirements:
        parts.append("【用户本次特别要求（最高优先级，必须重点落实）】\n" + extra_requirements[:1000])
    # 稳定上下文前置
    if lot_info:
        parts.append(fill_genre(
            "【本项目标段（数据取材范围，不必每章强调）】\n"
            "整本{G_SHORT}即该标段的{G_BID}，正文默认即为本标段内容，评标方已知晓——"
            "**不要**在每章反复写\"本标段/第X标段\"之类强调，措辞自然省略（可用\"本项目/本工程\"等），"
            "仅\"编制范围/工程概况\"等确需交代范围起止或对象时才点名。标段信息主要用于取材："
            "检索本项目文件/事实时只取本标段对应范围与对象的数据。\n"
            f"{lot_info}", domain))
    if experiences:
        parts.append("【用户经验与偏好（最高优先级，必须遵守）】\n" + "\n".join(
            f"- [{e.get('exp_type', '经验')}] {e.get('content', '')}" for e in experiences
        ))
    if facts:
        # 按标段范围分组：本标段/全线共通/未判定 一组；"全线背景"（含 multi）另立一组并警示，
        # 防止把"全线控制性工程=井冈山、武功山…"这类背景当成"本标段控制性工程"照搬进正文。
        mine = [f for f in facts if f.get("scope") != "background"]
        bg = [f for f in facts if f.get("scope") == "background"]
        strip = lambda fs: [{k: v for k, v in f.items() if k != "scope"} for f in fs]  # noqa: E731
        if mine:
            parts.append("【本项目事实（必须严格遵守）】\n" + json.dumps(strip(mine), ensure_ascii=False))
        if bg:
            parts.append(
                "【全线背景资料（**不是本标段内容**，严禁写成「本标段…」）】\n"
                "以下为全线层面的信息（如全线控制性/重难点工程清单），只可用于『全线概况』类背景描述；"
                "**不得**把它们当作本标段的工程内容，也不得据此展开本标段的重难点分析：\n"
                + json.dumps(strip(bg), ensure_ascii=False)
            )
    if reqs:
        parts.append(
            "【招标要求（编写依据：评分办法/技术要求/编制要求/废标风险，必须响应，不得遗漏）】\n"
            + reqs
        )
    if file_refs:
        parts.append(fill_genre("【本项目文件原文片段（招标/指导性{G_SHORT}/答疑/勘察/策划，可引用数字须标注出处文件名）】\n", domain) + "\n".join(
            f"◆ 出处：{r.get('title', '')}\n{r.get('chunk', '')}" for r in file_refs
        ))
    # （2026-09-07 决策：知识页的 method/key_points 暂不注入正文生成，避免历史"编制方式/要点"
    #  干扰；风格参考只用下方「历史原文参考」ref_text。）
    # 历史参考（ref_text）仅作"风格/布局"示范
    exemplar = next((k for k in (knowledge or []) if (k.get("ref_text") or "").strip()), None)
    if exemplar:
        parts.append(
            "【历史原文参考：通用文字可借鉴，项目数据严禁照搬】\n"
            f"以下为历史项目《{exemplar.get('title', '')}》章节原文（全文）。可借鉴其行文风格、专业措辞、"
            f"组织顺序与**通用性文字表述**（通用工艺/措施/管理要求的措辞可改写甚至直接沿用）。"
            f"但其中一切**项目特定且可量化的内容**——项目名/标段/地点位置/日期工期/工程量与数量/造价/"
            f"人员机械台数/对象名称/单位与指标——**全部属于该历史项目，严禁照搬进本项目正文**；"
            f"本项目数据一律以【本项目事实】与【本项目文件原文】为准，缺依据就标 [待补充]。\n\n"
            f"{str(exemplar.get('ref_text') or '')}"
        )
    if outline.get("image_needs"):
        parts.append("【本章配图需求（mermaid 自绘；history 选候选；illustration 缺图可生成示意；material 待补真实材料）】\n"
                     + json.dumps(outline["image_needs"], ensure_ascii=False))
    # 本章应配的表格/图片（来自知识页详情的表格结构与图片说明，只给"配什么"，不复制历史数据）
    skip_images = skip_images or {}
    tbl_items = []
    for table in outline.get('table_needs') or []:
        if isinstance(table, dict) and table.get('name'):
            tbl_items.append(f"- 表「{table['name']}」：{table.get('purpose') or ''}；用可编辑 Markdown 表格，不得画成图片")
    img_items = []
    seen_images: set[str] = set()
    yielded: list[str] = []          # 让出去的图名（保序去重），下面单列一段告知模型
    image_sources = list(knowledge or [])
    if image_refs is not None:
        image_sources.append({"images": image_refs, "_image_source": True})
    for k in image_sources:
        for t in k.get("tables") or []:
            t_name = str(t.get("title") or t.get("name") or "").strip()
            if not t_name:
                continue
            desc = str(t.get("description") or "").strip()
            cols = t.get("columns") or []
            col_txt = "、".join(str(c.get("name") or c) for c in cols if (c.get("name") if isinstance(c, dict) else c))
            tbl_items.append(f"- 表「{t_name}」{('：' + desc) if desc else ''}{('（表头：' + col_txt + '）') if col_txt else ''}")
        if image_refs is not None and not k.get("_image_source"):
            continue
        for im in k.get("images") or []:
            # 图名走 `image_ownership.image_name`（与算归属表同一口径），别再就地写一份
            cap = image_name(im)
            if not cap:
                continue
            if not str(im.get("url") or "").strip():
                # 没有文件的"图"：模型读正文时**从描述性文字里编出来的条目**，素材里根本没有这张图
                # （实测全库 27 条、22 个图名，如「实质性佐证材料示例集」「飞行器姿态抗扰动数据曲线图」）。
                # 放进【建议图片】= 让写手照着写一个**必然落空**的 [图片占位]——2026-10-16 真实翻车：
                # 项目 4 成稿 8 处占位里 6 处指向这种空壳。见 AGENTS.md「有名字但图不存在」。
                continue
            if im.get("kind") in NOT_SUGGESTIBLE_KINDS:
                continue           # 凭证扫描件是附件不是配图素材，别让写手照着写"见附件"
            if cap in skip_images and not im.get("asset_id"):
                if cap not in yielded:
                    yielded.append(cap)
                continue
            identity = im.get("asset_id") or cap
            if identity in seen_images:
                continue
            seen_images.add(identity)
            content = str(im.get("content") or im.get("description") or "").strip()
            img_items.append(f"- 图「{cap}」{('：' + content) if content else ''}")
            if im.get("asset_id"):
                img_items[-1] += (f"（素材ID={im['asset_id']}；历史来源：{im.get('source_title', '')}）"
                                  f"\n  若采用此图，原样写：[图片占位：{cap}|素材ID={im['asset_id']}]")
    if tbl_items:
        parts.append("【本章建议使用的表格（仅列应配什么表与怎么填，不复制历史数据）】\n" + "\n".join(tbl_items[:6]))
    if img_items:
        # ⚠ 这一行是**每章都在发**的处理许可。判据**插 `_IMG_SOURCE_RULE` 的全文**，
        # 不在这里再写一份枚举：枚举漏一个类就等于给模型一条合法退路——枚举里没提
        # 「示意图」时"算法逻辑图"就名正言顺留成 [图片占位]（2026-10-16 真实翻车，#283）；
        # 三份枚举互不一致时"组织架构图"从规则 5 的缝里漏下去（同日实测）。
        parts.append(
            "【本章建议使用的图片/图示（仅列图名与用途）】\n"
            "处理方式：" + _IMG_SOURCE_RULE + "\n"
            "复用下列历史图片时，占位中的图名必须逐字照抄，保留括号和序号，"
            "例如 [图片占位：完整图名/用途]；不得自行改名、简称或合并两张图片。"
            "只有下列清单中的图片可作为已有素材引用；缺少真实素材时不得声称图片已经提供。\n"
            "带素材ID的历史图必须保留该ID，不能只写图名；候选不等于必须使用，"
            "须核对图中内容与本章需求，历史场景不得冒充本项目已经发生的事实。\n"
            + "\n".join(img_items[:6])
        )
    if yielded:
        # 单列一段、说清"为什么没有"：只把图从建议里删掉是不够的——本章正文常常
        # 自己就想画这张图（"交付批次按下图推进"），模型会照着自己画第二张一模一样的。
        parts.append(
            "【本章不要再配的图（已由别的章节配用，重复配会出现两张一样的图）】\n"
            + "\n".join(f"- 「{c}」已用于《{skip_images[c]}》" for c in yielded)
            + "\n本章不配它：既不要画，也不要用 [图片占位] 顶上（这是规则 9(4) 的唯一例外）。"
        )
    if methods:
        # 工法整篇注入（含文字/工艺流程/表格/图片），本章"怎么做"的内容据此编写。
        # ⚠ 措辞**按域取词**：两个域的成稿都叫「技术方案」，这里写死「施工方法」等于
        # 拿铁路的名词给低空项目定调——2026-10-15 修的那批提示词是同一个形状
        # （见 `core.domains.fill_genre`：提示词里不该出现任何一处写死的体裁名词）。
        parts.append(fill_genre(
            "【工艺工法（{G_SHORT}的方法素材：本章方法/工艺类内容优先据此编写，"
            "可引用其中的工序与参数）】\n", domain,
        ) + "\n\n".join(f"◆ 工法：{m.get('name')}\n{m.get('text', '')}" for m in methods))
    if siblings:
        parts.append("【同级章节（避免重复，写别的内容）】\n" + "\n".join(f"- {s}" for s in siblings))
    # 篇幅硬约束，显式放到任务最末尾（模型易忽略埋在 JSON 里的 estimate_words）。
    # 优先级：项目按目标页数分摊下来的额度 > 参考知识页的 estimate_words——
    # 前者是「全书页数」这个硬指标的分解，比单看一章的参考页更该听。
    ref_len = next((k.get("estimate_words") for k in (knowledge or []) if k.get("estimate_words")), None)
    if target_words and target_words > 0:
        if target_words < 800:  # 分摊到的小章：多为表格/清单类，同样不按字数硬撑
            len_line = (
                f"【篇幅要求】本章目标篇幅约 {target_words} 字（±15%）。"
                f"该额度由全书目标页数按章分摊而来；请把该覆盖的要点写齐全，"
                f"不要为了凑篇幅加导语、备注列或无关解释。"
            )
        else:
            len_line = (
                f"【篇幅要求】本章目标篇幅约 {target_words} 字（±15%）。"
                f"该额度由全书目标页数按章分摊而来，请尽量贴近；"
                f"宁精勿滥：严禁空泛扩写、注水或罗列冗余来凑篇幅。"
            )
    elif ref_len:
        if ref_len < 800:  # 短表/清单类：以"覆盖内容点"为目标，不按字数硬撑
            len_line = (
                f"【篇幅要求】本章参考历史页内容很短（约 {ref_len} 字，多为表格/清单类），"
                f"请**按其覆盖的要点齐全地写**，不要为了凑篇幅加导语、备注列或无关解释。"
            )
        else:
            len_line = (
                f"【篇幅要求】参考知识页预计本章约 {ref_len} 字。请把正文控制在该字数附近（约 ±30%），"
                f"宁精勿滥：参考页很短就写短，严禁空泛扩写、注水或罗列冗余来凑篇幅。"
            )
    else:
        len_line = "【篇幅要求】无明确参考篇幅，内容写完整即可，不要空泛凑字。"
    # 任务差异放最后
    # 编写思路回归正文生成（仅作"数据/来源/写法指引"——说明本章要写什么、数据从哪取、
    # 怎么组织；结构布局由历史参考示范，不把思路当目录模板照抄）
    thinking = (outline.get('thinking') or '').strip()
    kp = outline.get('key_points') or []
    if thinking or kp:
        parts.append(
            "【本章编写思路（内容指引：要写什么、数据从哪取、怎么组织；不是结构模板）】\n"
            f"{thinking}\n"
            + ("\n".join(f"- {p}" for p in kp) if kp else "")
        )
    parts.append(
        f"{len_line}\n"
        f"\n请撰写章节「{outline.get('chapter_title', '')}」的正文。"
    )
    return "\n\n".join(parts)


def fit_chapter_length(body: str, target_words: int | None, context: str, domain: str = "") -> str:
    from app.services.length_control import fit

    def rewrite(current, actual, target, tolerance):
        instruction = (
            f"请校准上一版正文篇幅：按中日韩文字和标点逐字、西文按词计数，"
            f"当前约 {actual} 字，目标 {target} 字，允许偏差 ±{tolerance:.0%}。"
            "只输出完整修订后的 Markdown 正文。保留全部要求覆盖、事实、数据与来源。"
            "压缩时删冗余表述；扩充时补充有依据的操作细节，不新增无依据数据或套话。"
            "所有 Mermaid 代码块、图片占位的完整图名、图片引用必须原样保留，"
            "表格条目不得删除，也不得用代码块包裹正文来规避字数统计。"
        )
        return llm.chat([
            {"role": "system", "content": fill_genre(WRITER_SYSTEM, domain)},
            {"role": "user", "content": context},
            {"role": "assistant", "content": current},
            {"role": "user", "content": instruction},
        ], temperature=.2, max_tokens=50000)

    return fit(body, target_words, rewrite)


def write_chapter(outline: dict, facts: list[dict] | None = None, knowledge: list[dict] | None = None, siblings: list[str] | None = None, experiences: list[dict] | None = None, methods: list[dict] | None = None, file_refs: list[dict] | None = None, lot_info: str = "", extra_requirements: str = "", reqs: str = "", lot_profile: dict | None = None, target_words: int | None = None, domain: str = "", skip_images: dict[str, str] | None = None, image_refs: list[dict] | None = None) -> str:
    """生成章节正文，返回 Markdown 文本。lot_profile=当前标段档案（白名单约束）。

    target_words=本章目标字数（篇幅计划分摊的额度），None 表示不设目标、看参考知识页。
    domain 决定 lot_profile 里哪些字段算数（见 `_build_context`）。
    """
    context = _build_context(outline, facts or [], knowledge or [], siblings or [], experiences or [], methods or [], file_refs or [], lot_info, extra_requirements, reqs=reqs, lot_profile=lot_profile, target_words=target_words, domain=domain, skip_images=skip_images, image_refs=image_refs)
    body = llm.chat(
        [
            {"role": "system", "content": fill_genre(WRITER_SYSTEM, domain)},
            {"role": "user", "content": context},
        ],
        temperature=0.4,
        max_tokens=50000,
    )
    return fit_chapter_length(body, target_words, context, domain)


def write_chapter_stream(outline: dict, facts: list[dict] | None = None, knowledge: list[dict] | None = None, siblings: list[str] | None = None, experiences: list[dict] | None = None, methods: list[dict] | None = None, file_refs: list[dict] | None = None, lot_info: str = "", reqs: str = "", lot_profile: dict | None = None, target_words: int | None = None, domain: str = "", skip_images: dict[str, str] | None = None, image_refs: list[dict] | None = None):
    """流式生成章节正文（SSE 场景用），逐段 yield 文本增量。target_words / domain 同 write_chapter。"""
    context = _build_context(outline, facts or [], knowledge or [], siblings or [], experiences or [], methods or [], file_refs or [], lot_info, reqs=reqs, lot_profile=lot_profile, target_words=target_words, domain=domain, skip_images=skip_images, image_refs=image_refs)
    yield from llm.chat_stream(
        [
            {"role": "system", "content": fill_genre(WRITER_SYSTEM, domain)},
            {"role": "user", "content": context},
        ]
    )

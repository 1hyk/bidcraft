"""招标文件整体抽取（2026-09-11 用户拍板）。

**一次 LLM 调用**抽取三类信息：标段划分 + 招标要求 + 施组目录。

背景：此前解析招标文件要串行跑 4~5 次 LLM——标段识别 1 次、招标要求 4 个专项任务
（并发 2，跑 2 轮）、施组目录 1 次。这些调用全部落在进度 20%~90% 的**无进度上报空洞**里，
界面从头到尾只显示"准备建索引并逐章抽取全局参数"，看起来像卡死；实测这段要 1~2 分钟，
且其中大头是招标要求那 4 个任务各自 8192 的输出。

用户拍板：**招标文件整体交给 LLM 抽一次即可**——1M 上下文 + prompt 缓存，又快又便宜。
输入也一并改成"整份文件"（原先是头部 + 关键词窗口采样），不再靠关键词猜信息在哪。

保留 `lot_agent` / `requirement_agent` 的专项函数：对话内按需重抽某类、以及
"重新识别标段划分"（`lot_service.extract_lots_task`）仍走它们。
"""
import json
import re

from app.agents.json_util import extract_json
from app.core.domains import fill_genre
from app.services.llm import llm

MAX_INPUT = 400_000    # 招标文件整体上限（1M 上下文，给输出留足余量）
MAX_TOKENS = 24_000    # 三类信息合计输出上限（原三段各自 8192）

EXTRACT_SYSTEM = """你是招标文件分析专家。请**一次性**从这份招标文件中抽取三类信息。

请**严格按下面一、二、三的顺序处理**：第一段的「{G_SHORT}目录」是后两段的定位基准——
先定下目录，第三段每条招标要求才有地方可挂。

=== 一、{G_SHORT}目录（toc）===
招标文件里的"目录"有两种，**先分清是哪一种，取错会毁掉整份{G_SHORT}**：

· **{G_INTRO}目录**——招标单独规定的、{G_SHORT}自己该有哪些章。
  这是要的。
· **投标文件格式**——整本投标/响应文件由哪几章组成（响应函、报价表、{G_SHORT}、
  公司简介……）。**这不是{G_SHORT}目录**，哪怕它里面有一章叫"{G_SHORT}"：
  那一章只是"投标文件里的一章"，招标并没有规定它内部该写什么。

怎么认（第一条即可定性）：
- 目录里出现 响应函 / 投标函 / 报价表 / 开标一览表 / 法定代表人身份证明 / 授权书 /
  营业执照 / 资质证书 / 业绩 / 财务报告 / 投标保证金 / 廉洁·信用承诺书 这类
  **资信商务件** → 这是**投标文件格式**，是投标文件的组成部分清单，不是{G_SHORT}目录；
- **唯一的例外**：这种格式里把"{G_FULL}"**单独列成一支**（通常带附表/总平面布置图这类
  本册特有的子节）→ 只取**那一支**，其余章一概不要；
- 除此之外，判不准、或格式里没有单独成支的{G_FULL} → **found=false**。
  **found=false 不是失败**：生成侧有知识库骨架兜底（同类标书里实际用过的章节结构），
  比硬塞一份投标文件格式清单准得多。宁缺勿滥。

确实取到{G_SHORT}目录时：
- 原样提取为章→节两级结构，保持原文章节名称与顺序，不要自行增删
- 优先取"招标文件明确规定的{G_SHORT}目录"；确认没有规定目录时，若文中出现**{G_SHORT}**的
  示例目录/提纲，也可提取，并在 note 中说明是示例（示例也必须是{G_SHORT}的示例，
  不能拿投标文件格式来充数）
- 只有一级章的，children 给空数组
- note 里写清楚是哪一类、依据原文哪一段

{LOTS_SECTION}
=== 三、招标要求（requirements）===
{EXISTING_TOC}抽取四类，每类都要尽力找全（宁多勿漏，后续有筛选环节）：
1. category="评分办法"：**只抽与{G_FULL}编制直接相关的技术评分项**（评分项名称、分值、
   评分标准要点；表格形式的评分表逐行拆成条目）。业绩、资格、资质、商务、报价/价格、
   财务、信用、联合体等**商务评审项一律不抽**，即使它们和技术评分项写在同一张评分表里
2. category="技术要求"：技术标准、主要技术参数、验收标准等级、工期要求
3. category="编制要求"：{G_SHORT}编制格式要求（暗标、页数、字体、装订、盖章、章节要求）
4. category="废标风险"：否决投标/无效标/废标条款、实质性偏差条款
每条字段：title（名称）、content（内容要点，保留专业术语与关键数字，不编造）、
weight（分值，纯数字，无则 null）、source_location（章节名/条款号/表名）、
以及 **suggested_chapter —— 该要求该落到哪一章**（后面是按章名把要求注入对应章节正文的，
写成别的词就挂不上，所以这一条要格外当心）：
- 上面给了【本项目已有的规定目录】的，**优先从那份清单里原样照抄**（一个字都不要改）。
  它比本次抽出的更可信：**即使本次没能抽出目录，也照那份填**，不要留空、不要自造近义词；
- 没有那份清单、且本次目录 found=true 时：原样照抄本次目录里的章名或子节名；
{FALLBACK_CHAPTERS}
- 确实无从归章时给空字符串，不要硬凑。
**不抽**：业绩/案例、资格/资质、商务/价格评分、财务/信用、联合体、报价软件要求、
平台操作，以及与{G_SHORT}编写无关的程序性条款。

只输出 JSON，不要任何解释文字与代码围栏（注意 toc **排在最前**，因为它是后两段的定位基准）：
{"toc": {"found": true, "note": "", "chapters": [{"title": "", "children": [{"title": ""}]}]},
 {LOTS_SHAPE}
 "requirements": [{"category": "", "title": "", "content": "", "weight": null,
                   "suggested_chapter": "", "source_location": ""}]}"""


# 「二、标段划分」这一段**按领域二选一**：本域声明了标段档案字段才有上面那一版。
#
# 2026-10-15：原先这一段是写死在 `EXTRACT_SYSTEM` 里的，连例子都是铁路标段代码
# （CGJXZQ-3 / HSZQ-4）、范围口径写「里程 / 主要工程内容」。而这段随**每一次上传招标
# 文件**进提示词（`fact_service` 里招标文件是整体抽一次的），于是低空项目也会被问
# 「标段划分」，被例子带着去原文里找「里程」、把「包括」读成标包。
# 与 `file_index` 的 C) 标段归属段同一形状：**段的存废由域决定，不由"原文里像不像"决定**。
LOTS_SECTION = """=== 二、标段划分（lots）===
找"标段划分/标段设置/标包划分/合同段划分"等条款：
- 整个项目只有一个合同段、未做划分 → has_lots=false，lots=[]
- 有划分 → 逐个标段抽取：
  · lot_code：标段编号（如 <标段代码>、SG-1），无编号时用标段名
  · lot_name：标段名称
  · price_limit：最高投标限价（万元，纯数字，未知给 null）
  · scope：标段范围概述（范围起止 / 主要工程内容，100 字内）

"""

NO_LOTS_SECTION = """=== 二、标段划分（lots）===
本项目所属领域**没有「标段」这一层次**（一个项目、一个交付标的）。
一律返回 has_lots=false、lots=[]：**不要**去原文里找"标段划分/标包划分/合同段划分"，
也不要按合同段自行切分。项目文件里出现的"包"若只是"包括""分包给某公司"之类，
不是标包划分。

"""

LOTS_SHAPE_ON = ('"has_lots": true,\n'
                 ' "lots": [{"lot_code": "", "lot_name": "", "price_limit": null, "scope": ""}],')
LOTS_SHAPE_OFF = '"has_lots": false,\n "lots": [],'


def _extract_json(text: str) -> dict:
    """薄封装：统一走 json_util（带畸形修复）。"""
    return extract_json(text)


def _clean_lots(items) -> list[dict]:
    """标段的规则校验：编号去重、空壳剔除（与 lot_agent 同一套规则）。"""
    lots, seen = [], set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("lot_code") or "").strip()
        name = str(item.get("lot_name") or "").strip()
        if not code and not name:
            continue
        key = code or name
        if key in seen:
            continue
        seen.add(key)
        price = item.get("price_limit")
        try:
            price = float(price) if price not in (None, "", "null") else None
        except (TypeError, ValueError):
            price = None
        lots.append({
            "lot_code": code or name,
            "lot_name": name,
            "price_limit": price,
            "scope": str(item.get("scope") or "")[:500],
        })
    return lots


def _clean_requirements(items, toc: dict | None = None) -> list[dict]:
    """招标要求规整 + 去重（复用 requirement_agent 的 _sanitize/_dedup，保证与旧路径同构）。

    `toc` 要传**合并后的目录树**（本次抽出的 + 项目已有的，见 `extract_tender_all`），
    它同时当两件事用：

    1. **对齐候选**：模型可能少写个「四、」、或只写「技术方案」而目录里是「四、技术方案」，
       要对齐到**原样写法**，消费侧（requirement_service 按章取要求）才能直接比中。
       对不上任何真实章名的泛称**清空**而不是留着——「施工方案」这种在消费侧既匹配不上、
       又被当成有效定位，不如空着走类别兜底。

    2. **下沉依据**：`chapter_title.descend`。模型抽要求时爱填顶层章名——实测项目 4
       8 条技术要求全填成「四、技术方案」，而目录里明明有「第三节 监视能力全量验证技术方案」
       这种一字不差的子节；不下沉的话父章吃撑，底下各子节写正文时一条要求都拿不到。

    为什么要传合并树：本项目上次抽出的目录往往已在库里、而实际章节树就是照它生成的——
    这次模型若把目录抽窄了（实测项目 4 从 9 章缩成「四、技术方案」1 章），只按窄目录对齐，
    要求就全挂在真实树里不存在的名字上；合并后不仅有真章名可挂，**子节层级也才在**。
    """
    from app.agents.requirement_agent import TASKS, _dedup, _sanitize
    from app.core import chapter_title
    from app.services.requirement_matrix import response_scope

    valid = {t["category"] for t in TASKS}   # 评分办法/技术要求/编制要求/废标风险
    candidates = chapter_title.toc_titles(toc) if (toc or {}).get("found") else []
    out, aligned, descended, cleared = [], 0, 0, 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "").strip()
        if cat not in valid:
            continue          # 类别不认识的一律丢弃（避免脏数据进覆盖检查清单）
        r = _sanitize(item, cat)
        if not r:
            continue
        # 模型仍可能把商务评审表里的业绩/价格项写成“评分办法”；这里按内容兜底剔除，
        # 不让它们进入技术目录和逐章正文。
        if cat == "评分办法" and response_scope(r) != "technical":
            continue
        if candidates:
            raw = str(r.get("suggested_chapter") or "")
            hit = chapter_title.align(raw, candidates)
            if hit:
                if hit != raw:
                    aligned += 1
                deep = chapter_title.descend(r["title"], hit, toc)
                if deep != hit:
                    descended += 1
                r["suggested_chapter"] = deep
            else:
                if raw.strip():
                    cleared += 1
                r["suggested_chapter"] = ""
        out.append(r)
    if candidates:
        print(f"[招标抽取] 章名对齐：候选 {len(candidates)} 个章名，改写 {aligned} 条、"
              f"下沉到子节 {descended} 条、清空对不上的 {cleared} 条")
    return _dedup(out)


def _clean_toc(data) -> dict:
    """本册目录规整（与 `requirement_agent.extract_tender_toc` 的产出同构）。"""
    if not isinstance(data, dict):
        return {"found": False, "note": "", "chapters": []}
    chapters = []
    for ch in data.get("chapters") or []:
        if not isinstance(ch, dict) or not str(ch.get("title") or "").strip():
            continue
        children = [
            {"title": str(c["title"]).strip()}
            for c in (ch.get("children") or [])
            if isinstance(c, dict) and str(c.get("title") or "").strip()
        ]
        chapters.append({"title": str(ch["title"]).strip(), "children": children})
    found = bool(data.get("found")) and bool(chapters)
    return {
        "found": found,
        "note": str(data.get("note") or "")[:200],
        "chapters": chapters if found else [],
    }


def _read_toc_hint(project_id: int | None) -> dict:
    """项目里已有的规定目录（project.toc_hint），**带层级**返回。

    要层级不要平铺：它既是 `_clean_requirements` 的对齐候选，也是 `descend` 下沉时的
    子树依据——「四、技术方案」底下有哪几个子节，平铺的章名清单里看不出来。

    读失败一律当"没有"——少一份候选不影响主流程，不能因此让整次抽取失败。
    """
    empty = {"found": False, "note": "", "chapters": []}
    if not project_id:
        return empty
    try:
        from app.core.database import query_one

        row = query_one("SELECT toc_hint FROM project WHERE id = %s", (project_id,))
        hint = (row or {}).get("toc_hint")
        if isinstance(hint, str):
            hint = json.loads(hint)
        if not isinstance(hint, dict) or not hint.get("chapters"):
            return empty
        return hint
    except Exception as e:  # noqa: BLE001 补充候选读不到不影响抽取
        print(f"[招标抽取] 读已有目录失败（不影响主流程）: {e}")
        return empty


EXISTING_TOC_BLOCK = """【本项目已有的规定目录】（历次从同一份招标文件抽出、已落库，实际章节树就是照它生成的）
%s
suggested_chapter **优先从上面这份清单里原样照抄**——它比本次抽出的更可信。

"""


def _fallback_hint(chapters: str) -> str:
    """「两份章名清单都没有时，`suggested_chapter` 怎么填」这一行的措辞。

    有候选词表（本领域声明了章骨架）就照它选；**没有就让模型按性质自造泛称**，
    而不是退回别的领域的章名——那正是 2026-10-15 那个 bug：低空项目拿到的
    「施工方案」挂不上任何真实章节，一路退到类别兜底。

    空词表时这一行也必须在：它后面紧跟着「确实无从归章时给空字符串」，
    抽掉会让模型以为"没有要求"而随便编。
    """
    if chapters:
        return f"- 两份都没有时：才按性质归到泛称里（从：{chapters} 中选最贴切的）；"
    return "- 两份都没有时：按性质自造一个泛称章名（本项目还没有可参照的章名清单）；"


def _system_prompt(existing: list[str], chapters: str = "", expect_lots: bool = True,
                   domain: str = "") -> str:
    """拼系统提示词：占位符用 replace 而不是 %/format（提示词里有 {"toc": ...} 这类
    大括号，format 会把它当占位符炸掉）。

    `chapters`：该领域的兜底章名串，由调用方按项目所属领域取（见
    `requirement_agent.suggested_chapters`）。**不在这里给默认词表**——默认词表就是
    跨域污染，缺省只能是不给。

    `expect_lots`：本域有没有「标段」这一层次，决定「二、标段划分」那一段的措辞
    （见 `LOTS_SECTION` / `NO_LOTS_SECTION`）。默认 True 是**为了让"没显式说"的调用方
    保持原行为**；真正走投标流程的调用点（`extract_tender_all`）一律显式传。

    `domain`：提示词里的**体裁名词**（施组/技术标/技术方案…）按它填——见 `DocGenre`。
    默认空串＝没给，走默认域；调用点一律显式传项目所属领域。
    """
    block = ""
    if existing:
        names = " / ".join(existing)
        block = EXISTING_TOC_BLOCK % names
    return (fill_genre(EXTRACT_SYSTEM, domain)
            .replace("{EXISTING_TOC}", block)
            .replace("{FALLBACK_CHAPTERS}", _fallback_hint(chapters))
            .replace("{LOTS_SECTION}", LOTS_SECTION if expect_lots else NO_LOTS_SECTION)
            .replace("{LOTS_SHAPE}", LOTS_SHAPE_ON if expect_lots else LOTS_SHAPE_OFF))


def merge_toc(old: dict | None, new: dict | None) -> dict:
    """把新抽出的规定目录并进已有的，**已有章名一个都不丢**。

    为什么不能直接覆盖：模型每次抽出的目录范围会飘——实测项目 4 上一轮抽出附件2
    「响应文件格式」的 9 章，这一轮却收窄成「四、技术方案」1 章。而实际章节树是照
    上一轮那 9 章生成的，一旦覆盖，`toc_hint` 就和真实树对不上，之后「重新生成目录」
    会把目录缩掉一大截。目录是**招标文件的规定**，属于"找到就留着"的信息，
    不因模型某次看得窄就作废。

    合并规则：以已有章为主、保序；新章若与已有章指同一章（related）则并入其子节，
    否则追加。新目录 found=false 时原样返回旧的。
    """
    from app.core import chapter_title

    def kids(ch) -> list[dict]:
        return [{"title": str(c.get("title") or "").strip()}
                for c in ch.get("children") or [] if str(c.get("title") or "").strip()]

    if not isinstance(old, dict) or not old.get("found") or not old.get("chapters"):
        return new or {"found": False, "note": "", "chapters": []}
    if not isinstance(new, dict) or not new.get("found") or not new.get("chapters"):
        return old

    chapters = [{"title": str(c["title"]).strip(), "children": kids(c)}
                for c in old["chapters"] if str(c.get("title") or "").strip()]
    added = merged = 0
    for ch in new["chapters"]:
        title = str(ch.get("title") or "").strip()
        if not title:
            continue
        hit = next((c for c in chapters if chapter_title.related(c["title"], title)), None)
        if hit is None:
            chapters.append({"title": title, "children": kids(ch)})
            added += 1
            continue
        have = {c["title"] for c in hit["children"]}
        for k in kids(ch):
            if k["title"] not in have:
                hit["children"].append(k)
                merged += 1
    print(f"[招标抽取] 目录合并：保留已有 {len(old['chapters'])} 章，"
          f"新增 {added} 章、并入子节 {merged} 个")
    return {"found": True,
            "note": (new.get("note") or old.get("note") or "")[:200],
            "chapters": chapters}


def extract_tender_all(markdown: str, doc_name: str = "", project_id: int | None = None) -> dict:
    """一次调用抽取招标文件的三类信息。

    返回 {"has_lots": bool, "lots": [...], "requirements": [...], "toc": {...}}。
    输入是**整份文件**（不做关键词窗口采样）——信息可能出现在任何位置，让模型自己找。

    `project_id` 只用来读已有目录（project.toc_hint）——它既补对齐候选，也是下沉时的子树依据，
    见 `_clean_requirements`。
    """
    from app.agents.requirement_agent import suggested_chapters
    from app.core import chapter_title
    from app.core.domains import domain_of_project, fill_genre, lot_fields_of

    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown or "")   # 去图片引用噪音
    body = text[:MAX_INPUT]
    hint = _read_toc_hint(project_id)
    existing = chapter_title.toc_titles(hint)
    # 兜底章名按**项目所属领域**取。project_id 缺失时（单文件试抽、对话里按需重抽）
    # 刻意**不给**：`domain_of_project(None)` 会回退默认域，那等于把默认域的章名
    # 塞给一个不知道属于哪个领域的文件——2026-10-15 那个 bug 的同一个形状。
    fallback = suggested_chapters(domain_of_project(project_id)) if project_id else ""
    # 本域有没有「标段」这一层次 → 决定提示词里拼哪一版「二、标段划分」。
    # 这里**允许** project_id 缺失时退到默认域（与上面 fallback 的谨慎相反）：
    # 那两种情况的风险不对称——把默认域的章名塞给未知文件会让模型照抄错章名，
    # 而默认域（当前是低空）在标段这一段上只会让它"别找标段"，少问一段而已。
    expect_lots = bool(lot_fields_of(domain_of_project(project_id)))
    # 体裁名词同 `expect_lots` 的口径：**允许** project_id 缺失时退到默认域。
    # 它不同于 `fallback`——章名一旦给错，模型会照着抄出别的领域的章；而体裁词只影响
    # 提示词怎么称呼这份文件，退到默认域最多是措辞不合口味，且**必须有值**：
    # 空串会让提示词里留下未替换的占位符。
    domain = domain_of_project(project_id)
    print(f"[招标抽取] 输入 {len(body)} 字（整份文件）"
          + (f"，已有规定目录 {len(existing)} 个章名（写进提示词）" if existing else "")
          + ("" if expect_lots else "，本域无标段（提示词不拼标段段）"))
    raw = llm.chat(
        [
            {"role": "system", "content": _system_prompt(existing, fallback, expect_lots, domain)},
            {"role": "user", "content": f"文档：{doc_name}\n\n【招标文件原文】\n{body}"},
        ],
        temperature=0.1,
        max_tokens=MAX_TOKENS,
    )
    data = _extract_json(raw)

    # 本域声明没有标段 → **强制清空**，不因为模型多抽了就落库。
    # 领域声明是权威（见 `Domain.lot_fields`）：低空那份采购文件实测全文「标段」0 次、
    # 所有「包」都是「包括」，模型硬找出几个"标段"来只会污染 lot 表与后续的归属判定。
    lots = _clean_lots(data.get("lots")) if expect_lots else []
    # 目录先规整，再和项目已有的目录**合并成一棵树**：本次抽出的 + 已有的。
    # 树同时供 _clean_requirements 做两件事——对齐候选、以及把挂在父章上的要求下沉到子节。
    # 用合并树而不是只看本次：实际章节树常是照已有目录生成的（见 _clean_requirements）。
    toc = _clean_toc(data.get("toc"))
    tree = merge_toc(hint, toc)
    reqs = _clean_requirements(data.get("requirements"), tree)
    has_lots = bool(data.get("has_lots")) and bool(lots)
    print(f"[招标抽取] 标段 {len(lots)} 个、招标要求 {len(reqs)} 条、"
          f"本册目录 {'有' if toc['found'] else '无'}")
    return {"has_lots": has_lots, "lots": lots if has_lots else [],
            "requirements": reqs, "toc": toc}

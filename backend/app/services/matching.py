"""知识页匹配服务：目录节点 → 可参考知识页（三级漏斗）。

**候选池有两道墙，缺一不可**（`load_page_summaries`）：

  ① `domain` —— 领域墙（2026-10-15）：低空项目看不到铁路知识页；
  ② `scope`  —— 归属墙（2026-10-16）：领域墙挡不住**同域的另一个项目**。实测：一份
     **别的项目**的投标文件传进 `lowalt` 池，池子 1156 → 1353 页，生成正文时
     `match_pages` 就可能把那份标书的写法引进来。文档的 `scope` 分三态
     （domain=领域通用 / project=仅某项目 / off=不参与检索），见 `_scope_sql`。

章节类型字典废弃后（用户决策），知识页以「全路径标题」标识，匹配改为：

  第一级：粗分类/专业标签过滤 —— 目录节点路径中出现的专业词（路基/桥梁/隧道…）
          与知识页 tags 求交集，缩小候选池；节点无专业语境时用全池（不过滤）；
  第二级：标题相似度 —— 叶子标题去编号前缀后做字符 bigram Dice 系数，
          全路径逐级命中加分；≥ SIM_HIGH 直接命中；
  第三级：模糊带 [SIM_LOW, SIM_HIGH) 的候选交 LLM 按「标题 + usage」终判
          （方案 3.10 第 5 条：由 AI 按条目使用方式判断是否引用），
          每章最多一次 LLM 调用，无模糊候选则不调。

产出供两处使用：编排流程（生成思路时注入 refs，正文生成直接取用）、
对话智能体 search_knowledge_pages 工具。
"""
import json
import re

from app.agents.json_util import extract_json
from app.core.database import query
from app.core.domains import tags_of
from app.services.llm import llm
from app.services.knowledge_applicability import classify, applicable

SIM_HIGH = 0.80   # 直接命中
SIM_LOW = 0.35    # 低于此值不进候选
TOP_K = 10        # 每章最多引用知识页数

# 编号前缀：「2.3 xx」「1、xx」「第X章 xx」
_NUM_PREFIX = re.compile(r"^\d+(?:\.\d+)*\s*[、．.]?\s*")
_CN_PREFIX = re.compile(r"^第[一二三四五六七八九十百零]+[章节篇]\s*[、．.]?\s*")
# 编号级章节标题（5.2 / 5.2.2 / 2.3.1 …）：仅这类"真章节标题"才参与路径级命中计分，
# 伪叶标题（⑴布局 / ⑵实施方案）不带编号，不参与，防大范围误中。
_NUM_HEAD = re.compile(r"^\d+(?:\.\d+)*\s*")

# 第一级过滤用的专业标签词表按领域取（app/core/domains.py）：
# 低空项目只撞低空词，铁路项目只撞铁路词，跨域互不干扰。

# 2026-10-15：这个判断本身与领域无关（比的是"章节主题 vs 知识页主题"），
# 所以不改挂 `Domain`；但原来举的例子是铁路的（「桥梁下部结构施工 ↔ 墩身施工工艺」），
# 而本函数对**每一个待写章节**都会跑，低空项目也会读到"桥梁/墩身"。
# 改成中性例子：例子只示范"相邻主题"这个关系，不该顺带告诉模型这是什么行业的标。
JUDGE_SYSTEM = """你是技术文件编制专家。给定一个待编写章节（含层级路径）和若干候选历史知识页，
判断哪些知识页对该章节的编写有实际参考价值（写法/结构/表格可借鉴）。

判断标准：
- 章节主题相同或相近（如"进度计划" ↔ "进度安排"）；
- 或属于同一专业场景的相邻主题（如"实施流程" ↔ "实施步骤"）；
- 主题明显无关的剔除，宁缺毋滥。

只输出 JSON 数组（候选编号），不要解释、不要代码围栏：
[0, 2]"""


def normalize_title(title: str) -> str:
    """标题规范化：去编号前缀与空白，用于精准匹配与相似度计算。"""
    t = (title or "").strip()
    t = _CN_PREFIX.sub("", t)
    t = _NUM_PREFIX.sub("", t)
    return re.sub(r"\s+", "", t)


def leaf_of(full_path: str) -> str:
    """全路径标题取叶子：'工程概况 > 2.3 主要工程内容' → '2.3 主要工程内容'。"""
    return (full_path or "").split(" > ")[-1].strip()


def _bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _dice(a: str, b: str) -> float:
    """bigram Dice 系数，中文短标题相似度够用且零依赖。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    A, B = _bigrams(a), _bigrams(b)
    inter = len(A & B)
    return 2 * inter / (len(A) + len(B)) if (A or B) else 0.0


def _path_professions(path_titles: list[str], tags: tuple[str, ...] | list[str]) -> set[str]:
    """目录节点路径里出现的专业标签词（词表按领域传入，见 app/core/domains.py）。"""
    joined = " ".join(path_titles)
    return {t for t in tags if t in joined}


# ---------------------------------------------------------------- 第一级过滤的自我修正
#: 单个标签覆盖到候选池这个比例以上，就不再拿它当过滤依据。
#:
#: 第一级过滤的全部作用是**缩小**候选池（路径里撞到的专业词 ∩ 知识页 tags）。可一个标签
#: 若覆盖了池子里超过一半的页，这个交集就等于没缩——覆盖率为 f 的标签最多把池子缩到 f 倍。
#: 这种词留在词表里，只会让第一级形同虚设：实测 lowalt 域 1156 页里「实飞测试」覆盖
#: 91.1%，凡是路径里只撞到它一个词的章节，"过滤"与不过滤完全等价。
#:
#: 0.5 取自实测分布里最大的那道断崖（实飞测试 91.1% ↔ 数据治理 48.4%，中间空了 42.7 个
#: 百分点；下一道断崖在 36.9% ↔ 17.6% 之间）。跑 tests/verify_tag_filter.py 可复现。
#:
#: **这是规则，不是名单**：词表里一个词都没删，只是第一级不再信它。以后新加的领域、
#: 后来才泛滥的词，同一套规则照样挑得出来——不必指望谁记得回来手删。手删是改数据：
#: 改完这一份数据，机制还是原来那个，下次换份语料照样塌。
GENERIC_TAG_RATIO = 0.5

#: 池子小于这个数就不判——小样本里 3/5 也能算出 60%，那是噪音不是信号。
GENERIC_TAG_MIN_POOL = 30


def filter_pool(items: list[dict], path_titles: list[str], tags,
                key: str = "tags") -> list[dict]:
    """第一级过滤：路径里撞到的专业词 ∩ 各条的标签，缩小候选池。

    两种情况下原池返回，都是**故意**的：
    - 路径里一个专业词都没撞到（没有专业语境，第一级无从过滤）；
    - 剔掉泛滥词后一个都不剩——那说明这个节点的专业线索本来就只有泛滥词，
      它没有可用的语境，与其假装过滤，不如老实交给第二级按标题挑。

    另：撞到的词在池子里一条都没命中时也退回全池——标签打得稀疏是常态，不该因此断供。
    """
    profs = _path_professions(path_titles, tags) - generic_tags(items, tags, key=key)
    if not profs:
        return items
    pool = [it for it in items if profs & set(it.get(key) or [])]
    return pool or items


def generic_tags(items: list[dict], tags, key: str = "tags") -> set[str]:
    """候选池里已经泛滥、失去区分度的标签词（`key` 指定标签字段名）。

    返回 `tags` 里覆盖率达到 GENERIC_TAG_RATIO 的那些；池子小于 GENERIC_TAG_MIN_POOL
    则一个都不返回。纯函数、不查库——池子由调用方给，所以它算的就是**即将被过滤的那个池子**，
    换项目换语料自动跟着变。
    """
    if len(items) < GENERIC_TAG_MIN_POOL:
        return set()
    vocab = set(tags)
    counts: dict[str, int] = {}
    for it in items:
        for t in set(it.get(key) or []):
            if t in vocab:
                counts[t] = counts.get(t, 0) + 1
    n = len(items)
    return {t for t, c in counts.items() if c / n >= GENERIC_TAG_RATIO}


#: 泛滥词只报一次（按 域 + 池子规模）。`load_page_summaries` 在逐章循环里被反复调用，
#: 不挡一下同一次编译会刷 56 行一模一样的日志，看着像 bug。
_REPORTED_GENERIC: set[tuple[str, int]] = set()


def _report_generic(domain: str, pages: list[dict]) -> None:
    """把「这次有哪些词被判为泛滥」报到日志里——规则得让人看得见，否则和没修一样。"""
    gen = generic_tags(pages, tags_of(domain))
    if not gen:
        return
    stamp = (domain, len(pages))
    if stamp in _REPORTED_GENERIC:
        return
    _REPORTED_GENERIC.add(stamp)
    print(f"[匹配] 领域 {domain}：{len(pages)} 页里 {'、'.join(sorted(gen))} "
          f"覆盖率已达 {GENERIC_TAG_RATIO:.0%}，第一级不再拿它们当过滤依据")


#: 知识库文档的「归属」三态（`knowledge_doc.scope`，2026-10-16 新增）。
#: 这是**第二道墙**：领域墙（domain）挡的是别的行业，这道挡的是同域别的项目。
SCOPE_DOMAIN = "domain"    # 领域通用：同域任何项目都能参考（默认，也是加这道墙之前的行为）
SCOPE_PROJECT = "project"  # 只对 `knowledge_doc.project_id` 那个项目可见
SCOPE_OFF = "off"          # 不参与检索（只留档，知识库页面照样看得到）


def _scope_sql(project_id: int | None) -> tuple[str, tuple]:
    """归属过滤的 SQL 片段与参数（拼进 WHERE）。

    **没给项目上下文时只取「领域通用」**——宁可少取，不可串味：调用方漏传
    `project_id` 的后果是这一章少参考几页，而不是把别的项目的标书引进来。
    反过来的默认（漏传就等于全放开）会让这道墙形同虚设，且出错时没有任何症状。

    `off` 在任何情形下都不进池——它是用户显式关掉的那一类。
    """
    if project_id:
        return (f"(COALESCE(d.scope, '{SCOPE_DOMAIN}') = %s"
                f" OR (d.scope = %s AND d.project_id = %s))",
                (SCOPE_DOMAIN, SCOPE_PROJECT, project_id))
    return (f"COALESCE(d.scope, '{SCOPE_DOMAIN}') = %s", (SCOPE_DOMAIN,))


def load_page_summaries(domain: str | None = None,
                        project_id: int | None = None) -> list[dict]:
    """读某领域（且对本项目可见）的知识页摘要（匹配用，不读大字段）。

    domain 为空则读全库——只用于盘点/调试。正常匹配务必传领域，否则各域知识页会
    混进同一个候选池（正是本次领域隔离要消除的串味）。

    project_id 决定第二道墙怎么收（见 `_scope_sql`）：给了就「领域通用 + 本项目专用」，
    没给就只取「领域通用」。

    **为什么走 JOIN 而不是把 scope/project_id 冗余到 knowledge_page**：冗余就得在
    「改归属」时同步两份，而归属是**会改的**（用户把一份传错的标书改成 off 是本功能
    的主要用法）——同步一旦漏掉，改完不生效，看起来像功能没做。这张表 1353 行、
    `doc_id` 有索引，JOIN 的代价可以忽略。用 LEFT JOIN + COALESCE 而不是 INNER JOIN：
    孤儿页（文档删了、页还在）当领域通用处理，不会因为一道 JOIN 整页消失。

    返回 [{id, title, leaf, tags, category, usage, method_brief}]
    """
    where, params = ["p.status != 'deprecated'"], []
    if domain:
        where.append("p.domain = %s")
        params.append(domain)
    scope_sql, scope_params = _scope_sql(project_id)
    where.append(scope_sql)
    params += list(scope_params)
    rows = query(
        "SELECT p.id, p.title, p.tags, p.content FROM knowledge_page p"
        " LEFT JOIN knowledge_doc d ON d.id = p.doc_id"
        " WHERE " + " AND ".join(where),
        tuple(params),
    )
    pages = []
    for r in rows:
        tags = r.get("tags")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (TypeError, ValueError):
                tags = []
        content = r.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                content = {}
        content = content or {}
        pages.append({
            **classify(r["title"] or "", content),
            "id": r["id"],
            "title": r["title"] or "",
            "leaf": leaf_of(r["title"] or ""),
            "tags": tags or [],
            "category": content.get("category", ""),
            "usage": content.get("usage", ""),
            "method_brief": (content.get("method") or "")[:200],
        })
    if domain:
        _report_generic(domain, pages)
    return pages


#: 知识库骨架最多回多少个子节。多了把 prompt 淹掉，也没人真按三十条去装配。
SKELETON_MAX = 12


def chapter_skeleton(chapter_title: str, pages: list[dict] | None = None,
                     domain: str | None = None, limit: int = SKELETON_MAX,
                     project_id: int | None = None) -> list[dict]:
    """知识库里这一章**实际有哪些子节**——从同类标书的章节目录里数出来的。

    返回 [{"name": 子节名（已去序号）, "pages": 页数}]，按页数降序。

    **为什么需要它**：目录的「节」此前只有两个来源——招标文件，和模型自由发挥。
    本项目的招标「附件2 响应文件格式」是**平铺的九个章名、一个子节都没规定**，于是
    子节全由模型现编。而知识库里躺着 1139 页技术方案的参考（路径形如
    「四、技术方案 > 第一章 工程化交付方案 > 第一节 …」），评审办法要的四份方案
    逐条都在里面——**此前没有任何一处代码读过它**（本项目实测：以「四、技术方案」
    开头的知识页 1139 条，而目录生成阶段根本拿不到知识页）。

    做法：每页路径按 " > " 切段，找到与本章同名的那一段，取**紧邻的下一段**；段名
    去掉序号前缀后归并计数（「第一章 工程化交付方案」与「2、工程化交付方案」算同一
    节——两批标书用了两套编号，内容是一回事）。同名段出现了多次也只取最近的那一次，
    避免把孙辈当成子节。同类标书里写得越多的子节，越说明它是行业惯例。

    **这是从语料里数出来的规则，不是词表**：换一批知识页、换一个领域，骨架自然跟着变；
    没有骨架的章返回空列表，调用方照旧走原来的自由生成。
    """
    if pages is None:
        pages = load_page_summaries(domain, project_id)
    from app.core import chapter_title as ct

    target = ct.normalize(chapter_title)
    if not target:
        return []
    counts: dict[str, int] = {}
    for p in pages:
        if not applicable(p, chapter_title):
            continue
        segs = [s.strip() for s in str(p.get("title") or "").split(" > ")]
        for i, seg in enumerate(segs[:-1]):
            if ct.normalize(seg) != target:
                continue
            key = ct.normalize(segs[i + 1])
            if key:
                counts[key] = counts.get(key, 0) + 1
            break  # 只认最近的一层，别把孙辈也数成子节
    return [{"name": k, "pages": v}
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]


def load_pages_by_ids(ids: list[int]) -> list[dict]:
    """按 id 取知识页完整内容（正文生成注入用）。"""
    if not ids:
        return []
    marks = ",".join(str(int(i)) for i in ids)
    rows = query(f"SELECT id, title, tags, content FROM knowledge_page WHERE id IN ({marks})")
    out = []
    for r in rows:
        content = r.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                content = {}
        content = content or {}
        out.append({
            **classify(r["title"] or "", content),
            "id": r["id"],
            "title": r["title"],
            "method": content.get("method", ""),
            "key_points": content.get("key_points", []),
            "tables": content.get("tables", []),
            "images": content.get("images", []),
            "usage": content.get("usage", ""),
            "estimate_words": content.get("estimate_words"),
            "ref_text": content.get("ref_text", ""),
        })
    return out


def _llm_judge(node_path: str, candidates: list[tuple[int, dict, float]]) -> list[dict]:
    """第三级：模糊候选交 LLM 终判。candidates 为 [(序号, page, score)]。"""
    if not candidates:
        return []
    lines = [
        f"{i}. 标题：{p['title']} | 用途：{p['usage'][:100]}"
        for i, (idx, p, _s) in enumerate(candidates)
    ]
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": f"待编写章节：{node_path}\n\n候选知识页：\n" + "\n".join(lines)},
            ],
            temperature=0.1,
        )
        picked = extract_json(raw, kind="array")
        out = []
        for i in picked:
            if isinstance(i, int) and 0 <= i < len(candidates):
                out.append(candidates[i][1])
        return out
    except Exception:  # noqa: BLE001 LLM 终判失败时保守放行模糊候选
        return [p for _idx, p, _s in candidates]


def match_pages(
    node_title: str,
    node_path: list[str] | None = None,
    pages: list[dict] | None = None,
    top_k: int = TOP_K,
    llm_judge: bool = True,
    domain: str | None = None,
    project_id: int | None = None,
) -> list[dict]:
    """为一个目录节点匹配知识页。node_path 为祖先标题链（不含自身）。

    domain 决定在哪个领域的知识库里找（专业词表与候选池都按域收敛）；
    project_id 决定第二道墙怎么收（见 `load_page_summaries` / `_scope_sql`）。
    返回命中的知识页摘要列表（含 score），按相关度降序。
    """
    if pages is None:
        pages = load_page_summaries(domain, project_id)
    if not pages:
        return []

    node_path = node_path or []
    target = normalize_title(node_title)
    if not target:
        return []

    # 第一级：专业标签过滤（节点路径有专业语境才过滤；泛滥词不算数，见 filter_pool）
    pool = filter_pool([p for p in pages if applicable(p, node_title)],
                       [*node_path, node_title], tags_of(domain))

    # 第二级：标题相似度（叶子为主 + 路径编号级标题参与命中）
    # 知识页按全路径标题存储（父节 > 子节），只比叶子会把"叶是 ⑴布局/实施方案"这类
    # 子页全判 0——如"5.2 小临工程"下 15 张子页全漏，反被同章无关的"5.1 大临工程"命中。
    # 故路径中带编号的真章节段（5.2 / 5.2.2…，_NUM_HEAD 判定，排除 ⑴⑵ 伪叶）也参与计分，
    # 取该页所有编号段的最高 Dice；非编号段（无章节语义的纯描述词）不参与，防大范围误中。
    def _best_dice(p: dict) -> float:
        best = _dice(target, normalize_title(p["leaf"]))
        segs = str(p.get("title") or "").split(" > ")
        for seg in segs[:-1]:  # 非叶子的父/祖章节段
            if _NUM_HEAD.match(seg.strip()):
                d = _dice(target, normalize_title(seg))
                if d > best:
                    best = d
        return best

    scored: list[tuple[dict, float]] = []
    for p in pool:
        s = _best_dice(p)
        # 祖先链标题命中知识页路径：整条路径越像越相关
        for anc in node_path:
            if normalize_title(anc) and normalize_title(anc) in normalize_title(p["title"]):
                s = min(1.0, s + 0.15)
        if s >= SIM_LOW:
            scored.append((p, s))
    scored.sort(key=lambda x: x[1], reverse=True)

    hits = [p for p, s in scored if s >= SIM_HIGH][:top_k]
    ambiguous = [(i, p, s) for i, (p, s) in enumerate(scored) if SIM_LOW <= s < SIM_HIGH]

    # 第三级：模糊带 LLM 终判（补齐到 top_k）
    rest = top_k - len(hits)
    if ambiguous and rest > 0 and llm_judge:
        judged = _llm_judge(" > ".join([*node_path, node_title]), ambiguous[:8])
        hits.extend(judged[:rest])
    elif ambiguous and rest > 0:
        hits.extend(p for _i, p, _s in ambiguous[:rest])

    # 兜底（V1）：三级漏斗零命中时走向量检索补召回（方案 1.2"轻检索兜底"）
    if not hits:
        recalled = _vector_fallback("page", [*node_path, node_title], top_k)
        # 向量召回也只能在已过行业、归属与本章适用性检查的池中取，不能绕墙。
        allowed = {p["id"]: p for p in pool}
        hits = [allowed[h["ref_id"]] for h in recalled if h.get("ref_id") in allowed]

    return hits[:top_k]


def match_for_toc(flat_toc: list[dict], llm_judge: bool = True,
                  domain: str | None = None,
                  project_id: int | None = None) -> dict[str, list[dict]]:
    """批量匹配：展平的目录 [{title, level, parent?}] → {标题路径: [知识页]}。

    知识页摘要只读一次（限本域 + 对本项目可见）；键为全路径标题（父 > 子），
    与章节标识一致。
    """
    pages = load_page_summaries(domain, project_id)
    result: dict[str, list[dict]] = {}
    for item in flat_toc:
        path = [item["parent"]] if item.get("parent") else []
        key = " > ".join([*path, item["title"]])
        result[key] = match_pages(item["title"], path, pages, llm_judge=llm_judge,
                                  domain=domain, project_id=project_id)
    return result


# ---------------------------------------------------------------- 工法条目匹配
# 与知识页同用三级漏斗，但匹配对象是工法名称（"钻孔灌注桩施工工艺"）：
# 第一级 applies_to 标签按节点路径专业词过滤；第二级标题 Dice；
# 第三级模糊候选复用 _llm_judge（用 apply_condition 当 usage 终判）。

# ⚠ 以下三个函数（`METHOD_TOP_K` / `load_method_summaries` / `load_methods_by_ids` /
# `match_methods`）**是 2026-09-04 之前那套「一工法拆成多条结构化条目」的残留，现已没有调用方**
# ——编写正文走的是 `app.services.method_index`（一工法一条概况 + 整文注入）。
# 保留是因为删掉会牵动这里的三级漏斗（与知识页那条线共用 `filter_pool`/`_dice`/`_llm_judge`），
# 但**别再往这里接线**：它们读 `method_entry`（现 0 行），那张表**没有 `domain`/`scope` 列**，
# 真接上去就是一条既无领域墙也无归属墙的路。要加工法就加在 `method_index`。
METHOD_TOP_K = 3  # 每章最多注入工法条目数


def load_method_summaries() -> list[dict]:
    """读全部工法条目摘要（匹配用）。"""
    rows = query("SELECT id, name, applies_to, content FROM method_entry WHERE status != 'deprecated'")
    out = []
    for r in rows:
        applies = r.get("applies_to")
        if isinstance(applies, str):
            try:
                applies = json.loads(applies)
            except (TypeError, ValueError):
                applies = []
        content = r.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                content = {}
        content = content or {}
        out.append({
            "id": r["id"],
            "name": r["name"] or "",
            "applies_to": applies or [],
            "apply_condition": content.get("apply_condition", ""),
            "process": content.get("process", []),
            "key_controls": content.get("key_controls", []),
        })
    return out


def load_methods_by_ids(ids: list[int]) -> list[dict]:
    """按 id 取工法条目完整内容（正文生成注入用）。"""
    if not ids:
        return []
    marks = ",".join(str(int(i)) for i in ids)
    rows = query(f"SELECT id, name, applies_to, content FROM method_entry WHERE id IN ({marks})")
    out = []
    for r in rows:
        content = r.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                content = {}
        content = content or {}
        applies = r.get("applies_to")
        if isinstance(applies, str):
            try:
                applies = json.loads(applies)
            except (TypeError, ValueError):
                applies = []
        out.append({
            "id": r["id"],
            "name": r["name"],
            "applies_to": applies or [],
            "apply_condition": content.get("apply_condition", ""),
            "process": content.get("process", []),
            "key_controls": content.get("key_controls", []),
            "equipment": content.get("equipment", []),
            "quality_points": content.get("quality_points", []),
            "tables": content.get("tables", []),
        })
    return out


def match_methods(
    node_title: str,
    node_path: list[str] | None = None,
    entries: list[dict] | None = None,
    top_k: int = METHOD_TOP_K,
    llm_judge: bool = True,
    domain: str | None = None,
) -> list[dict]:
    """为一个目录节点匹配工法条目（"施工方案"类章节用）。

    返回命中的工法摘要列表，按相关度降序；模糊带复用 _llm_judge 终判。
    """
    if entries is None:
        entries = load_method_summaries()
    if not entries:
        return []

    node_path = node_path or []
    target = normalize_title(node_title)
    if not target:
        return []

    # 第一级：节点路径专业词 ∩ 工法 applies_to 标签（词表按领域取，泛滥词同知识页那条线一样不算数）
    pool = filter_pool(entries, [*node_path, node_title], tags_of(domain), key="applies_to")

    # 第二级：工法名称与节点标题的 Dice 相似度；applies_to 命中路径加分
    joined_path = " ".join(node_path)
    scored: list[tuple[dict, float]] = []
    for m in pool:
        s = _dice(target, normalize_title(m["name"]))
        if any(t and t in joined_path for t in m["applies_to"]):
            s = min(1.0, s + 0.15)
        if s >= SIM_LOW:
            scored.append((m, s))
    scored.sort(key=lambda x: x[1], reverse=True)

    hits = [m for m, s in scored if s >= SIM_HIGH][:top_k]
    ambiguous = [(i, m, s) for i, (m, s) in enumerate(scored) if SIM_LOW <= s < SIM_HIGH]

    rest = top_k - len(hits)
    if ambiguous and rest > 0 and llm_judge:
        # 复用知识页终判：工法条目以 name 当 title、apply_condition 当 usage
        pseudo = [{**m, "title": m["name"], "usage": m["apply_condition"]} for _i, m, _s in ambiguous[:8]]
        judged = _llm_judge(" > ".join([*node_path, node_title]), [(i, p, 0) for i, p in enumerate(pseudo)])
        hits.extend(judged[:rest])
    elif ambiguous and rest > 0:
        hits.extend(m for _i, m, _s in ambiguous[:rest])

    # 兜底（V1）：漏斗零命中时走向量检索补召回
    if not hits:
        fb = _vector_fallback("method", [*node_path, node_title], top_k)
        if fb:
            return load_methods_by_ids([h["ref_id"] for h in fb])

    return hits[:top_k]


def _vector_fallback(ref_type: str, path_titles: list[str], top_k: int) -> list[dict]:
    """向量兜底：本项目已移除向量库，恒返回空（保留接口签名以兼容调用方）。"""
    return []

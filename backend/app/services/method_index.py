"""工艺工法概况索引（2026-09-04 重构，用户决策：一工法一个文档，不再提炼结构化条目）。

思路：与项目文件章节索引同模式——
1. 解析完成后，对工法**整篇全文**提取一条「概况 + 适用范围」存 method_index（一工法一条）；
2. 编写正文章节时，先把各工法概况交给 AI 选"需要哪个工法" → 再取该工法**整篇文档**
   （含文字/工艺流程/表格/图片，图片改写为可访问 URL）注入正文上下文。

1M 上下文下整篇工法文档轻松装下，写作拿到的是完整工艺而非被拆碎的条目。

**两道墙和知识页那条线共用**：工法文档也是 `knowledge_doc` 行（`doc_category='method'`），
`domain`（领域，硬过滤）与 `scope`（归属）都落在它身上。`catalog()` 是唯一的取料口，
两道墙都在里面——见它的注释。别在别处另写一份 `SELECT`：两份 SQL 就是两份会各自漂移的墙
（2026-10-16 修的正是这个：列表页给了「归属」开关，工法这条线却不认）。
"""
import json
import re
from pathlib import Path

from app.core.database import execute, query, query_one
from app.services.llm import llm

MAX_SELECT = 10         # 单章最多注入工法数
MAX_TOTAL_CHARS = 400_000  # 注入工法整文总字符上限

# 2026-10-15：原先的 name / applies_to 例子是铁路的（「钻孔灌注桩施工工艺」
# 「CRTSⅢ型无砟轨道板铺设施工工法」「编写桥梁钻孔桩基础施工方案」）。例子就是给模型
# 定的调：给铁路例子，它就把低空的工法也往"XX施工工艺/施工工法"上命名。
# 这里改成中性例子——讲的是"工法命名要有行业规范名"这件事本身，与领域无关。
#
# 2026-10-16 补：上一轮只换掉了铁路的例子，**留着「施工」这个口径**（原写作
# 「如"XX施工工艺""XX作业工法"」）——那仍然是给模型定调，且与 `Domain.genre`
# 那套机制不符（`core/domains.fill_genre`：提示词里不该出现写死的体裁/行业名词）。
# 现在连「施工」也去掉，只留"用本行业的正式叫法"。同批修的还有 `writer_agent`
# 的工法注入块与 `matching.JUDGE_SYSTEM` 的举例。
# 守护：`tests/verify_domain_prompt_isolation.py` 第 8 节。
OVERVIEW_SYSTEM = """你是工艺技术专家。下面给出一篇工艺工法文档的**完整原文**，请生成该工法的索引条目
（供后续 AI 判断"编写哪个子节时应参考此工法"，描述越具体判断越准）：
- name：该工艺/方法的规范名称（用**本行业**的正式叫法命名，不要照抄文件名），简短；
- summary（50~200 字）：该工法做什么、适用什么对象/部位、核心工艺流程与关键要点，
  逐项点明（含涉及的主要工序、关键控制、配套设备/表）；
- applies_to（50~200 字）：适用范围——编写哪些章节/问题时应当参考本工法，
  以及典型触发场景（如"编写XX专项方案、XX质量保证措施时参考"）。
忠于原文，概括具体信息而不是空泛描述。
只输出 JSON：{"name": "...", "summary": "...", "applies_to": "..."}，不要解释、不要代码围栏。"""

SELECT_SYSTEM = """你是技术文件编制专家。给定「编写需求」和一份工艺工法清单（名称+概况+适用范围），
选出编写时需要参考的**工法**。选中的工法会以整篇原文提供。

规则：
1. 只按概况与适用范围判断，宁多勿漏（相关就选，上下文足够大），明显无关不选；
2. 最多输出 {max_select} 个序号；没有相关返回空数组。
只输出 JSON：{{"ids": [1, 3]}}（ids 为清单第一列序号），不要解释。"""


def build(doc_id: int, parsed_path: str) -> dict:
    """为一个工法文档提取概况并写入 method_index（一工法一条，upsert）。返回写入记录。"""
    md_path = Path(parsed_path) / "full.md"
    if not md_path.exists():
        raise FileNotFoundError(md_path)
    full = md_path.read_text(encoding="utf-8")
    raw = llm.chat(
        [
            {"role": "system", "content": OVERVIEW_SYSTEM},
            {"role": "user", "content": f"工法文档全文：\n{full}"},
        ],
        temperature=0.1,
        max_tokens=2000,
    )
    s, e = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[s : e + 1]) if s != -1 and e != -1 else {}
    name = str(data.get("name") or "").strip()
    if not name:
        row = query_one("SELECT file_name FROM knowledge_doc WHERE id = %s", (doc_id,))
        name = (row or {}).get("file_name", f"工法{doc_id}").rsplit(".", 1)[0]
    execute(
        "DELETE FROM method_index WHERE doc_id = %s", (doc_id,),
    )
    execute(
        "INSERT INTO method_index (doc_id, name, summary, applies_to) VALUES (%s, %s, %s, %s)",
        (doc_id, name[:300], str(data.get("summary") or "")[:600], str(data.get("applies_to") or "")[:400]),
    )
    return {"doc_id": doc_id, "name": name}


def catalog(domain: str | None = None, project_id: int | None = None,
            *, library_view: bool = False) -> list[dict]:
    """某领域的已解析工法文档概况清单（联文档名）。domain 为空则不限领域。

    **两道墙都在这一个函数里**（与知识页那条线走同一份 `matching._scope_sql`，不另写一份
    ——两份 SQL 就是两份会各自漂移的墙）：

    - `project_id` 给了 → 「领域通用 + 本项目专用」；**没给 → 只取「领域通用」**。
      与 `_scope_sql` 同一个默认：调用方漏传的后果是**少料**，不是把别的项目的料引进来。
    - `off` 在**任何**情形下都不进池。

    `library_view=True` 是给「知识库列表」用的**唯一例外**：**不过滤归属**，`off` 的工法在
    库里照样看得见——「off = 从检索排除、在库中仍可见」是归属三态的定义，列表页要如实显示，
    否则用户连"改回来"的入口都找不到。**生成选料绝不能传这个**。

    ⚠ 此前这里**根本没有归属过滤**：工法文档在知识库列表里带着「归属」徽标、点一下能改成
    `off`（`PUT /knowledge/docs/{id}/scope` 改的是 `knowledge_doc.scope`，两类文档共用），
    但工法这条线不看它——**同一个按钮，标书文档生效、工法文档静默无效**（2026-10-16 修）。
    """
    from app.services.matching import _scope_sql

    sql = (
        "SELECT mi.doc_id, mi.name, mi.summary, mi.applies_to, d.file_name, d.parsed_path"
        " FROM method_index mi JOIN knowledge_doc d ON d.id = mi.doc_id"
        " WHERE d.doc_category = 'method' AND d.status = 'parsed' AND d.parsed_path IS NOT NULL"
    )
    params: tuple = ()
    if domain:
        sql += " AND d.domain = %s"
        params += (domain,)
    if not library_view:
        frag, scope_params = _scope_sql(project_id)
        sql += " AND " + frag
        params += scope_params
    return query(sql + " ORDER BY mi.id", params)


def select(catalog: list[dict], need: str, max_select: int = MAX_SELECT) -> list[int]:
    """AI 按编写需求从工法概况中选取需要的工法 doc_id（稳定序号防编造 id）。"""
    if not catalog or not need:
        return []
    seq_map: dict[int, int] = {}
    lines = []
    for k, c in enumerate(catalog, 1):
        seq_map[k] = c["doc_id"]
        lines.append(f"{k} | {c['name'][:40]} | 概况:{c['summary'][:200]} | 适用:{c['applies_to'][:120]}")
    raw = llm.chat(
        [
            {"role": "system", "content": SELECT_SYSTEM.replace("{max_select}", str(max_select))},
            {"role": "user", "content": f"【编写需求】\n{need[:1500]}\n\n【工法清单】\n" + "\n".join(lines)},
        ],
        temperature=0.1,
        max_tokens=800,
    )
    s, e = raw.find("{"), raw.rfind("}")
    if s == -1 or e == -1:
        return []
    seqs = json.loads(raw[s : e + 1]).get("ids") or []
    out = []
    for i in seqs:
        try:
            if int(i) in seq_map and seq_map[int(i)] not in out:
                out.append(seq_map[int(i)])
        except (TypeError, ValueError):
            continue
    return out[:max_select]


def load(catalog: list[dict], doc_ids: list[int]) -> list[dict]:
    """按工法取**整篇文档**原文（图片引用改写为可访问 URL）。"""
    by_id = {c["doc_id"]: c for c in catalog}
    out = []
    total = 0
    for did in doc_ids:
        c = by_id.get(did)
        if not c or not c.get("parsed_path"):
            continue
        md_path = Path(c["parsed_path"]) / "full.md"
        if not md_path.exists():
            continue
        text = md_path.read_text(encoding="utf-8")
        # 图片引用改写为 /uploads URL
        from app.services.file_index import _rewrite_image_refs

        text = _rewrite_image_refs(text, md_path.parent)
        if total + len(text) > MAX_TOTAL_CHARS:
            continue
        total += len(text)
        out.append({"name": c["name"], "file": c["file_name"], "path": c["name"], "text": text})
    return out


def match_methods(need: str, domain: str | None = None,
                  project_id: int | None = None) -> list[dict]:
    """写正文用：按本章需求选工法并取整文。fail-safe 返回空。

    domain 限定只在本领域工法库里选（跨域工法对本章无参考价值，且会挤占注入额度）；
    project_id 收第二道墙（归属，见 `catalog`）——**调用方必须传**，不传只会少料。
    """
    try:
        cat = catalog(domain, project_id)
        if not cat:
            return []
        ids = select(cat, need)
        return load(cat, ids)
    except Exception:  # noqa: BLE001
        return []

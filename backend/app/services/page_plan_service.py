"""篇幅控制的取数层：给「页数」抽屉组装面板数据，给生成侧算每章的目标字数。

算法都在 app/core/page_plan.py（纯函数、无 DB），这里只负责把它接到库上：
目录树 → 树序扁平（带层级/是否容器/人工额度）→ 按权重分摊 → 与实际字数对比。

**没设目标页数时 `target_words_map` 直接返回空字典**——也就是说，不设目标的项目
在生成侧是零开销、零行为变化的，注入逻辑只在真的设了目标后才起作用。
"""
from app.core import docx_style, page_plan
from app.core.database import query


def _ordered_nodes(project_id: int) -> list[dict]:
    """目录树按**树序**扁平，每项带层级、是否容器、人工额度、父章类型倍率。

    用 get_tree 而不是自己查表：跨层级的 sort_order 拉平后不是树序，
    面板里按树序排（子节点紧跟父节点）才好读。

    **类型倍率必须沿树下传**：技术核心往往只写在父章标题上（「四、技术方案」），
    子节标题是「总体技术路线」这类具体说法、命不中任何关键词。只按自己的标题判,
    技术方案章的子节就会被当中性内容，和「纳税及社会保障资金缴纳证明」拿一样的额度。
    """
    from app.services import section_service

    rows = query("SELECT id, target_words FROM chapter_node WHERE project_id = %s",
                 (project_id,))
    overrides = {r["id"]: r["target_words"] for r in rows}

    out: list[dict] = []

    def walk(nodes: list[dict], level: int, ancestor: float | None) -> None:
        for n in nodes:
            kids = n.get("children") or []
            out.append({
                "id": n["id"], "title": n["title"], "level": level,
                "has_children": bool(kids),
                "override": overrides.get(n["id"]),
                # 父章**已解析**的倍率（不是它的原始命中值），逐层衰减
                "ancestor_type_factor": ancestor,
            })
            # 传给下一层的是本章解析后的**链值**（type_chain 而非 type_factor：
            # 整条链都没命中要原样传 None 下去，否则中性父章会凭空给子节打 0.9 折）
            walk(kids, level + 1, page_plan.type_chain(n["title"], ancestor))

    walk(section_service.get_tree(project_id), 1, None)
    return out


def _actual_words(project_id: int) -> dict[int, int]:
    """各章已写正文的字数（按本模块统一口径，见 page_plan.count_words）。"""
    rows = query(
        "SELECT sc.chapter_node_id AS nid, sc.content FROM section_content sc"
        " JOIN chapter_node cn ON cn.id = sc.chapter_node_id WHERE cn.project_id = %s",
        (project_id,),
    )
    return {r["nid"]: page_plan.content_words(r["content"]) for r in rows}


def coefficient(project_id: int, style=None) -> dict:
    """当前排版下的「字/页」系数。

    第 2a 步只有**估算**一种来源（密度 × 默认填充率，见 page_plan 模块说明）。
    第 2b 步会在这里优先返回该排版规格下的**实测**值，并带上测量引擎与时间——
    所以返回结构现在就按「来源可区分」来定，前端也好如实标注「估算 / 实测」。
    """
    style = style or docx_style.get_project_style(project_id)
    from app.services.pagination_service import stored
    measured = stored(project_id)
    if measured and measured.get('style') == style.to_dict() and measured.get('value',0) > 0:
        return {**measured, 'source':'measured'}
    density = page_plan.page_density(style)
    fill = page_plan.DEFAULT_FILL_FACTOR
    return {
        "value": round(density * fill),
        "source": "estimated",
        "density": round(density),
        "fill_factor": fill,
        "engine": None,        # 实测引擎名（第 2b 步填，如 "WPS Office 12.1"）
        "measured_at": None,
    }


def target_words_map(project_id: int) -> dict[int, int]:
    """每章的目标字数 {node_id: 字数}。没设目标页数就返回空（生成侧据此不注入篇幅要求）。

    批量生成时**在循环外算一次**传下去——每章算一遍等于把目录树和分摊重做 56 次。
    """
    plan = page_plan.get_project_plan(project_id)
    if not plan.target_pages:
        return {}
    style = docx_style.get_project_style(project_id)
    total = int(plan.target_pages * coefficient(project_id, style)["value"])
    return page_plan.allocate(_ordered_nodes(project_id), total)


def build_page_plan_view(project_id: int) -> dict:
    """「页数」抽屉的一次性载荷（照 /docx-style 的约定：一个来回把要用的都给全）。"""
    from app.services.pagination_service import view
    plan = page_plan.get_project_plan(project_id)
    style = docx_style.get_project_style(project_id)
    coeff = coefficient(project_id, style)
    nodes = _ordered_nodes(project_id)
    words = _actual_words(project_id)

    total = int(plan.target_pages * coeff["value"]) if plan.target_pages else None
    targets = page_plan.allocate(nodes, total)

    rows = []
    for n in nodes:
        target = targets.get(n["id"])
        actual = words.get(n["id"], 0)
        rows.append({
            "id": n["id"],
            "title": n["title"],
            "level": n["level"],
            "weight": round(page_plan.chapter_weight(
                n["title"], n["level"], n["has_children"], n["ancestor_type_factor"]), 2),
            # 倍率是不是从父章继承来的（前端可据此提示「随父章」）
            "inherited": (page_plan.own_type_factor(n["title"]) is None
                          and n["ancestor_type_factor"] is not None),
            "target_words": target,
            "override": n["override"],
            "words": actual,
            # 偏差按比例给，前端才好按大小排序挑「偏差最大的章」
            "deviation": (round((actual - target) / target, 3) if target else None),
        })

    written = sum(1 for r in rows if r["words"])
    return {
        "plan": plan.to_dict(),
        "measurement": view(project_id),
        "options": page_plan.options(),
        "style": style.to_dict(),
        "coefficient": coeff,
        "target_words_total": total,
        "nodes": rows,
        "stats": {
            "nodes": len(rows),
            "written": written,
            "words": sum(r["words"] for r in rows),
        },
    }


def save_page_overrides(project_id: int, overrides: dict) -> int:
    """保存单章人工额度：值为空的置 NULL（回到按权重分摊）。返回写入的条数。

    只认该项目下真实存在的节点 id——前端多送/送错 id 不该写进别人项目。
    """
    from app.core.database import execute

    valid = {r["id"] for r in query("SELECT id FROM chapter_node WHERE project_id = %s",
                                    (project_id,))}
    n = 0
    for raw_id, raw_val in (overrides or {}).items():
        try:
            nid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if nid not in valid:
            continue
        try:
            val = int(float(raw_val)) if raw_val not in (None, "") else None
        except (TypeError, ValueError):
            continue
        if val is not None and val <= 0:
            val = None
        execute("UPDATE chapter_node SET target_words = %s WHERE id = %s AND project_id = %s",
                (val, nid, project_id))
        n += 1
    return n

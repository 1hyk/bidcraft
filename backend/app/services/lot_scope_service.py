r"""P1：标段归属判定服务。

判定策略 = 【规则快筛（零成本）】→ 未命中才走【AI 兜底】：
- 规则：标段档案的"本标段独有对象"名命中、或范围值落在该标段区间；
- 命中 1 个标段 → 该标段；命中 ≥2 个 → ["multi"]（章内含多标段）；命不中 → []（未判定，交 AI）。

**字段名一律按域声明取**（`Domain.lot_fields`），这里不写死"构造物/里程"：
2026-10-15 之前 `judge_text` 硬编码了铁路标段代码前缀 `CGJXZQ-\d+` 三条正则，
"CGJXZQ-8标铺轨范围"这类写法才算数——换一个领域，标段代码叫什么都不认识。

**区间解析不挂 `Domain`**：「用区间判归属」是机制的职责（`LotField.kind == "ranges"`
已经声明了哪个字段是区间），怎么从一段文字里抠出可比数值是**格式**问题，不是领域知识。

用数据说话：跑一遍全量索引的规则命中率，先看快筛能覆盖多少，再决定 AI 兜底的规模。
"""
import json
import re

from app.core.database import execute, query
from app.services.lot_profile_service import build_profile, whitelist_terms

#: 桩号式写法：DK411+553.05 / K12+300（前缀字母 + 公里 + 米）
_MILE_RE = re.compile(r"([A-Za-z]{0,4}K)\s*(\d{2,5})\s*\+\s*(\d{1,3}(?:\.\d+)?)")
#: 普通数值写法（区间两端直接是数字时用）
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# 允许中文顿号/波浪号等分隔
_RANGE_SPLIT_RE = re.compile(r"[～~—－-]")


def _numbers(text: str) -> list[tuple[str, float]]:
    """文本里的可比数值 → [(前缀, 值)]。

    两种写法都认：桩号式（`DK411+553.05` → `("DK", 411553.05)`）与普通数值（`1200` → `("", 1200.0)`）。
    有桩号式写法时**只取桩号**，否则 `DK1+200` 会被拆成 `1` 和 `200` 两个数，区间判反。
    """
    out: list[tuple[str, float]] = []
    for m in _MILE_RE.finditer(text or ""):
        try:
            out.append((m.group(1).upper(),
                        int(m.group(2)) * 1000 + float(m.group(3))))
        except (TypeError, ValueError):
            continue
    if out:
        return out
    return [("", float(m.group(0))) for m in _NUM_RE.finditer(text or "")]


def _ranges_of(segments: list[str]) -> dict[str, list[tuple[float, float]]]:
    """区间字段 → {前缀: [(起, 止)]}（只处理"起～止"两端的写法）。"""
    out: dict[str, list[tuple[float, float]]] = {}
    for seg in segments or []:
        parts = [p.strip() for p in _RANGE_SPLIT_RE.split(str(seg)) if p.strip()]
        if len(parts) != 2:
            continue
        a, b = (_numbers(parts[0]), _numbers(parts[1]))
        if a and b and a[0][0] == b[0][0]:
            out.setdefault(a[0][0], []).append((min(a[0][1], b[0][1]), max(a[0][1], b[0][1])))
    return out


def build_lot_index(project_id: int, ensure_profiles: bool = True) -> list[dict]:
    """全部标段的判定索引：[{lot_code, terms, ranges, term_label, range_label}]。

    **本域没声明标段档案字段 → 返回空表**（`lot` 表在低空/软件测试这类单一交付的
    项目上本来就是空的，这里一并明说，省得下游拿一份空索引去判归属）。
    """
    from app.core.domains import domain_of_project, lot_fields_of
    from app.services.lot_profile_service import get_profile

    domain = domain_of_project(project_id)
    fields = lot_fields_of(domain)
    if not fields:
        return []
    # 区间字段按域声明的 kind 找（原先写死 mileage_ranges）
    range_key = next((f.key for f in fields if f.kind == "ranges"), None)
    # 判定依据要回述给人看（"命中 XX：…"），标签也按域取——原先写死「主要构造物/大临」
    range_label = next((f.label for f in fields if f.kind == "ranges"), "")
    term_label = "、".join(f.label for f in fields if f.kind == "list")

    lots = query("SELECT lot_code FROM lot WHERE project_id = %s ORDER BY id", (project_id,))
    idx = []
    for lot in lots:
        code = lot["lot_code"]
        prof = get_profile(project_id, code)
        if not prof and ensure_profiles:
            prof = build_profile(project_id, code, skip_llm=True)  # 零 AI 成本
        if not prof:
            continue
        idx.append({
            "lot_code": code,
            "terms": [t for t in whitelist_terms(prof, domain) if len(t) >= 2],
            "ranges": _ranges_of(prof.get(range_key) or []) if range_key else {},
            "range_label": range_label,
            "term_label": term_label,
        })
    return idx


def _code_patterns(codes: list[str]) -> list[tuple[str, str]]:
    """把标段代码拆成 (前缀, 末段数字)，用于在文本里认它。

    **不再写死前缀**（原先指死 `CGJXZQ`）：代码长什么样是招标文件的事，
    这里只做"前缀 + 末尾数字"的一般化拆解。拆不出末尾数字的（纯名字代码）
    只能整串撞，不参与"1~10标"这类范围表述的识别。
    """
    out = []
    for c in codes:
        m = re.fullmatch(r"(.*?)(\d+)", c or "")
        out.append((m.group(1), m.group(2)) if m else (c, ""))
    return out


def judge_text(text: str, lot_index: list[dict]) -> tuple[list[str], str]:
    """规则判定：返回 (applies_lots, 依据)。命中 1 个→该标段；≥2→["multi"]；无→[]。

    优先级：① 文本里明确写了的标段代码 > ② 本标段独有对象名 > ③ 范围值落区间。
    （① 最可靠——"<标段代码>标范围"这类不该再拿区间去猜。）
    """
    txt = text or ""
    # 没有标段索引（本域根本没标段）→ 一律判不出，别拿空索引去撞
    if not lot_index:
        return [], ""
    codes = [li["lot_code"] for li in lot_index if li.get("lot_code")]
    pats = _code_patterns(codes)

    # ⓿ 全标段范围表述（"<前缀>1~<前缀>10 标段"、或"1~10标"）→ 多标段
    for prefix, _num in pats:
        if prefix and re.search(rf"{re.escape(prefix)}\d+\s*[~～—-]\s*(?:{re.escape(prefix)})?\d+", txt):
            return ["multi"], "全标段范围表述"
    if re.search(r"\d+\s*[~～]\s*\d+\s*标", txt):
        return ["multi"], "全标段范围表述"

    # ① 明确标段代码。前后加边界：`CGJXZQ-1` 不该撞上 `CGJXZQ-10`
    hit = [c for c in codes
           if re.search(rf"(?<![0-9A-Za-z\-]){re.escape(c)}(?![0-9])", txt)]
    hit = sorted(dict.fromkeys(hit))
    if len(hit) == 1:
        return [hit[0]], "文本明确标注了标段代码"
    if len(hit) > 1:
        return ["multi"], "文本提及多个标段代码：" + "、".join(hit[:4])

    # ② 本标段独有对象名
    hits: dict[str, list[str]] = {}
    for li in lot_index:
        for t in li["terms"]:
            if t and t in txt:
                hits.setdefault(li["lot_code"], []).append(t)
    if len(hits) == 1:
        code = next(iter(hits))
        label = next((li.get("term_label") or "" for li in lot_index
                      if li["lot_code"] == code), "")
        return [code], f"命中{label or '本标段对象'}：" + "、".join(hits[code][:3])
    if len(hits) > 1:
        brief = "、".join(f"{c}({v[0]})" for c, v in list(hits.items())[:4])
        return ["multi"], "同时命中多个标段：" + brief

    # ③ 范围值落区间
    nums = _numbers(txt)
    if nums:
        for li in lot_index:
            for prefix, val in nums:
                for lo, hi in li["ranges"].get(prefix, []):
                    if lo <= val <= hi:
                        label = li.get("range_label") or "范围"
                        shown = f"{prefix}{val / 1000:.3f}" if prefix else f"{val:g}"
                        return [li["lot_code"]], f"{label} {shown} 落在该标段范围"
    return [], ""


JUDGE_SYSTEM = """你是标段归属判定器。给定「本项目标段划分」（各标段的范围与本标段独有对象），
为**每一条**内容判定其标段归属。

只输出 JSON，不要解释、不要代码围栏：
{"results": [{"i": 编号, "lots": ["取值"], "why": "简短依据"}]}

lots 取值：
- ["<标段代码>"]：只涉及某一个标段（代码用上面「本项目标段划分」里给的那串，原样照抄）
- ["all"]：全线共性（项目名称、承包方式、总工期、技术标准等，各标段都适用）
- ["background"]：全线背景（如"全线控制性工程清单""各标段一览"——可作背景说明，
  但**不是本标段的工程内容**，不得写成"本标段…"）
- ["multi"]：一条内容里并列涉及多个标段的具体内容
- []：确实判不出来（宁可留空，不要猜）

判定要点：出现标段划分表里点名的对象或范围值时，按表对号入座；
拿不准某条内容属于哪个标段，就留空 []，**严禁默认给 ["all"]**（"判断不出就标 all"是老 bug 的根源）。"""


def judge_batch_llm(items: list[tuple[int, str]], lot_index: list[dict],
                    batch: int = 30) -> dict[int, tuple[list[str], str]]:
    """批量 AI 判定归属（每批若干条合并一次调用，降低成本）。

    items: [(id, text)]；返回 {id: (applies_lots, why)}。失败的条目不出现在结果里（保持原值）。
    """
    from app.services.llm import llm

    # 两栏的标签按域取（原先写死「里程」「主要构造物」）——挂到 Domain.lot_fields 的
    # label 上，换一个领域就是那套字段自己的叫法。
    lot_desc = "\n".join(
        f"- {li['lot_code']}："
        f"{li.get('range_label') or '范围'} "
        f"{'、'.join(f'{p}{lo/1000:.3f}~{hi/1000:.3f}' for p, segs in li['ranges'].items() for lo, hi in segs) or '（无）'}；"
        f"{li.get('term_label') or '本标段对象'}：{'、'.join(li['terms']) or '（无）'}"
        for li in lot_index
    )
    out: dict[int, tuple[list[str], str]] = {}
    for s in range(0, len(items), batch):
        chunk = items[s : s + batch]
        lines = "\n".join(f"{i}｜{t[:300]}" for i, t in chunk)
        try:
            raw = llm.chat(
                [
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": (
                        f"【本项目标段划分】\n{lot_desc}\n\n【待判定内容（编号｜内容）】\n{lines}"
                    )},
                ],
                temperature=0.1,
                max_tokens=3000,
            )
            a, b = raw.find("{"), raw.rfind("}")
            if a == -1 or b == -1:
                continue
            for r in (json.loads(raw[a : b + 1]).get("results") or []):
                try:
                    i = int(r.get("i"))
                except (TypeError, ValueError):
                    continue
                lots = r.get("lots")
                if not isinstance(lots, list):
                    lots = []
                out[i] = ([str(x) for x in lots], str(r.get("why") or "")[:120])
        except Exception as e:  # noqa: BLE001 单批失败不影响其他批
            print(f"[标段判定] 批次 {s // batch} 失败: {e}")
    return out


def rebuild_by_llm(project_id: int, target: str = "sections", limit: int = 0,
                   rejudge_all: bool = False, on_progress=None) -> dict:
    """对"规则未判定"的条目做 AI 批量判定并落库。

    target: sections | facts；limit=0 表示不限；rejudge_all=True 时连已判定的也重判；
    on_progress(done_batch, total_batch)：批次进度回调（后台任务用）。
    """
    from app.services.lot_service import selected_lot_code

    idx = build_lot_index(project_id)
    mine = selected_lot_code(project_id)
    if target == "facts":
        rows = query(
            "SELECT id, CONCAT(IFNULL(fact_key,''), ' = ', IFNULL(fact_value,'')) AS txt, applicable_lots AS cur"
            " FROM global_fact WHERE project_id = %s", (project_id,),
        )
    elif target == "requirements":
        rows = query(
            "SELECT id, CONCAT('[', IFNULL(category,''), '] ', IFNULL(title,''), ' ', IFNULL(content,'')) AS txt,"
            " applicable_lots AS cur FROM tender_requirement WHERE project_id = %s", (project_id,),
        )
    else:
        rows = query(
            "SELECT id, CONCAT(IFNULL(sec_path,''), ' ', IFNULL(summary,'')) AS txt, applies_lots AS cur"
            " FROM file_section_index WHERE project_id = %s", (project_id,),
        )

    def _cur_lots(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return []
        return v or []

    todo = []
    for r in rows:
        if not rejudge_all:
            cur = _cur_lots(r.get("cur"))
            # 跳过已由规则判定的（本标段/其他标段/multi）
            if cur and cur not in ([], ["unknown"]):
                continue
        todo.append((r["id"], r["txt"] or ""))
    if limit:
        todo = todo[:limit]

    stat = {"todo": len(todo), "judged": 0, "mine": 0, "other": 0,
            "all": 0, "background": 0, "multi": 0, "empty": 0}
    BS = 30
    nbatch = (len(todo) + BS - 1) // BS
    for bi in range(nbatch):
        chunk = todo[bi * BS : (bi + 1) * BS]
        verdicts = judge_batch_llm(chunk, idx, batch=BS)
        # 逐批写库：进度可见、中途失败也不丢已判定的结果
        for i, (lots, why) in verdicts.items():
            stat["judged"] += 1
            if not lots:
                stat["empty"] += 1
            elif "all" in lots:
                stat["all"] += 1
            elif "background" in lots:
                stat["background"] += 1
            elif "multi" in lots:
                stat["multi"] += 1
            elif mine and mine in lots:
                stat["mine"] += 1
            else:
                stat["other"] += 1
            if target == "facts":
                execute("UPDATE global_fact SET applicable_lots = %s WHERE id = %s",
                        (json.dumps(lots, ensure_ascii=False), i))
            elif target == "requirements":
                execute("UPDATE tender_requirement SET applicable_lots = %s WHERE id = %s",
                        (json.dumps(lots, ensure_ascii=False), i))
            else:
                execute(
                    "UPDATE file_section_index SET applies_lots = %s, lot_evidence = %s WHERE id = %s",
                    (json.dumps(lots, ensure_ascii=False), (why + "（AI 判定）")[:200], i),
                )
        if on_progress:
            on_progress(bi + 1, nbatch)
    return stat


def parse_lots(v) -> list[str]:
    """DB 里的 applies_lots/applicable_lots（JSON 字符串或 list）→ list。"""
    if isinstance(v, str):
        try:
            return [str(x) for x in (json.loads(v) or [])]
        except (TypeError, ValueError):
            return []
    return [str(x) for x in (v or [])]


def filter_catalog_by_lot(catalog: list[dict], lot_code: str | None) -> list[dict]:
    """按当前标段过滤候选章节：**排除"明确属于其他标段"的**；全线/背景/多标段/未判定保留。

    注意：未判定一律保留（判不出 ≠ 不属于本标段），避免把内容过滤没。
    """
    if not lot_code:
        return list(catalog)
    out = []
    for c in catalog:
        lots = parse_lots(c.get("applies_lots"))
        if lots and not any(x in lots for x in ("all", "multi", "background", lot_code)):
            continue  # 明确属于其他标段 → 不进候选
        out.append(c)
    return out


def clip_text_by_terms(text: str, terms: list[str], ctx: int = 2) -> str:
    """按本标段词表做**行级裁剪**（用于"含多标段"的整表章节：附件1、附表7、图纸目录等）。

    保留命中行及其上下 ctx 行；若一条都没命中则原样返回（宁可多给，不可裁空）。
    """
    if not text or not terms:
        return text
    lines = text.splitlines()
    keep: set[int] = set()
    for i, ln in enumerate(lines):
        if any(t and t in ln for t in terms):
            for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1)):
                keep.add(j)
    if not keep or len(keep) < 3:
        return text
    return "\n".join(lines[i] for i in sorted(keep))


def relabel_all(project_id: int, on_progress=None) -> dict:
    """标段归属全量重标（规则 → AI 补判定），**只改归属字段，不动内容**。

    步骤：规则重标索引/事实 → AI 批量判未判定项（索引 → 事实 → 招标要求）→ 汇总。

    on_progress(pct, msg)：总进度 0~100 回调（可由「重新抽取标段档案」的任务直接串联，
    让"建档 → 重标"在一个任务里可见地跑完，而不是另起一个用户看不见的后台任务）。
    """
    def _p(pct: int, msg: str) -> None:
        if on_progress:
            on_progress(int(pct), msg)

    _p(2, "规则重标：章节索引…")
    s1 = rebuild_sections(project_id, apply=True)["stat"]
    _p(6, "规则重标：全局参数…")
    s2 = rebuild_facts(project_id, apply=True)["stat"]

    # AI 补判定：章节索引占 6%~46%，全局参数占 46%~88%，招标要求占 88%~99%
    r1 = rebuild_by_llm(project_id, "sections",
                        on_progress=lambda i, n: _p(6 + 40 * i / max(1, n),
                                                    f"AI 判定章节索引 {i}/{n} 批"))
    r2 = rebuild_by_llm(project_id, "facts",
                        on_progress=lambda i, n: _p(46 + 42 * i / max(1, n),
                                                    f"AI 判定全局参数 {i}/{n} 批"))
    r3 = rebuild_by_llm(project_id, "requirements",
                        on_progress=lambda i, n: _p(88 + 11 * i / max(1, n),
                                                    f"AI 判定招标要求 {i}/{n} 批"))
    return {
        "stat": {"sections": s1, "facts": s2},
        "ai": {"sections": r1, "facts": r2, "requirements": r3},
        "summary": (f"索引规则 {s1['other'] + s1['multi']} 条 → AI 补判 {r1.get('judged', 0)}；"
                    f"全局参数规则 {s2['other'] + s2['multi']} 条 → AI 补判 {r2.get('judged', 0)}；"
                    f"招标要求 AI 判定 {r3.get('judged', 0)} 条"),
    }


def relabel_all_task(task_id: int, project_id: int) -> None:
    """后台任务：标段归属全量重标（进度写 task 表）。"""
    from datetime import datetime

    from app.core.database import execute

    def _prog(pct: int, msg: str) -> None:
        execute("UPDATE task SET progress = %s, detail = %s, updated_at = %s WHERE id = %s",
                (int(pct), msg, datetime.now(), task_id))

    res = relabel_all(project_id, _prog)
    _prog(100, "完成：" + res["summary"])


def start_relabel(project_id: int) -> int:
    """启动全量重标后台任务，返回 task_id。"""
    from app.services.task_runner import task_runner

    return task_runner.submit("lot_relabel", project_id, relabel_all_task, project_id)


def rebuild_sections(project_id: int, apply: bool = False) -> dict:
    """重标章节索引的标段归属（规则快筛）。apply=False 仅统计不写库。"""
    idx = build_lot_index(project_id)
    rows = query(
        "SELECT id, sec_path, summary, applies_to, applies_lots FROM file_section_index"
        " WHERE project_id = %s",
        (project_id,),
    )
    stat = {"total": len(rows), "mine": 0, "other": 0, "multi": 0, "unknown": 0}
    from app.services.lot_service import selected_lot_code

    mine = selected_lot_code(project_id)
    samples: dict[str, list] = {"other": [], "multi": [], "unknown": [], "mine": []}
    for r in rows:
        text = f"{r['sec_path'] or ''} {r['summary'] or ''} {r['applies_to'] or ''}"
        lots, ev = judge_text(text, idx)
        if not lots:
            stat["unknown"] += 1
            key = "unknown"
        elif "multi" in lots:
            stat["multi"] += 1
            key = "multi"
        elif mine and mine in lots:
            stat["mine"] += 1
            key = "mine"
        else:
            stat["other"] += 1
            key = "other"
        if len(samples[key]) < 4:
            samples[key].append((r["sec_path"][:64], lots, ev[:60]))
        if apply:
            # 规则判不出、但已有判定（多半是 AI 判的）→ 保留，不要用"未判定"覆盖掉
            if not lots and parse_lots(r.get("applies_lots")):
                continue
            execute(
                "UPDATE file_section_index SET applies_lots = %s, lot_evidence = %s WHERE id = %s",
                (json.dumps(lots, ensure_ascii=False),
                 (ev + "（规则判定）")[:200], r["id"]),
            )
    return {"stat": stat, "samples": samples, "lots": len(idx)}


def rebuild_facts(project_id: int, apply: bool = False) -> dict:
    """重标事实表的标段归属（规则快筛）。"""
    idx = build_lot_index(project_id)
    rows = query(
        "SELECT id, fact_key, fact_value, applicable_lots FROM global_fact WHERE project_id = %s",
        (project_id,),
    )
    stat = {"total": len(rows), "mine": 0, "other": 0, "multi": 0, "unknown": 0}
    from app.services.lot_service import selected_lot_code

    mine = selected_lot_code(project_id)
    samples: dict[str, list] = {"other": [], "multi": [], "unknown": [], "mine": []}
    for r in rows:
        text = f"{r['fact_key'] or ''} {r['fact_value'] or ''}"
        lots, ev = judge_text(text, idx)
        if not lots:
            stat["unknown"] += 1
            key = "unknown"
        elif "multi" in lots:
            stat["multi"] += 1
            key = "multi"
        elif mine and mine in lots:
            stat["mine"] += 1
            key = "mine"
        else:
            stat["other"] += 1
            key = "other"
        if len(samples[key]) < 5:
            samples[key].append((f"{r['fact_key']} = {r['fact_value']}"[:70], lots, ev[:50]))
        if apply:
            # 规则判不出、但已有判定（多半是 AI 判的）→ 保留，不要用"未判定"覆盖掉
            if not lots and parse_lots(r.get("applicable_lots")):
                continue
            execute(
                # 未判定写 []（存疑），**不写 ["all"]**——"判断不出就标 all"正是老 bug 的根源
                "UPDATE global_fact SET applicable_lots = %s WHERE id = %s",
                (json.dumps(lots, ensure_ascii=False), r["id"]),
            )
    return {"stat": stat, "samples": samples, "lots": len(idx)}

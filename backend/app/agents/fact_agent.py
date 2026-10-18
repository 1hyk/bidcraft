"""事实抽取智能体（方案 3.9）：招标/指导性施组文档 → 结构化全局事实。

本模块现只提供**供逐章抽取复用的零件**：
- `_sanitize` / `_norm_lots`：单条事实的校验与规整；
- `_key_merge`：按 (category, fact_key) 合并去重（同 key 取置信度高者）；
- `_extract_json_array`：解析 LLM 返回的 JSON 数组；
- `DOC_TYPE_HINTS` / `CATEGORIES` / `MAX_FACTS_PER_CHUNK`：抽取侧重点与类别常量。

抽取的实际执行在 `services/file_index.build_index`（随建索引逐章抽取）与
`services/fact_service.reextract_facts_for_lot`（按标段定向重提取）。

**已删除（2026-09-11）**：原先的"整篇文档按 5000 字分块并发抽取"（`extract_facts` +
`SYSTEM_PROMPT`/`MERGE_PROMPT`/`_chunk_text`/`_cap_total`）——2026-09-07 改成逐章抽取后
就没有调用点了。改成逐章的原因：固定分块会切断章节语义单元、长文档容易漏抽或张冠李戴，
且出处只能给到"第 N 块"而非"文件 · 章节"。
"""
import json
import re

from app.agents.json_util import extract_json

# 每章/每块最多抽取条数（只挑编制决策必需的关键约束）
MAX_FACTS_PER_CHUNK = 8

# 事实类别（与全局事实表的分类筛选一致）
CATEGORIES = ["工期", "人员", "机械", "造价", "地质", "结构", "质量标准", "安全目标", "其他"]

# 不同文件类型的抽取侧重点（按 project_file.category 查，补充进抽取提示）
#
# 2026-10-15：原先这三条写的是**铁路土建**的侧重项——「设计荷载、设计时速、**轨道类型**」
# 「地质分层、**地下水位、承载力**」「**大临**布置原则」。它们随**每一次上传**拼进抽取提示词，
# 于是低空项目解析图纸时也会被问「轨道类型」。
# 这里改成**通用侧重项**：这张表问的是"这类文件该看什么"，本身与领域无关
# （图纸看参数、检测报告看条件、策划看安排），所以不改挂 `Domain`、只去掉具体领域的名词。
DOC_TYPE_HINTS = {
    "drawing": "\n- 本文件为图纸/示意图解析结果：重点提取图上的全局性设计参数与技术标准"
               "（技术指标、规格型号、结构形式、主要技术标准等）；"
               "零部件级明细尺寸仍不抽（属正文按需取用）。",
    "survey_report": "\n- 本文件为勘察/检测报告：重点提取影响方案的场地与自然条件、"
                     "环境限制、既有条件等关键约束。",
    "planning": "\n- 本文件为前期策划文件：重点提取工期目标、资源投入计划、总体部署原则等全局性安排。",
}


def _extract_json_array(text: str) -> list[dict]:
    """薄封装：统一走 json_util（带畸形修复）。"""
    data = extract_json(text, kind="array")
    return data if isinstance(data, list) else []


def _sanitize(f: dict) -> dict | None:
    """校验并规整单条事实，无效则丢弃。"""
    key = (f.get("fact_key") or "").strip()
    val = (f.get("fact_value") or "").strip()
    if not key or not val:
        return None
    cat = f.get("category") if f.get("category") in CATEGORIES else "其他"
    try:
        conf = float(f.get("confidence", 0.7))
    except (TypeError, ValueError):
        conf = 0.7
    return {
        "category": cat,
        "fact_key": key[:100],
        "fact_value": val[:200],
        "unit": (f.get("unit") or "")[:30],
        "source_location": (f.get("source_location") or "")[:200],
        "confidence": round(max(0.0, min(1.0, conf)), 2),
        "applicable_lots": _norm_lots(f.get("applicable_lots")),
    }


def _norm_lots(v) -> list[str]:
    """规整适用标段列表：空/非法一律按全线通用处理。"""
    if not isinstance(v, list):
        return ["all"]
    lots = [str(x).strip() for x in v if str(x).strip()]
    return (lots or ["all"])[:10]


def _key_merge(batches: list[list[dict]]) -> list[dict]:
    """程序化兜底合并：同 (category,fact_key) 取置信度最高。"""
    best: dict[tuple, dict] = {}
    for batch in batches:
        for f in batch:
            k = (f["category"], f["fact_key"])
            if k not in best or f["confidence"] > best[k]["confidence"]:
                best[k] = f
    return list(best.values())

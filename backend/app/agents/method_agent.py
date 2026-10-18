"""工法提炼智能体（M3-1 工艺工法库分支）：工法文档章节 → 结构化工法条目。

工艺工法库（method）与历史施组库（sod）走同一条 MinerU 解析管线，
但提炼目标不同：
- sod → 知识页：「这类章节怎么写」（写法参考）；
- method → 工法条目：「这道工序怎么做」（施工方法素材，方案 2.2）：
  工艺流程 process / 关键控制点 key_controls / 主要设备 equipment /
  质量控制 quality_points / 典型表格与图片。

消费方：编写"施工方案"章各专业子节时，按标题相似度匹配工法条目注入正文生成上下文。
入库：method_entry 表（name / applies_to / content JSON / status）。
"""
import json

from app.agents.knowledge_agent import _chunk_text, _extract_json
from app.core.domains import fill_genre
from app.services.llm import llm

# ⚠ 本模块的提炼入口（`extract_method_entry` / `METHOD_MERGE_PROMPT`）**全仓零调用点**：
#   工法条目现在由 `services/method_index.py`（`OVERVIEW_SYSTEM`，一次吃整篇文档）产出，
#   这条"逐章提炼 + 分块合并"的老路已被它取代。留在这里是因为删除要另行确认；
#   **但提示词里的体裁名词仍然按域填**（`{G_FULL}`）——万一将来有人把它接回去，也不该
#   让低空项目领一句"供后续编写施工组织设计"。
#   ⚠ 说清楚这个"按域填"的边界：只换了体裁名词，下面这段提示词**通篇仍是施工口径**
#   （"施工工序""施工所需""XX施工工艺"…）。**故意不去美化死代码**——它现在不被任何人
#   调用，改了也无从验证。**要接回去就先读这条**：把它按当前领域重写一遍，别再让它
#   给低空项目定施工的调。守护 `tests/verify_domain_prompt_isolation.py` 第 8 节会
#   断言本模块仍无调用方，一旦接线那条豁免立刻失效。
METHOD_SYSTEM = """你是资深工程技术专家。
你的任务是从给定的工艺工法文档章节原文中，提炼出一条结构化"工法条目"，
供后续编写{G_FULL}"施工方案"章节时直接取用施工方法素材。

要求：
1. 提炼【工艺流程 process】：施工工序的先后步骤（简短动宾短语，按顺序）；
2. 提炼【关键控制点 key_controls】：决定成败的控制要素（如"泥浆比重控制""导管埋深"）；
3. 提炼【主要设备 equipment】：施工所需的主要机械设备与仪器；
4. 提炼【质量控制 quality_points】：质量验收指标、允许偏差、检测方法等；
5. 保留章节的【典型表格】并做说明：
   - description：表格用途与维度说明（按什么组织、编制时注意什么）；
   - columns：逐列说明表头列需要填什么内容；
   - content_md：保留原表 Markdown（含表头与样例行）；
6. 保留章节的【图片引用】并说明：content 为图片内容描述（图上画了什么），usage 为用途；
7. 给出【适用场景 applies_to】：该工法适用的场景标签（如"基础施工""主体结构"），2-5 个；
8. 给出【适用条件 apply_condition】：一句话说明适用前提（地质/结构形式/环境等）；
9. 工法名称 name：规范命名，形如"XX施工工艺/XX施工工法"；
10. 原文中的工艺参数（如泥浆比重 1.1-1.3）属于工法本身的技术参数，可以保留在条目里
   （与历史项目事实不同，工法参数可跨项目复用）；
11. 只输出 JSON，不要任何解释性文字、不要 markdown 代码块围栏。

输出 JSON 结构：
{
  "name": "工法名称",
  "applies_to": ["适用场景标签"],
  "apply_condition": "适用前提一句话",
  "process": ["工序1", "工序2"],
  "key_controls": ["控制点1", "控制点2"],
  "equipment": ["设备1", "设备2"],
  "quality_points": ["质量要点1", "质量要点2"],
  "tables": [{"title": "表名", "description": "表格用途与维度说明", "columns": [{"name": "列名", "desc": "该列填什么"}], "content_md": "Markdown 表格"}],
  "images": [{"caption": "图名", "content": "图片内容描述", "usage": "用途说明"}]
}"""

METHOD_MERGE_PROMPT = """以下是同一工法章节分块提炼出的多份工法要点，请合并成一条完整工法条目。
要求：工艺流程按先后合并去重、控制点与质量要点合并同类项、表格图片不丢失。
只输出合并后的 JSON（结构同输入），不要任何解释性文字与代码块围栏。"""


def extract_method_entry(chapter_title: str, chapter_text: str,
                         model: str | None = None, domain: str = "") -> dict:
    """从工法章节文本提炼一条工法条目（长文本自动分块 + 合并）。

    `domain` 只用于填提示词里的体裁名词（见模块顶部那段的说明）；缺省＝没给。
    """
    chunks = _chunk_text(chapter_text)
    partials: list[dict] = []

    for i, chunk in enumerate(chunks):
        user_prompt = (
            f"工法章节标题（全路径）：{chapter_title}\n"
            f"{'（本段为长章节第 %d/%d 块）' % (i + 1, len(chunks)) if len(chunks) > 1 else ''}\n\n"
            f"章节原文：\n{chunk}"
        )
        raw = llm.chat(
            [
                {"role": "system", "content": fill_genre(METHOD_SYSTEM, domain)},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            temperature=0.2,
        )
        partials.append(_extract_json(raw))

    entry = partials[0]
    if len(partials) > 1:
        raw = llm.chat(
            [
                {"role": "system", "content": METHOD_MERGE_PROMPT},
                {"role": "user", "content": "待合并的工法要点：\n" + json.dumps(partials, ensure_ascii=False)},
            ],
            model=model,
            temperature=0.2,
        )
        entry = _extract_json(raw)

    entry.setdefault("name", chapter_title.split(" > ")[-1])
    for key in ("applies_to", "process", "key_controls", "equipment", "quality_points", "tables", "images"):
        entry.setdefault(key, [])
    entry.setdefault("apply_condition", "")
    return entry

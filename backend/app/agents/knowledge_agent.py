"""知识提炼智能体（M5-2, P0）：章节正文 → 结构化知识页。

要点（方案 3.2 + 用户新决策）：
- 不做章节类型分类：知识页按「标题全路径」标识（如"工程概况 > 2.3 主要工程内容和数量"），
  后续正文生成按标题路径匹配引用；专业标签 tags 保留（专业维度仍有用）；
- 提炼的是"编制方式、方法、要点、典型表格/图"——即"这类章节怎么写"；
- 页内历史数字打 fact_role=reference，与本项目事实区分（不进全局事实表）；
- 单章 10 万字内不分块（模型上下文实测 40 万字符 = 23 万 token 足够），
  超长章节分块提炼后再合并。
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.agents.json_util import chat_json, extract_json
from app.core.domains import fill_genre, tags_of
from app.services.llm import llm

# 第二层：专业标签——按领域取词表（app/core/domains.py）。
# 原先此处硬编码铁路 14 词，低空文档提炼时提示词给的仍是铁路词，模型只能往最泛的
# 「信息」上靠：实测 837 页里 68% 被贴同一个标签，三级漏斗第一级过滤形同虚设。

# 单章 10 万字内不分块（qwen3.8-flash 上下文实测充足）；超过才分块+合并
CHUNK_SIZE = 100_000
CHUNK_OVERLAP = 2_000

SYSTEM_PROMPT = """你是资深{G_INTRO}编制专家。
你的任务是从给定的历史{G_SHORT}章节原文中，提炼出"这类章节应该怎么写"的可复用知识页。

给你的章节标题是全路径形式（如"工程概况 > 2.3 主要工程内容和数量"），
它体现了章节在文档中的层级归属。

要求：
1. 提炼【编制方式与方法】：该章节的组织顺序、内容框架、常用写法，而不是简单摘抄原文；
2. 提炼【要点清单】：该章节必须覆盖的内容点（供后续生成时对照）；
3. 保留该章节的【典型表格】，并对表格做编制经验说明：
   - description：这张表用于表达什么、按什么维度组织（如"按工点×月份组织"）、编制时注意什么；
   - columns：逐列说明每个表头列需要填什么内容（口径、单位、常见取值）；
   - content_md：保留原表 Markdown（含表头与样例行，作为填写样例）；
4. 保留该章节的【图片引用】并说明：
   - caption：**图名**。原文里这张图的图题就是图名（正文常写成"图 3-2 系统总体架构图"，
     图名取"系统总体架构图"，图号不要）；图题一般在图的前一段或后一段文字里，找一下。
     **解析器已经帮你在图片引用里标好了图题**——`![无人机实飞测试现场（一）](docx_0002.png)`
     方括号里的就是这个文件的图题，**照抄即可**（方括号里是"图"的表示原文没有图题）。
     **原文确实没有图题的，照 content 自己起一个 6~16 字、一眼能看懂的短名**
     （如"系统总体架构图""测试执行流程图"）——宁可自己起名，也不要留空。
     **绝对不要把文件名当图名**：`![...](docx_0009.png)` 圆括号里的 `docx_0009.png`
     是文件名、不是图名，写了它这张图在后续引用里就等于没有名字；
   - content：图片内容描述（图上画了什么、展示了哪些信息，如"总平面布置图，含区域划分与通道走向"）；
   - usage：用途说明（编写本章时这张图用来支撑什么内容）；
5. 标注【适用范围】：说明什么情况下应参考本知识页（工程类型、章节场景），供 AI 判断何时引用；
6. 专业标签从标签列表中选择（可多选，无则空数组）；
7. 给出【粗分类 category】：自由命名一个短语概括该章节属于哪类内容
   （如"进度计划""质量管理""资源配置"），用于匹配时缩小候选范围，不要求从固定列表选；
8. 原文中出现的具体数字（工期、人数、机械数量、造价等）属于历史参考值，
   必须在 numbers 中列出并标记 fact_role="reference"，表示不可当作新项目的事实直接引用；
9. 按本知识页实际内容标注 service_types（测试/开发/集成/运维/咨询/产品，可多选）和
   chapter_purposes（需求分析/设计开发/测试验收/集成部署/项目管理/培训交付/运维保障，可多选）。
   不按整册标书或父章推断；开发标书中的测试章节标为测试。无法确定时用空数组。
10. 只输出 JSON，不要任何解释性文字、不要 markdown 代码块围栏。

输出 JSON 结构：
{
  "title": "知识页标题（章节名）",
  "category": "粗分类短语",
  "service_types": [],
  "chapter_purposes": [],
  "tags": ["专业标签"],
  "method": "编制方式与方法说明（200-600字）",
  "key_points": ["要点1", "要点2"],
  "tables": [{"title": "表名", "description": "表格用途与维度说明", "columns": [{"name": "列名", "desc": "该列填什么"}], "content_md": "Markdown 表格"}],
  "images": [{"caption": "图名", "content": "图片内容描述", "usage": "用途说明"}],
  "numbers": [{"name": "工期", "value": "540", "unit": "天", "fact_role": "reference"}],
  "usage": "适用范围说明（什么情况下参考本页）"
}"""

MERGE_PROMPT = """以下是同一章节分块提炼出的多份知识要点，请将它们合并成一份完整的知识页。
要求：去重、保持方法说明连贯、要点合并同类项、表格与图片引用不丢失。
只输出合并后的 JSON（结构同输入），不要任何解释性文字与代码块围栏。"""


@dataclass
class KnowledgePage:
    title: str
    tags: list[str]
    method: str
    key_points: list[str]
    tables: list[dict]
    images: list[dict]
    numbers: list[dict]
    usage: str
    source: dict

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "tags": self.tags,
            "method": self.method,
            "key_points": self.key_points,
            "tables": self.tables,
            "images": self.images,
            "numbers": self.numbers,
            "usage": self.usage,
            "source": self.source,
        }


def _extract_json(text: str) -> dict:
    """从 LLM 输出中稳健提取 JSON（统一走 json_util，兼容围栏/前后废话/常见畸形）。"""
    return extract_json(text)


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks, pos = [], 0
    while pos < len(text):
        chunks.append(text[pos : pos + size])
        pos += size - overlap
    return chunks


def extract_knowledge_page(
    chapter_title: str,
    chapter_text: str,
    doc_name: str,
    source_location: str = "",
    model: str | None = None,
    domain: str | None = None,
) -> dict:
    """从章节文本提炼知识页（长文本自动分块 + 合并）。

    domain 决定提示词里给模型哪套专业标签词表（见 app/core/domains.py）。
    """
    chunks = _chunk_text(chapter_text)
    partials: list[dict] = []

    for i, chunk in enumerate(chunks):
        user_prompt = (
            f"历史{{G_SHORT}}文档：{doc_name}\n"
            f"章节标题（全路径）：{chapter_title}\n"
            f"可选专业标签：{list(tags_of(domain))}\n"
            f"{'（本段为长章节第 %d/%d 块）' % (i + 1, len(chunks)) if len(chunks) > 1 else ''}\n\n"
            f"章节原文：\n{chunk}"
        )
        partials.append(
            chat_json(
                [
                    {"role": "system", "content": fill_genre(SYSTEM_PROMPT, domain)},
                    {"role": "user", "content": fill_genre(user_prompt, domain)},
                ],
                model=model,
            )
        )

    page = partials[0]
    if len(partials) > 1:
        page = chat_json(
            [
                {"role": "system", "content": MERGE_PROMPT},
                {
                    "role": "user",
                    "content": "待合并的知识要点：\n" + json.dumps(partials, ensure_ascii=False),
                },
            ],
            model=model,
        )

    page.setdefault("title", chapter_title)
    page.setdefault("tags", [])
    page.setdefault("category", "")
    page["source"] = {
        "doc": doc_name,
        "chapter": chapter_title,
        "location": source_location,
        "chunks": len(chunks),
    }
    for key in ("key_points", "tables", "images", "numbers"):
        page.setdefault(key, [])
    page.setdefault("method", "")
    page.setdefault("usage", "")
    from app.services.knowledge_applicability import classify
    page.update(classify(chapter_title, page))
    return page


def save_knowledge_page(page: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path

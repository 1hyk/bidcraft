"""公司口径：交付与验收流程照常写，但不出现任何具体交付文件。

对外投标文件只描述过程、动作、控制点和验收安排，不写文件名、份数、交付物清单。
生成、改写、提纲、落库和导出统一走这里，避免某一条链路把文件名又带回来。

与 customer_voice.strip_internal_notes 不同：这里连 Mermaid/代码块一起处理，
因为流程图标签里的“测试报告”等同样属于文件名式表述。

开关：BIDCRAFT_HIDE_DELIVERABLE_FILES=0/false/off/no 可关闭；默认开启。
"""
import os
import re

PROMPT = """公司对外文件口径（必须遵守）：
交付与验收流程要写完整，但全文不得出现任何具体交付文件、文件名、份数或交付物清单。
1. 只写过程、动作、控制点和验收安排，例如“形成测试策划并提交确认”“执行过程与证据可追溯”“形成结果分析与验收结论”“完成阶段成果移交并登记”。
2. 不得出现“测试方案、测试用例、测试执行记录、测试报告、验收大纲、验收报告、交付物、交付文件、文档、清单”等文件名式表述。需要指代时改用“测试策划、测试设计、执行过程与证据、结果分析与结论、验收安排、验收结论、过程资料、台账”等过程性表述。
3. 不得写“提交/提供/移交 X 份 X 文件”，不得列交付物清单、文件目录或份数；可以写“按采购人确认的形式提交阶段成果”。
4. 不得用《》括起上述名称；表格、流程图、图注、目录标题中同样不得出现。
5. 招标目录或格式中列有交付文件名时，保留其位置和工作内容，但标题与正文改为过程性表述。
6. 交付批次、内部评审、预验收、书面异议闭环、终验、归档、知识产权和付款节点仍须写清楚。"""

_OFF = {"0", "false", "off", "no", "n"}


def enabled() -> bool:
    try:
        from app.core.config import settings

        return bool(settings.bidcraft_hide_deliverable_files)
    except Exception:  # noqa: BLE001 独立运行/配置未加载时退回环境变量
        return os.environ.get("BIDCRAFT_HIDE_DELIVERABLE_FILES", "1").strip().lower() not in _OFF


def prompt_suffix() -> str:
    return ("\n\n" + PROMPT) if enabled() else ""


# 先处理具体名称，再处理泛称；顺序有影响。
_SPECIFIC = (
    ("验收大纲及验收报告", "验收安排与结论"),
    ("验收大纲及报告", "验收安排与结论"),
    ("系统测试执行记录", "测试执行过程及证据"),
    ("测试执行记录", "测试执行过程及证据"),
    ("系统测试方案", "测试策划成果"),
    ("系统测试用例", "测试设计成果"),
    ("系统测试报告", "测试结果分析与结论"),
    ("验收大纲", "验收组织安排"),
    ("验收报告", "验收结论及签署"),
    ("测试大纲", "测试策划要求"),
    ("测试方案", "测试策划"),
    ("测试用例", "用例"),
    ("测试计划", "测试策划"),
    ("初验大纲", "初验安排"),
    ("终验大纲", "终验安排"),
    ("测试报告", "测试结果分析"),
    ("执行记录", "执行过程数据"),
    ("交付物清单", "成果说明"),
    ("交付清单", "移交记录"),
    ("文件清单", "资料目录"),
    ("报告模板", "结果呈现方式"),
    ("记录模板", "记录格式"),
    ("验收交付物", "对应验收条款"),
    ("交付文件", "成果"),
    ("交付物", "成果"),
    ("正式文档", "正式成果"),
    ("正式文件", "正式成果"),
    ("资料包", "阶段成果"),
    ("正式稿", "最终成果"),
    ("文件名称", "成果名称"),
    ("可编辑文件", "可编辑版本"),
    ("签章版", "确认版"),
    ("签署文件", "签署材料"),
    ("归档文件", "归档资料"),
    ("分析报告", "分析结果"),
    ("性能分析报告", "性能分析结果"),
    ("复测报告", "复测结果"),
    ("巡检报告", "巡检记录"),
    ("故障报告", "故障处置记录"),
    ("总结报告", "结果分析"),
)

# 个别词里的“报告”不是文件（如报告厅），先保护再还原。
_SAFE = (
    ("报告厅", "@@SAFE0@@"),
    ("报告期", "@@SAFE1@@"),
)

_COUNTS = (
    ("五项验收交付物", "各项测试成果"),
    ("五项主要交付物", "各项测试成果"),
    ("五项交付物", "各项测试成果"),
    ("五类交付物", "各类测试成果"),
    ("5项交付物", "各项测试成果"),
    ("5类交付物", "各类测试成果"),
    ("五项验收成果", "各项测试成果"),
    ("五项成果", "各项成果"),
    ("5项验收成果", "各项测试成果"),
    ("5项成果", "各项成果"),
    ("六份正式文档", "全套测试成果"),
    ("六份正式文件", "全套测试成果"),
    ("正式文档", "正式成果"),
    ("正式文件", "正式成果"),
    ("数量均为1份", ""),
    ("每项1份", ""),
    ("各 1 份", ""),
    ("各1份", ""),
    ("各一份", ""),
)

# 兜底：剩余“文档”“清单”“模板”等改为过程性表述。“报告”不全局替换——
# 第三方检测报告等是外部证据，不是我方交付文件；只处理“提交/编制/出具……报告”。
_GENERIC = (
    ("文档", "资料"),
    ("清单", "台账"),
    ("模板", "格式"),
    ("五项", "各项"),
    ("六份", "全套"),
)

# “提交报告”“编制检测报告”这类把报告当成交付动作宾语的表述。
_REPORT_ACTION = re.compile(
    r"(提交|提供|移交|交付|编制|出具|形成|归档|装订|签署|盖章|输出|产出|报送)"
    r"([^。；;，,\n]{0,10}?)报告"
)

# 生成“结果分析/测试成果”后可能出现的重复与断句问题。
_CLEANUP = (
    ("测试策划要求、测试策划", "测试策划"),
    ("测试策划要求、方案及用例", "测试策划及用例"),
    ("测试策划要求、方案", "测试策划"),
    ("测试策划要求、", "测试策划、"),
    ("测试策划要求", "测试策划"),
    ("测试策划和测试策划", "测试策划"),
    ("测试策划、测试策划", "测试策划"),
    ("测试策划、方案及用例", "测试策划及用例"),
    ("测试策划、方案", "测试策划"),
    ("分析分析", "分析"),
    ("结果分析分析", "结果分析"),
    ("测试策划策划", "测试策划"),
    ("执行过程数据数据", "执行过程数据"),
    ("成果成果", "成果"),
    ("资料资料", "资料"),
    ("用例用例", "用例"),
)

# 引用采购文件条款时保留可定位的条款号，但不保留条款标题里的文件名：
# 附件1“2.2 验收交付物” → 附件1第2.2条；采购人仍能按编号找到原条款。
_CITATION_NESTED = re.compile(
    r"[“\"]\s*(附件\s*\d+)\s+(\d+(?:\.\d+)+)[^”\"\n]{0,40}[”\"]"
)
_CITATION_CLAUSE = re.compile(
    r"(附件\s*\d+)\s*[“\"]\s*(\d+(?:\.\d+)+)[^”\"\n]{0,40}[”\"]"
)
_CITATION_UNQUOTED = re.compile(r"(附件\s*\d+)\s+(\d+(?:\.\d+)+)\s*验收交付物")
_CITATION_BARE = re.compile(r"[“\"]\s*(\d+(?:\.\d+)+)\s*验收交付物\s*[”\"]")


def _rewrite_citations(text: str) -> str:
    text = _CITATION_NESTED.sub(r"\1第\2条", text)
    text = _CITATION_CLAUSE.sub(r"\1第\2条", text)
    text = _CITATION_UNQUOTED.sub(r"\1第\2条", text)
    text = _CITATION_BARE.sub(r"第\1条", text)
    return text


_STRONG_BANNED = re.compile(
    r"系统测试方案|系统测试用例|系统测试执行记录|系统测试报告|验收大纲|验收报告|"
    r"测试大纲|测试方案|测试用例|测试报告|执行记录|交付物|交付文件|交付清单|"
    r"资料包|正式稿|初验大纲|终验大纲"
)
_SOFT_BANNED = re.compile(r"文档|清单")


def _clean(text: str) -> str:
    text = _rewrite_citations(text)
    for old, new in _SAFE:
        text = text.replace(old, new)
    for old, new in _COUNTS:
        text = text.replace(old, new)
    for old, new in _SPECIFIC:
        text = text.replace(old, new)
    text = _REPORT_ACTION.sub(r"\1\2结果分析", text)
    text = text.replace("文档化", "书面化").replace("清单化", "条目化").replace("文件化", "书面化")
    # 《测试策划成果》这类书名号去掉，名称本身已泛化；相邻书名号之间补顿号。
    text = re.sub(r"》\s*(?=《)", "》、", text)
    text = re.sub(r"《\s*([^《》]{1,30})\s*》", r"\1", text)
    for old, new in _GENERIC:
        text = text.replace(old, new)
    for old, new in _SAFE:
        text = text.replace(new, old)
    for old, new in _CLEANUP:
        text = text.replace(old, new)
    # 多个泛称并列时收敛，避免“测试成果、各项测试成果”式堆砌。
    text = re.sub(r"(?:相应|各项|各类)?测试成果(?:[、，,及和与]?各项测试成果)+", "相应测试成果", text)
    text = re.sub(r"([、，,；;])\s*[、，,；;]+", r"\1", text)
    text = re.sub(r"[，,；;]\s*。", "。", text)
    text = re.sub(r"。\s*。+", "。", text)
    text = re.sub(r"及\s*及", "及", text)
    text = re.sub(r"与\s*及", "及", text)
    text = re.sub(r"[（(]\s*[）)]", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def sanitize_text(text: str) -> str:
    """把正文里的具体交付文件名改成过程性表述；关闭开关时原样返回。"""
    if not enabled():
        return str(text or "")
    value = str(text or "")
    if not value:
        return value
    # 正文空白清洗不能触及源码：mindmap 的缩进就是父子关系。
    parts=re.split(r'(```[\s\S]*?```|<pre\b[\s\S]*?</pre>|<[^>]*\bdata-code\s*=\s*(?:"[^"]*"|\'[^\']*\')[^>]*>)',value,flags=re.I)
    return ''.join(part if (part.startswith('```') or re.match(r'<(?:pre\b|[^>]*\bdata-code\s*=)',part,re.I)) else _clean(part) for part in parts)


# 这些键参与目录匹配或与数据库标题对齐，改了会让思路挂错节点，保持原样。
# chapter_title 不在其中：它只进编写提示，清洗后能防止写手按“交付物清单”旧题展开。
_STRUCTURAL_KEYS = {"_path", "_toc_title", "title"}


def sanitize_obj(obj):
    """递归清洗提纲等 JSON 结构里的字符串值（键名和结构标题不动）。"""
    if not enabled():
        return obj
    if isinstance(obj, str):
        return sanitize_text(obj)
    if isinstance(obj, list):
        return [sanitize_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {
            key: (value if key in _STRUCTURAL_KEYS else sanitize_obj(value))
            for key, value in obj.items()
        }
    return obj


def violations(text: str) -> list[str]:
    """返回仍存在的文件名式表述，供测试和巡检使用。"""
    value = _rewrite_citations(str(text or ""))
    found = _STRONG_BANNED.findall(value)
    found += _SOFT_BANNED.findall(value)
    return sorted(set(found))

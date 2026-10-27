"""对外正文口吻：内部审阅提示和 AI 过程用语不得进入成稿。

成稿是给采购人看的正式文件。“供人工核对”“建议值，请确认”“拟议优化安排”这类话
是写给内部编写人的，出现在正文里会让人一眼看出不是人写的。生成、改写、落库前
统一过一遍；保留 Mermaid/代码块原文，避免误改图内标签。
"""
import re

# 括号里的整段内部提示：连括号一起删，避免留下空括号。
_INTERNAL_PAREN = re.compile(
    r'[（(]\s*[^（）()]{0,40}?'
    r'(?:供人工核对|请人工核对|待人工核对|请确认|建议值)'
    r'[^（）()]{0,40}?\s*[）)]'
)

# 先处理固定搭配，再处理残留词；顺序有影响。
_REPLACEMENTS = (
    ('拟议优化安排', '优化安排'),
    ('建议性实施安排', '实施安排'),
    ('该项属于我方实施安排，', ''),
    ('该项属于我方实施安排', ''),
    ('作为我方拟议安排供人工核对', '按我方实施安排执行'),
    ('作为我方拟议安排', '按我方实施安排'),
    ('属于本项目拟议安排', '按本项目实施需要安排'),
    ('作为服务实施安排供双方核对', '按服务实施安排执行'),
    ('供双方核对', '与采购人确认'),
    ('供人工核对', ''),
    ('请人工核对', ''),
    ('供核对', ''),
    ('拟议频次', '巡检频次'),
    ('拟议评审', '方案评审'),
    ('拟定时长', '课程时长'),
    ('按上述建议值执行', '按上述测试基线执行'),
    ('形成测试建议值', '形成测试基线'),
    ('形成建议值', '形成测试基线'),
    ('建议值', '测试基线'),
    ('，不虚构未确认信息', '。'),
    ('不虚构未确认信息', ''),
    ('，请确认', ''),
    ('拟议', ''),
    ('建议性', ''),
)


def _clean(text: str) -> str:
    text = _INTERNAL_PAREN.sub('', text)
    for old, new in _REPLACEMENTS:
        text = text.replace(old, new)
    text = re.sub(r'[（(]\s*[）)]', '', text)
    text = re.sub(r'[ \t]+([，,。；;：:])', r'\1', text)
    text = re.sub(r'([，,。；;])\s*[，,。；;]+', r'\1', text)
    text = re.sub(r'([，,；;])\s*。', '。', text)
    text = re.sub(r'。\s*。+', '。', text)
    return text


def strip_internal_notes(text: str) -> str:
    """删除正文里的内部审阅提示；代码块/<pre> 原样保留。"""
    value = str(text or '')
    if not value:
        return value
    parts = re.split(r'(```[\s\S]*?```|<pre\b[\s\S]*?</pre>)', value, flags=re.I)
    return ''.join(
        part if part.startswith('```') or re.match(r'<pre\b', part, re.I) else _clean(part)
        for part in parts
    )

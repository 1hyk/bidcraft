"""正文待处理标记及内部引用的共享检查，不推断公司事实。

内部要求编号（如 #625）只用于系统内部关联，绝不能进入成稿。模型从提示词里抄下来的
编号既不可审阅，也会在换项目后指向不存在的要求；正文一律改写成招标原文位置。
"""
import re

_MARKERS = re.compile(r'\[(?:待补充|待确认)[^\]\n]{0,160}\]|【(?:待补充|待确认)[^】\n]{0,160}】|[（(]\s*(?:待确认|待补充)(?:\s*[：:][^）)\n]{1,120})?\s*[）)]')

_REF_LABEL = r'(?:招标要求|要求条目|需求条目)'
_SEP = r'(?:[、,，]|及|和|与|[-–—~～]|至)'
# 一条引用可以带“招标要求/要求条目/需求条目”前缀，也可以是裸 #625；
# 表格里常见的 “#625, #626, #627” 或 “#625-#631” 视为同一条引用串，整体改写后再去重。
_REF_RUN = re.compile(
    r'(?:'
    r'(?:' + _REF_LABEL + r')\s*#\s*\d+(?![A-Za-z0-9_])'
    # 裸编号至少三位：Mermaid 颜色 #333/#2e7d32、清单序号 #1 不能被误判。
    r'|(?<![#A-Za-z0-9_/&])\s*#\s*\d{3,}(?![A-Za-z0-9_])'
    r')'
    r'(?:\s*' + _SEP + r'\s*(?:' + _REF_LABEL + r'\s*)?#\s*\d+(?![A-Za-z0-9_]))*'
)
_REF_PART = re.compile(
    r'(?:(?P<label>' + _REF_LABEL + r')\s*)?#\s*(?P<id>\d+)'
    r'(?P<sep>\s*' + _SEP + r')?'
)
# 代码、HTML 标签/样式块里的 #333 可能是颜色值，不是要求编号；检查与替换都跳过。
# 先整块屏蔽代码围栏，再处理行内代码和标签——单个反引号紧贴围栏时不能把围栏吃掉。
_FENCE = re.compile(r'(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$')
_BLOCK = re.compile(r'(?is)<(pre|code|style|script)\b.*?</\1>')
_TAG = re.compile(r'<[^>]+>')
_INLINE_CODE = re.compile(r'`[^`\n]+`')
# HTML 属性值可能含裸 >（Mermaid 的 A --> B），_TAG 会在箭头处提前结束，
# 把后面的 stroke:#333 漏进正文检查。data-code 单独按引号成对整段屏蔽。
_DATA_CODE = re.compile(r'\bdata-code\s*=\s*(?:"[^"]*"|\'[^\']*\')', re.I | re.S)
# 无围栏的 Mermaid 也可能进正文：style/fill/stroke 后的 #333 是颜色而非要求编号。
# 只认“属性名: #值”这种明确颜色位置，不放过正文里真正的裸编号 #333。
_MERMAID_COLOR = re.compile(
    r'(?i)\b(?:fill|stroke|color|background(?:-color)?|border(?:-color)?)'
    r'\s*[:=]\s*#[0-9a-f]{3,8}\b'
    r'(?:\s*[,;]\s*(?:fill|stroke|color|background(?:-color)?|border(?:-color)?)'
    r'\s*[:=]\s*#[0-9a-f]{3,8}\b)*'
)
_MERMAID_STYLE_LINE = re.compile(r'(?im)^[ \t]*style\s+\S.*$')
# 无围栏 Mermaid 的连线/节点行同样属于图源；行内出现的 #625 不能当正文引用改掉。
_MERMAID_EDGE_LINE = re.compile(
    r'(?m)^[ \t]*[^\n]*(?:-->|---|==>|-\.->|--x|--o|->>|-->>)[^\n]*$'
)
_UNRESOLVED = '【待补充：采购条款】'


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []

    def add(pattern: re.Pattern):
        for match in pattern.finditer(text):
            if not any(start < match.end() and match.start() < end for start, end in spans):
                spans.append((match.start(), match.end()))

    for pattern in (
        _FENCE, _BLOCK, _DATA_CODE, _TAG, _INLINE_CODE,
        _MERMAID_STYLE_LINE, _MERMAID_EDGE_LINE, _MERMAID_COLOR,
    ):
        add(pattern)
    return sorted(spans)


def _segments(text: str):
    """按受保护片段切分，yield (是否受保护, 片段)。"""
    text = text or ''
    last = 0
    for start, end in _protected_spans(text):
        if start > last:
            yield False, text[last:start]
        yield True, text[start:end]
        last = end
    if last < len(text):
        yield False, text[last:]


def _is_markdown_heading(text: str, match: re.Match) -> bool:
    """`# 625` 独占一行是 Markdown 标题（章节编号），不是内部要求编号。"""
    line_start = text.rfind('\n', 0, match.start()) + 1
    line_end = text.find('\n', match.start())
    if line_end < 0:
        line_end = len(text)
    before = text[line_start:match.start()]
    line = text[line_start:line_end]
    return not before.strip() and bool(re.fullmatch(r'#\s*\d+\s*', line))


def _reference_runs(text: str):
    for match in _REF_RUN.finditer(text):
        if not _is_markdown_heading(text, match):
            yield match


def pending_markers(text):
    return _MARKERS.findall(text)


def internal_references(text):
    """返回正文里仍在使用的内部编号（裸编号也算），已排除代码和 HTML 属性。"""
    found = []
    for protected, part in _segments(text):
        if protected:
            continue
        found.extend(match.group(0).strip() for match in _reference_runs(part))
    return list(dict.fromkeys(found))


def _join(sep: str | None) -> str:
    sep = (sep or '').strip()
    if sep in {'及', '和', '与'}:
        return sep
    if sep in {'-', '–', '—', '~', '～', '至'}:
        return '至'
    return '、'


def _rewrite_run(run: str, sources: dict[str, str]) -> str:
    head = run[:len(run) - len(run.lstrip())]  # 裸编号匹配会吞掉前导空格，原样还回去
    entries = []  # (改写后的引用, 其后的连接词)
    for part in _REF_PART.finditer(run):
        location = sources.get(part['id'])
        text = f'采购文件“{location}”' if location else _UNRESOLVED
        entries.append([text, part['sep']])

    # 七条要求同属“附件1 2.1.1 功能要求”时只保留一处，避免表格里重复七遍。
    kept: list[list[str | None]] = []
    seen = set()
    for text, sep in entries:
        if text in seen:
            if kept:
                kept[-1][1] = sep or kept[-1][1]
            continue
        seen.add(text)
        kept.append([text, sep])

    if not kept:
        return run
    out = kept[0][0]
    for index in range(1, len(kept)):
        out += _join(kept[index - 1][1]) + kept[index][0]
    return head + out


def resolve_requirement_refs(body, requirements):
    """把内部要求编号改写为招标原文位置；当前项目查不到的编号留待人工核对。"""
    if not body:
        return body
    sources = {}
    for row in requirements:
        if row.get('id') is None:
            continue
        location = re.sub(
            r'\s+', ' ', str(row.get('source_location') or row.get('title') or '')
        ).strip()
        if location:
            sources[str(row['id'])] = location

    def clean(text):
        out, last = [], 0
        for match in _reference_runs(text):
            chunk = text[last:match.start()]
            # 原文写成“采购文件“附件…”-#634”时，悬空连字符改成顿号。
            chunk = re.sub(r'(?<=”)\s*[-–—~～]\s*$', '、', chunk)
            out.append(chunk)
            out.append(_rewrite_run(match.group(0), sources))
            last = match.end()
        out.append(text[last:])
        return ''.join(out)

    return ''.join(part if protected else clean(part)
                   for protected, part in _segments(body))


def clean_requirement_refs(body, project_id):
    """成稿落库/导出前的统一入口：只有真的出现内部编号时才查库。"""
    if not body or not internal_references(body):
        return body
    from app.services.compilation_requirements import load_requirements
    return resolve_requirement_refs(body, load_requirements(project_id))

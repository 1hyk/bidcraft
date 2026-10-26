"""修复已声明的时序图参与者 ID 与 Mermaid 保留词 link 的冲突，不改图中文字。"""
import html
import re


def normalize(source: str) -> str:
    if re.match(r'^\s*gantt\b',source):
        # 已声明任务被用作里程碑起点时，补齐明确的 after 引用；不改日期或工期。
        ids=set(re.findall(r':\s*(?:active\s*,\s*|crit\s*,\s*|done\s*,\s*)*([A-Za-z_]\w*)\s*,',source))
        def milestone(match):
            return match[1]+'after '+match[2]+match[3] if match[2] in ids else match[0]
        return re.sub(r'(:\s*milestone\s*,\s*)([A-Za-z_]\w*)(\s*,\s*0d\b)',milestone,source)
    if not re.match(r'^\s*sequenceDiagram\b', source):
        return source
    declarations = re.findall(r'^\s*(?:participant|actor)\s+(\w+)\b', source, re.M)
    names = set(declarations)
    changes = {}
    for name in declarations:
        if name.lower() != 'link':
            continue
        replacement = 'actor_' + name
        while replacement in names:
            replacement += '_'
        names.add(replacement)
        changes[name] = replacement
    if not changes:
        return source
    lines = []
    for line in source.splitlines(keepends=True):
        declaration = re.match(r'^(\s*(?:participant|actor)\s+)(\w+)(.*?)(\r?\n)?$', line)
        if declaration and declaration[2] in changes:
            tail = declaration[3]
            if not tail.strip():
                tail = ' as ' + declaration[2]
            lines.append(declaration[1] + changes[declaration[2]] + tail + (declaration[4] or ''))
            continue
        # 冒号后的标签以及注释/分支说明一字不动，仅替换语法位置的参与者。
        head, sep, label = line.partition(':')
        if re.match(r'^\s*(?:Note\s+(?:over|right of|left of)|activate\s|deactivate\s|destroy\s)', head, re.I) or re.search(r'(?:->|-->|-x|--x|-\)|--\))', head):
            for old, new in changes.items():
                head = re.sub(r'\b' + re.escape(old) + r'\b', new, head)
        lines.append(head + sep + label)
    return ''.join(lines)


def split_mindmap(source: str, limit: int = 14) -> list[str]:
    """仅拆分可确定解析的缩进树，整支搬运；未知语法、单支过大均保持原样。"""
    lines=source.strip().splitlines()
    if not lines or lines[0].strip()!='mindmap': return [source]
    nodes=[line for line in lines[1:] if line.strip()]
    if len(nodes)<=limit or any('\t' in line or re.search(r'::|^\s*%%|^\s*icon\b',line) for line in nodes):
        return [source]
    indent=lambda line:len(line)-len(line.lstrip(' '))
    root=nodes[0]
    if any(indent(line)<=indent(root) for line in nodes[1:]):return [source]
    branch_depth=min(indent(line) for line in nodes[1:])
    branches=[]
    for line in nodes[1:]:
        if indent(line)==branch_depth:branches.append([])
        if not branches:return [source]
        branches[-1].append(line)
    if any(len(branch)+1>limit for branch in branches):return [source]
    groups=[];group=[]
    for branch in branches:
        if group and len(group)+len(branch)+1>limit:
            groups.append(group);group=[]
        group.extend(branch)
    if group:groups.append(group)
    return ['mindmap\n'+root+'\n'+'\n'.join(group)+'\n' for group in groups]


_CAPTION_RE = re.compile(
    r'(?m)^[ \t]*(?:\*\*|__)?[ \t]*图\s*(?:\d+(?:[-–—.]\d+)*)?\s*[:：].*$'
)


def _pull_captions(source: str) -> tuple[str, list[str]]:
    """图名属于正文，不属于 Mermaid 语法；缩进为 0 的图名会被 mindmap 当成第二个根节点。"""
    captions = []

    def take(match):
        captions.append(match.group(0).strip().strip('*_').strip())
        return ''

    return _CAPTION_RE.sub(take, source), captions


def strip_captions(source: str) -> str:
    """单图候选只有源码，没有正文容器；图名行直接丢弃。"""
    return _pull_captions(source)[0].strip()


_FENCE_OPEN_LINE = re.compile(r'^[ \t]*```[ \t]*mermaid\b[^\n]*$', re.I)
_FENCE_CLOSE_LINE = re.compile(r'^[ \t]*```[ \t]*$')
_CAPTION_TEXT_RE = re.compile(r'^图\s*(?:\d+(?:[-–—.]\d+)*)?\s*[:：].*$')
_SPLIT_LABEL_LINE = re.compile(r'^[ \t]*（?关系图分图\s*\d+\s*/\s*\d+）?[ \t]*$')
_EMPTY_MD_HEADING = re.compile(r'^\s{0,3}#{1,6}\s*$')


def _md_caption_text(line: str) -> str | None:
    text = re.sub(r'^\s{0,3}#{1,6}\s*', '', line).strip().strip('*_').strip()
    return text if _CAPTION_TEXT_RE.match(text) else None


def _md_blocks(lines: list[str]) -> list[tuple[int, int]]:
    blocks = []
    i = 0
    while i < len(lines):
        if _FENCE_OPEN_LINE.match(lines[i]):
            j = i + 1
            while j < len(lines) and not _FENCE_CLOSE_LINE.match(lines[j]):
                j += 1
            if j < len(lines):
                blocks.append((i, j))
                i = j + 1
                continue
        i += 1
    return blocks


def _md_groups(lines: list[str]) -> list[list[tuple[int, int]]]:
    """自动分图产生的连续代码块算一张图，图名只写在最后一块之后。"""
    groups: list[list[tuple[int, int]]] = []
    for block in _md_blocks(lines):
        if groups:
            between = lines[groups[-1][-1][1] + 1:block[0]]
            if (any(_SPLIT_LABEL_LINE.match(line) for line in between)
                    and all(not line.strip() or _SPLIT_LABEL_LINE.match(line) for line in between)):
                groups[-1].append(block)
                continue
        groups.append([block])
    return groups


def _caption_indices_before(lines: list[str], index: int,
                            skip_split_label: bool = False) -> list[int]:
    found = []
    k = index - 1
    while k >= 0:
        if not lines[k].strip():
            k -= 1
            continue
        if skip_split_label and _SPLIT_LABEL_LINE.match(lines[k]):
            k -= 1
            continue
        if _md_caption_text(lines[k]):
            found.insert(0, k)
            k -= 1
            continue
        break
    return found


def _caption_indices_after(lines: list[str], index: int) -> list[int]:
    found = []
    k = index + 1
    while k < len(lines):
        if not lines[k].strip():
            k += 1
            continue
        if _md_caption_text(lines[k]):
            found.append(k)
            k += 1
            continue
        break
    return found


def _clean_caption(line: str) -> str:
    return _md_caption_text(line) or line.strip()


def _relocate_md_captions(body: str) -> str:
    """把写在图前的图名移到图后；图后已有图名时只删图前重复项。"""
    lines = body.splitlines()
    infos = []
    for group in _md_groups(lines):
        first, last = group[0][0], group[-1][1]
        infos.append({
            'first': first, 'last': last,
            'pre': _caption_indices_before(
                lines, first, skip_split_label=len(group) > 1),
            'post': _caption_indices_after(lines, last),
        })
    for info in infos:
        info['owned_pre'] = list(info['pre'])
        info['owned_post'] = list(info['post'])

    # 图名夹在两个图之间时：前一张图已有图名（写在图前）→ 归后一张；否则归前一张。
    for i in range(1, len(infos)):
        prev, cur = infos[i - 1], infos[i]
        shared = set(prev['post']) & set(cur['pre'])
        if not shared:
            continue
        if prev['pre']:
            prev['owned_post'] = [idx for idx in prev['post'] if idx not in shared]
            cur['owned_pre'] = list(cur['pre'])
        else:
            prev['owned_post'] = list(prev['post'])
            cur['owned_pre'] = [idx for idx in cur['pre'] if idx not in shared]

    edits = []
    for info in infos:
        pre, post = info['owned_pre'], info['owned_post']
        for idx in post:
            clean = _clean_caption(lines[idx])
            if clean != lines[idx]:
                edits.append((idx, idx + 1, [clean]))
        if pre:
            for idx in pre:
                edits.append((idx, idx + 1, []))
            # 前后都有空行时顺手去掉一行，避免删掉图名后留下双空行。
            if (pre[0] - 1 >= 0 and not lines[pre[0] - 1].strip()
                    and pre[-1] + 1 < len(lines) and not lines[pre[-1] + 1].strip()):
                edits.append((pre[-1] + 1, pre[-1] + 2, []))
            if not post:
                captions = []
                for idx in pre:
                    caption = _clean_caption(lines[idx])
                    if caption and caption not in captions:
                        captions.append(caption)
                insert_at = info['last'] + 1
                replacement = list(captions)
                if insert_at < len(lines) and lines[insert_at].strip():
                    replacement.append('')
                edits.append((insert_at, insert_at, replacement))
        # 空标题紧贴图块时是无意义的结构残留，删除。
        k = (pre[0] if pre else info['first']) - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if k >= 0 and _EMPTY_MD_HEADING.match(lines[k]):
            edits.append((k, k + 1, []))
        k = (post[-1] if post else info['last']) + 1
        while k < len(lines) and not lines[k].strip():
            k += 1
        if k < len(lines) and _EMPTY_MD_HEADING.match(lines[k]):
            edits.append((k, k + 1, []))

    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        lines[start:end] = replacement
    out = '\n'.join(lines)
    if body.endswith('\n'):
        out += '\n'
    return out


def normalize_body(body: str) -> str:
    def replace(match):
        code, captions = _pull_captions(match[2])
        if captions:
            code = code.rstrip('\n') + '\n'
        chunks = split_mindmap(normalize(code))
        if len(chunks) == 1:
            out = match[1] + chunks[0] + match[3]
        else:
            out = '\n\n'.join(
                f'（关系图分图 {i+1}/{len(chunks)}）\n\n```mermaid\n{chunk}```'
                for i, chunk in enumerate(chunks)
            )
        # 模型偶尔把图名同时写在代码块内外，已有正文图名时不再重复追加。
        tail = match.string[match.end():match.end() + 200]
        captions = [caption for caption in captions if caption not in tail]
        if captions:
            out += '\n\n' + '\n\n'.join(captions)
        return out
    body = re.sub(r'(```mermaid[^\n]*\n)(.*?)(```)',
                  replace, body, flags=re.S)
    return _relocate_md_captions(body)


_HTML_MERMAID_RE = re.compile(r'<div\b([^>]*?)\bdata-code="([^"]*)"([^>]*)></div>')
_HTML_MERMAID_DIV_RE = re.compile(r'<div\b[^>]*\bdata-code="[^"]*"[^>]*></div>', re.I)
_HTML_ELEMENT_FULL = re.compile(
    r'<h([1-6])\b[^>]*>([\s\S]*?)</h\1>\s*$|'
    r'<p\b([^>]*)>([\s\S]*?)</p>\s*$', re.I)
_HTML_ELEMENT_OPEN = re.compile(r'<(?:p|h[1-6])\b[^>]*>', re.I)
_HTML_POST_P_RE = re.compile(r'<p\b([^>]*)>([\s\S]*?)</p>', re.I)
_HTML_POST_H_RE = re.compile(r'<h([1-6])\b[^>]*>([\s\S]*?)</h\1>', re.I)
_HTML_EMPTY_HEADING_RE = re.compile(r'<h([1-6])\b[^>]*>\s*</h\1>', re.I)
_FIGURE_CAPTION_P = '<p data-figure-caption="true" style="text-align: center;">{}</p>'


def _html_text(fragment: str) -> str:
    return html.unescape(re.sub(r'<[^>]+>', '', fragment)).strip()


def _html_caption_text(fragment: str) -> str | None:
    text = _html_text(fragment)
    return text if _CAPTION_TEXT_RE.match(text) else None


def _html_pre_element(prefix: str):
    """取紧邻图块、且一直延伸到图块前的最后一个段落/标题元素。"""
    for opening in reversed(list(_HTML_ELEMENT_OPEN.finditer(prefix))):
        match = _HTML_ELEMENT_FULL.match(prefix[opening.start():])
        if match:
            return opening.start(), match
    return None


def _relocate_html_captions(body: str) -> str:
    """HTML 图名同样归图后：图前标题/段落移走，空标题删除。"""
    divs = list(_HTML_MERMAID_DIV_RE.finditer(body))
    if not divs:
        return body
    edits = []
    for div in divs:
        start, end = div.start(), div.end()
        prefix, suffix = body[:start], body[end:]

        pre_caption = None
        pre_empty_heading = False
        pre_span = None
        pre_el = _html_pre_element(prefix)
        if pre_el:
            pre_start, pre = pre_el
            pre_span = (pre_start, pre_start + pre.end())
            if pre.group(1):  # <hN>
                inner = pre.group(2)
                pre_caption = _html_caption_text(inner)
                pre_empty_heading = not _html_text(inner)
            else:  # <p>
                attrs, inner = pre.group(3), pre.group(4)
                pre_caption = _html_caption_text(inner)
                if not pre_caption and 'data-figure-caption' in attrs:
                    pre_caption = _html_text(inner) or None

        post_caption = None
        post_span = None
        post_empty_heading = None
        p = _HTML_POST_P_RE.search(suffix)
        if p and not suffix[:p.start()].strip():
            inner = p.group(2)
            post_caption = _html_caption_text(inner)
            if not post_caption and 'data-figure-caption' in p.group(1):
                post_caption = _html_text(inner) or None
            if post_caption:
                post_span = (end + p.start(), end + p.end())
        else:
            h = _HTML_POST_H_RE.search(suffix)
            if h and not suffix[:h.start()].strip():
                inner = h.group(2)
                if not _html_text(inner):
                    post_empty_heading = h
                else:
                    post_caption = _html_caption_text(inner)
                    if post_caption:
                        post_span = (end + h.start(), end + h.end())

        if pre_caption:
            # 图名紧跟在上一张图后时属于上一张，不能抢给当前图。
            previous_end = max((d.end() for d in divs if d.end() <= pre_span[0]), default=-1)
            protected = (previous_end >= 0 and not body[previous_end:pre_span[0]].strip())
            if not protected:
                edits.append((pre_span[0], pre_span[1], ''))
                if not post_caption:
                    caption = html.escape(pre_caption, quote=False)
                    edits.append((end, end, _FIGURE_CAPTION_P.format(caption)))
        elif pre_empty_heading:
            edits.append((pre_span[0], pre_span[1], ''))

        if post_caption:
            standard = _FIGURE_CAPTION_P.format(html.escape(post_caption, quote=False))
            if body[post_span[0]:post_span[1]] != standard:
                edits.append((post_span[0], post_span[1], standard))
        elif post_empty_heading:
            span = (end + post_empty_heading.start(), end + post_empty_heading.end())
            edits.append((span[0], span[1], ''))

    out = body
    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        out = out[:start] + replacement + out[end:]
    return out


def normalize_html_body(body: str) -> str:
    """HTML 正文里的 mermaid-block 同一条规则：图名移出 data-code，插回图后正文。"""
    def replace(match):
        code, captions = _pull_captions(match[2])
        if not captions:
            return match.group(0)
        out = f'<div{match.group(1)}data-code="{code.rstrip()}"{match.group(3)}></div>'
        tail = match.string[match.end():match.end() + 300]
        kept = [caption for caption in captions if caption not in tail]
        if kept:
            out += ''.join(f'<p>{caption}</p>' for caption in kept)
        return out
    return _relocate_html_captions(_HTML_MERMAID_RE.sub(replace, body))


def normalize_payload_body(body: str, fmt: str = 'md') -> str:
    """落库前的统一入口：兼容历史混存（format 标 md 但正文是 HTML）。"""
    if not body:
        return body
    if (fmt or 'md').lower() == 'html' or 'data-code="' in body:
        body = normalize_html_body(body)
    if '```mermaid' in body:
        body = normalize_body(body)
    return body

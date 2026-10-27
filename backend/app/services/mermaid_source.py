"""修复已声明的时序图参与者 ID 与 Mermaid 保留词 link 的冲突，不改图中文字。"""
import html
import re
from datetime import datetime, timedelta

#: 时间制甘特图里的“纯小时偏移”写法：`任务 :0, 2h`。Mermaid 需要完整时间戳，
#: 否则整张图解析失败；这里只补参考日期，不改任务名、相对起点和工期。
_GANTT_HOUR_OFFSET = re.compile(
    # 任务名里不含冒号：这样只认第一个冒号（任务分隔符），已经带完整日期的
    # `:2000-01-01 00:15, 1d` 不会再被二次换算。
    r'^(?P<label>\s*[^:\n]+?)\s*:\s*(?:(?:active|crit|done|milestone)\s*,\s*)?'
    r'(?P<start>\d+(?:\.\d+)?)\s*,\s*(?P<dur>\d+(?:\.\d+)?[hdm])\s*$'
)


def _normalize_gantt_time_offsets(source: str) -> str:
    if not re.search(r'^\s*dateFormat\s+[^\n]*\b(?:HH|mm)\b', source, re.M):
        return source
    reference = datetime(2000, 1, 1)
    lines = []
    for line in source.splitlines():
        match = _GANTT_HOUR_OFFSET.match(line)
        if match:
            stamp = (reference + timedelta(hours=float(match['start']))).strftime('%Y-%m-%d %H:%M')
            line = f"{match['label']} :{stamp}, {match['dur']}"
        lines.append(line)
    out = '\n'.join(lines)
    if source.endswith('\n'):
        out += '\n'
    return out


def normalize(source: str) -> str:
    if re.match(r'^\s*gantt\b',source):
        # Mermaid 不接受 `:0, 2h` 这类纯偏移；先补参考日期，再处理里程碑引用。
        source = _normalize_gantt_time_offsets(source)
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
    return _relocate_orphan_md_captions(_relocate_md_captions(body))


def _relocate_orphan_md_captions(body: str) -> str:
    """图名离图太远（常见于全章末尾）时收回最近的图后；无图可归则删除。

    2026-10-27 项目 37《应急保障与故障处置预案》实测：Mermaid 图在章中，
    “图：故障分级响应与应急处置闭环流程图”孤零零落在全章最后，正文没有第二张图。
    原来的 `_relocate_md_captions` 只看图前后紧邻的行，管不到这种远距离图名。
    """
    lines = body.splitlines()
    groups = _md_groups(lines)
    if not groups:
        # 全章一张图都没有：单独的“图：……”只会让读者以为漏图，直接删掉。
        # 正文若用 Markdown 图片语法插了图，图名可能本来就属于它，保持原样。
        if re.search(r'!\[[^\]]*\]\([^)]+\)', body):
            return body
        kept = [line for line in lines if not _md_caption_text(line)]
        if len(kept) == len(lines):
            return body
        out = '\n'.join(kept)
        if body.endswith('\n'):
            out += '\n'
        return out
    attached = set()
    has_after = []
    for group in groups:
        first, last = group[0][0], group[-1][1]
        attached.update(_caption_indices_before(
            lines, first, skip_split_label=len(group) > 1))
        after = _caption_indices_after(lines, last)
        attached.update(after)
        has_after.append(bool(after))
    orphans = [i for i, line in enumerate(lines)
               if _md_caption_text(line) and i not in attached]
    if not orphans:
        return body
    edits = []
    for index in orphans:
        target = next((gi for gi in range(len(groups) - 1, -1, -1)
                       if groups[gi][-1][1] < index and not has_after[gi]), None)
        if target is None:
            # 前面没有可归属的图，留着只会让读者以为这里漏了一张图。
            edits.append((index, index + 1, []))
            continue
        has_after[target] = True
        at = groups[target][-1][1] + 1
        replacement = [_clean_caption(lines[index])]
        if at < len(lines) and lines[at].strip():
            replacement.append('')
        edits.append((index, index + 1, []))
        edits.append((at, at, replacement))
    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        lines[start:end] = replacement
    out = '\n'.join(lines)
    if body.endswith('\n'):
        out += '\n'
    return out


_HTML_MERMAID_RE = re.compile(r'<div\b([^>]*?)\bdata-code="([^"]*)"([^>]*)></div>')
_HTML_MERMAID_DIV_RE = re.compile(r'<div\b[^>]*\bdata-code="[^"]*"[^>]*></div>', re.I)
# Markdown 围栏经 python-markdown 转换后的形态：<pre><code class="language-mermaid">。
# 它同样是图块；不把这种图算进来，无图判断会把它后面的合法图名误删。
_HTML_MERMAID_PRE_RE = re.compile(
    r'<pre\b[^>]*>\s*<code\b[^>]*class="[^"]*\blanguage-mermaid\b[^"]*"[^>]*>[\s\S]*?</code>\s*</pre>',
    re.I,
)
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
    return _relocate_orphan_html_captions(
        _relocate_html_captions(_HTML_MERMAID_RE.sub(replace, body)))


_HTML_IMG_RE = re.compile(r'<img\b[^>]*>', re.I)


def _following_html_caption(body: str, end: int):
    """图块后面第一个非空元素是图名时返回 (start,end,text)。

    图片常包在 `<p><img></p>` 里，图与图名之间会隔着 `</p>`；跳过闭合标签再匹配，
    否则合法的图片图名会被误判成孤儿。
    """
    offset = end
    while offset < len(body):
        if body[offset].isspace():
            offset += 1
            continue
        closing = next((tag for tag in ('</p>', '</div>', '</figure>')
                        if body.startswith(tag, offset)), None)
        if closing:
            offset += len(closing)
            continue
        break
    for regex in (_HTML_POST_P_RE, _HTML_POST_H_RE):
        match = regex.match(body, offset)
        if not match:
            continue
        text = _html_caption_text(match.group(2))
        if not text and 'data-figure-caption' in match.group(0):
            text = _html_text(match.group(2))
        return (match.start(), match.end(), text) if text else None
    return None


def _relocate_orphan_html_captions(body: str) -> str:
    """HTML 图名离图太远时收回最近的 Mermaid 图后；无图可归则删除。

    只把图名归给 Mermaid 图块；普通图片的图名保持原位（无法可靠判断其容器边界）。
    """
    mermaids = ([(m.start(), m.end()) for m in _HTML_MERMAID_DIV_RE.finditer(body)]
                + [(m.start(), m.end()) for m in _HTML_MERMAID_PRE_RE.finditer(body)])
    mermaids.sort()
    pictures = [(m.start(), m.end()) for m in _HTML_IMG_RE.finditer(body)]
    figures = sorted(mermaids + pictures)
    captions = []
    for match in _HTML_POST_P_RE.finditer(body):
        text = _html_caption_text(match.group(2))
        if not text and 'data-figure-caption' in match.group(1):
            text = _html_text(match.group(2))
        if text:
            captions.append((match.start(), match.end(), text))
    if not captions:
        return body
    if not figures:
        # 全章一张图都没有：孤立的图名只会让读者以为漏图。
        for start, end, _ in reversed(captions):
            body = body[:start] + body[end:]
        return body
    attached, captioned = set(), set()
    for start, end in figures:
        post = _following_html_caption(body, end)
        if post:
            attached.add((post[0], post[1]))
            captioned.add((start, end))
    edits = []
    for start, end, text in captions:
        if (start, end) in attached:
            continue
        # 最近的图是普通图片时，图名可能本来就属于它；不移动、也不删除。
        previous = [i for i, (f_start, f_end) in enumerate(figures) if f_end <= start]
        if previous and figures[previous[-1]] in pictures:
            continue
        target = next((i for i in range(len(mermaids) - 1, -1, -1)
                       if mermaids[i][1] <= start and mermaids[i] not in captioned), None)
        if target is None and not pictures:
            # 全章没有可归属的图，留着会让读者以为这里漏了一张图。
            edits.append((start, end, ''))
            continue
        if target is None:
            continue
        captioned.add(mermaids[target])
        insert_at = mermaids[target][1]
        edits.append((insert_at, insert_at,
                      _FIGURE_CAPTION_P.format(html.escape(text, quote=False))))
        edits.append((start, end, ''))
    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        body = body[:start] + replacement + body[end:]
    return body


def normalize_payload_body(body: str, fmt: str = 'md') -> str:
    """落库前的统一入口：兼容历史混存（format 标 md 但正文是 HTML）。"""
    if not body:
        return body
    if (fmt or 'md').lower() == 'html' or 'data-code="' in body:
        body = normalize_html_body(body)
    if '```mermaid' in body:
        body = normalize_body(body)
    return body

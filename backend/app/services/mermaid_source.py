"""修复已声明的时序图参与者 ID 与 Mermaid 保留词 link 的冲突，不改图中文字。"""
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


def normalize_body(body: str) -> str:
    def replace(match):
        chunks=split_mindmap(normalize(match[2]))
        if len(chunks)==1:return match[1]+chunks[0]+match[3]
        return '\n\n'.join(f'（关系图分图 {i+1}/{len(chunks)}）\n\n```mermaid\n{code}```' for i,code in enumerate(chunks))
    return re.sub(r'(```mermaid[^\n]*\n)(.*?)(```)',
                  replace, body, flags=re.S)

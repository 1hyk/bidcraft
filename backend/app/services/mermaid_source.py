"""修复已声明的时序图参与者 ID 与 Mermaid 保留词 link 的冲突，不改图中文字。"""
import re


def normalize(source: str) -> str:
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


def normalize_body(body: str) -> str:
    return re.sub(r'(```mermaid[^\n]*\n)(.*?)(```)',
                  lambda m: m[1] + normalize(m[2]) + m[3], body, flags=re.S)

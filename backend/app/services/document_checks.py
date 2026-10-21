"""正文待处理标记及内部引用的共享检查，不推断公司事实。"""
import re

_MARKERS = re.compile(r'\[(?:待补充|待确认)[^\]\n]{0,160}\]|【(?:待补充|待确认)[^】\n]{0,160}】|[（(]\s*(?:待确认|待补充)(?:\s*[：:][^）)\n]{1,120})?\s*[）)]')
_INTERNAL = re.compile(r'(?:招标要求|要求条目|需求条目)\s*#\s*\d+')


def pending_markers(text):
    return _MARKERS.findall(text)


def internal_references(text):
    return list(dict.fromkeys(_INTERNAL.findall(text)))


def resolve_requirement_refs(body, requirements):
    sources = {str(r['id']): str(r.get('source_location') or r.get('title') or '').strip()
               for r in requirements if r.get('id') is not None}
    def replace(match):
        location = sources.get(match['id'])
        if not location: return match[0]
        prefix = match['prefix'] or ''
        join = prefix if prefix in {'及','和','与','、'} else ''
        return join+'采购文件“'+re.sub(r'\s+', ' ', location)+'”'
    pattern = re.compile(r'(?P<prefix>招标要求|要求条目|需求条目|及|和|与|、)\s*#\s*(?P<id>\d+)')
    parts = re.split(r'(```.*?```|`[^`\n]+`)',body,flags=re.S)
    return ''.join(part if part.startswith('`') else pattern.sub(replace,part) for part in parts)

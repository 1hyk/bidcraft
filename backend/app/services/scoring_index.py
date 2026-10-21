"""编辑预览和 Word 导出共用的评分索引；不代表响应充分性结论。"""
from app.core.chapter_title import related, normalize
import re
import json
from html import escape

ROLE = 'scoring_index'


def is_index(node):
    outline = node.get('outline') or {}
    if isinstance(outline, str):
        try: outline = json.loads(outline)
        except (TypeError, ValueError): return False
    return outline.get('_document_role') == ROLE


def criteria_summary(content):
    """只剔除要求之后的评分档位，不机械截断技术要求。"""
    text = str(content or '').strip()
    band = re.search(r'\d+\s*[–—~-]\s*\d+\s*分[：:（(]', text)
    if band and band.start() > 10:
        return text[:band.start()].strip()
    band = re.search(r'[。\n](?=[^。\n]{0,80}得\s*\d+\s*[-–—~]?\s*\d*\s*分)', text)
    return text[:band.start()+1].strip() if band and band.start() > 10 else text


def editable_html(entries):
    rows = []
    for entry in entries:
        if not entry['included']: continue
        # 一个评分项先定位完整响应章；不再把整棵子树铺满索引。
        refs = [{'id':entry['node_id'], 'title':entry['chapter_title']}]
        titles = ''.join('<p>'+escape(ref['title'])+'</p>' for ref in refs)
        pages = ''.join(f'<p><span data-page-reference="{int(ref["id"])}">排版后生成</span></p>' for ref in refs)
        criteria = escape(criteria_summary(entry.get('content'))).replace('\n','<br>')
        rows.append('<tr><td><p>'+escape(entry['title'])+'</p></td><td><p>'+criteria+
                    '</p></td><td>'+titles+'</td><td>'+pages+'</td></tr>')
    return ('<p>本表按技术评分要点定位响应章节；评分标准以招标原文为准，实际证明材料须据实提供。</p>'
            '<table><tr><th colwidth="130"><p>评分项</p></th><th colwidth="340"><p>评分要求摘要</p></th><th colwidth="180"><p>对应章节</p></th><th colwidth="100"><p>正文页码</p></th></tr>'+
            ''.join(rows)+'</table>')


def ensure_editor_index(project_id):
    """显式同步接口调用；只新建一次，绝不重写用户已有内容。"""
    from app.core.database import get_conn
    from app.services.section_service import get_tree
    entries = project_index(project_id)
    tree = get_tree(project_id)
    if not entries or not tree:
        return {'created': False}
    conn = get_conn()
    conn.begin()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM project WHERE id=%s FOR UPDATE', (project_id,))
            if not cur.fetchone(): raise ValueError('项目不存在')
            cur.execute('SELECT id,outline FROM chapter_node WHERE project_id=%s', (project_id,))
            existing = next((n for n in cur.fetchall() if is_index(n)), None)
            if existing:
                conn.commit()
                return {'created': False, 'node_id': existing['id']}
            # 放入已展示的编制子树，导出时作为前置索引单独输出，不参与正文编号。
            parent = tree[0]['id']
            cur.execute('SELECT lot_id FROM chapter_node WHERE id=%s', (parent,))
            lot_id = cur.fetchone().get('lot_id')
            cur.execute('SELECT MIN(sort_order) AS first_order FROM chapter_node WHERE parent_id=%s', (parent,))
            order = (cur.fetchone().get('first_order') or 0) - 1
            outline = json.dumps({'_document_role': ROLE, 'thinking': '按招标要求核对索引，直接编辑表格；页码在导出排版后生成。'}, ensure_ascii=False)
            cur.execute("INSERT INTO chapter_node(project_id,lot_id,parent_id,title,sort_order,status,outline) VALUES(%s,%s,%s,%s,%s,'generated',%s)",
                        (project_id, lot_id, parent, '技术评分响应索引', order, outline))
            nid = cur.lastrowid
            content = json.dumps({'format': 'html', 'body': editable_html(entries)}, ensure_ascii=False)
            cur.execute('INSERT INTO section_content(chapter_node_id,content,ai_generated) VALUES(%s,%s,0)', (nid, content))
        conn.commit()
        return {'created': True, 'node_id': nid}
    except Exception:
        conn.rollback()
        raise


def index_required(requirements):
    """只认当前文件的明确编制指令；评分表、附件名称本身不构成指令。"""
    for row in requirements:
        if row.get('status') == 'ignored' or row.get('category') != '编制要求':
            continue
        for sentence in re.split(r'[。；;\n]', str(row.get('content') or '')):
            if re.search(r'无需|无须|不需|不用|不要求|可不|自行决定|自愿', sentence):
                continue
            if re.search(r'(?:须|应当|应|必须|要求|请)[^。；;\n]{0,50}(?:编制|提供|附|填写|建立)[^。；;\n]{0,25}(?:索引目录|评分索引|响应索引|索引表)', sentence):
                return True
    return False


def build_entries(requirements, tree):
    nodes = []
    def walk(items):
        for node in items:
            if is_index(node): continue
            nodes.append(node)
            walk(node.get('children') or [])
    walk(tree)
    entries = []
    for req in requirements:
        if req.get('status') == 'ignored':
            continue
        matches = [n for n in nodes if normalize(req['title']) == normalize(n['title'])]
        if not matches:
            matches = [n for n in nodes if related(req['title'], n['title'])]
        if not matches:
            matches = [n for n in nodes if related(req.get('suggested_chapter'), n['title'])]
        refs = []
        seen = set()
        def collect(node, path=()):
            if is_index(node) or node['id'] in seen: return
            seen.add(node['id'])
            refs.append({'id':node['id'], 'title':' / '.join((*path,node['title']))})
            for child in node.get('children') or []:
                collect(child, (*path,node['title']))
        for node in matches:
            collect(node)
        entries.append({'requirement_id': req.get('id'), 'title': req['title'],
                        'content': req.get('content', ''), 'source_file': req.get('source_file', ''),
                        'source_location': req.get('source_location', ''),
                        'node_id': matches[0]['id'] if matches else None,
                        'chapter_title': matches[0]['title'] if matches else '',
                        'chapter_refs': refs,
                        'included': bool(matches)})
    return entries


def project_index(project_id, tree=None):
    from app.services.requirement_service import list_requirements
    from app.services.section_service import get_tree
    requirements = list_requirements(project_id)
    if not index_required(requirements):
        return []
    return build_entries([r for r in requirements if r.get('category') == '评分办法'],
                         get_tree(project_id) if tree is None else tree)

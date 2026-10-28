"""只回报当前源码的实际导出失败，不在正文生成或检查中启动浏览器。"""
import hashlib,html,json,re,os,uuid
from app.core.config import settings

def sources(payload):
    body=payload.get('body') or ''
    if body.lstrip().startswith('<') or payload.get('format')=='html':
        from lxml import html as parser
        try:
            root=parser.fromstring('<div>'+body+'</div>')
            return root.xpath('//*[@data-code]/@data-code')
        except Exception:return []
    return re.findall(r'^\s*```mermaid[^\n]*\n(.*?)^\s*```\s*$',body,re.S|re.M)

def key(source):return hashlib.sha256(source.strip().encode()).hexdigest()
def path(project_id):return settings.uploads_dir/'diagram_diagnostics'/f'{int(project_id)}.json'

def save(project_id,contents,rendered):
    from app.services.mermaid_source import split_mindmap
    rows=[]
    for nid,payload in contents.items():
        for pos,source in enumerate(sources(payload),1):
            parts=split_mindmap(source)
            rows.append({'node_id':int(nid),'source_hash':key(source),'position':pos,
                         'failed':any(part.strip() not in rendered for part in parts)})
    p=path(project_id);p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(rows),encoding='utf-8');os.replace(temp,p)
    finally:temp.unlink(missing_ok=True)

def failures(project_id,node_id,payload):
    p=path(project_id)
    if not p.exists():return []
    try:records=json.loads(p.read_text(encoding='utf-8'))
    except (ValueError,OSError):return []
    current={key(s) for s in sources(payload)}
    return [r for r in records if r['node_id']==node_id and r['source_hash'] in current and r['failed']]

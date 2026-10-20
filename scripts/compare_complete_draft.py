"""项目14只读取材，生成两章对照文件；不写回正文。"""
import sys, json, hashlib, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.core.database import query
from app.services.writer_service import _chapter_context, _target_for
from app.agents.writer_agent import write_chapter
from app.services.evidence_roles import flag_capability_claims, flag_unverified_conclusions

OUT = Path(__file__).resolve().parents[1] / 'uploads/_verification/complete_draft_20261019'
OUT.mkdir(parents=True, exist_ok=True)
def fingerprint():
    rows=query('SELECT n.id,n.title,c.content FROM chapter_node n LEFT JOIN section_content c ON c.chapter_node_id=n.id WHERE n.project_id=%s ORDER BY n.id',(14,))
    return hashlib.sha256(json.dumps(rows,ensure_ascii=False,default=str,sort_keys=True).encode()).hexdigest()
before=fingerprint()
def run(node):
    ctx=_chapter_context(14,node)
    row=query('SELECT content FROM section_content WHERE chapter_node_id=%s',(node,))[0]
    old=row['content']; old=json.loads(old) if isinstance(old,str) else old
    (OUT/f'{node}_before.md').write_text(old.get('body',''),encoding='utf-8')
    args={k:ctx[k] for k in ('outline','facts','knowledge','siblings','experiences','methods','file_refs','lot_info','reqs','lot_profile','domain')}
    args.update(skip_images=ctx.get('img_skips'),image_refs=ctx.get('image_refs'),target_words=_target_for(14,node))
    body=flag_capability_claims(flag_unverified_conclusions(write_chapter(**args)))
    (OUT/f'{node}_after.md').write_text(body,encoding='utf-8')
    counts=lambda text:len(re.findall(r'\[待(?:补充|确认)[^\]]*\]',text))
    return {'node_id':node,'title':ctx['node']['title'],'before_pending':counts(old.get('body','')),'after_pending':counts(body),'characters':len(body)}
with ThreadPoolExecutor(max_workers=2) as pool:
    result=list(pool.map(run,[int(v) for v in sys.argv[1:]] or [530,555]))
assert fingerprint()==before,'原正文发生变化'
report=OUT/'result.json'
previous=json.loads(report.read_text(encoding='utf-8')).get('chapters',[]) if report.exists() else []
merged={r['node_id']:r for r in previous+result}
report.write_text(json.dumps({'original_unchanged':True,'chapters':list(merged.values())},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False),flush=True)

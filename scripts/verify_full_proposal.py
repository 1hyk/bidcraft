"""在独立项目副本上运行真实全稿验证，原项目正文以摘要校验保护。"""
import argparse
import hashlib
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.core.config import settings
from app.core.database import query, query_one, execute, get_conn

OUT = settings.uploads_dir / '_verification' / 'full_proposal'


def digest(pid):
    rows = query('SELECT cn.id,cn.outline,sc.content FROM chapter_node cn LEFT JOIN section_content sc ON sc.chapter_node_id=cn.id WHERE cn.project_id=%s ORDER BY cn.id', (pid,))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def insert(table, row, **changes):
    data = {k: v for k, v in row.items() if k != 'id'}
    data.update(changes)
    cols = list(data)
    return execute('INSERT INTO `' + table + '` (' + ','.join('`'+c+'`' for c in cols) + ') VALUES (' + ','.join(['%s']*len(cols)) + ')', tuple(data.values()))


def prepare(source, pages):
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = __import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = {t: query('SELECT * FROM '+t+' WHERE project_id=%s', (source,)) for t in ['lot','project_file','global_fact','tender_requirement','chapter_node','file_section_index']}
    backup['project'] = query_one('SELECT * FROM project WHERE id=%s', (source,))
    backup['section_content'] = query('SELECT sc.* FROM section_content sc JOIN chapter_node cn ON cn.id=sc.chapter_node_id WHERE cn.project_id=%s', (source,))
    (OUT / f'original_{source}_{stamp}.json').write_text(json.dumps(backup, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
    before = digest(source)
    conn = get_conn()
    conn.begin()
    try:
        pid = insert('project', backup['project'], name=backup['project']['name']+'—150页完整验证稿—'+stamp, page_plan=json.dumps({'target_pages':pages,'rewrite_tolerance':0.15}))
        lots = {r['id']: insert('lot', r, project_id=pid) for r in backup['lot']}
        files = {r['id']: insert('project_file', r, project_id=pid, lot_id=lots.get(r['lot_id'])) for r in backup['project_file']}
        for table in ['global_fact','tender_requirement','file_section_index']:
            for r in backup[table]:
                changes = {'project_id':pid}
                if 'lot_id' in r: changes['lot_id'] = lots.get(r['lot_id'])
                if 'file_id' in r: changes['file_id'] = files[r['file_id']]
                # applicable_lots/applies_lots encode lot codes/names in this project, not DB keys.
                insert(table, r, **changes)
        nodes = {}
        pending = list(backup['chapter_node'])
        while pending:
            progressed = False
            for r in pending[:]:
                if r['parent_id'] and r['parent_id'] not in nodes: continue
                nodes[r['id']] = insert('chapter_node', r, project_id=pid, lot_id=lots.get(r['lot_id']), parent_id=nodes.get(r['parent_id']), status='unwritten', outline=None, target_words=None)
                pending.remove(r)
                progressed = True
            if not progressed: raise RuntimeError('目录存在循环或缺失父节点')
        # 项目专用知识也必须保留原来的可见性；不放宽 scope。
        for r in query("SELECT * FROM knowledge_doc WHERE project_id=%s AND scope='project'", (source,)):
            doc = insert('knowledge_doc',r,project_id=pid)
            for t in ['knowledge_page','method_index']:
                for child in query('SELECT * FROM '+t+' WHERE doc_id=%s',(r['id'],)):
                    insert(t,child,doc_id=doc)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    state = {'source_id':source,'project_id':pid,'pages':pages,'original_digest':before,'phase':'prepared','node_map':nodes}
    save(state)
    print(json.dumps(state, ensure_ascii=True), flush=True)
    return state


def save(state):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')


def run(state):
    from app.services.compile_service import regenerate_node_outline
    from app.services.section_service import generation_node_ids
    from app.services import writer_service
    from app.services.docx_export import export_project_docx
    pid = state['project_id']
    ids = sorted(generation_node_ids(pid))
    failed = []
    if state['phase'] in ('prepared','outlines_failed'):
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(regenerate_node_outline,0,pid,n):n for n in ids}
            for f in as_completed(futures):
                n=futures[f]
                try: result=f.result()
                except Exception as e: result={'ok':False,'error':str(e)}
                if not result.get('ok'): failed.append({'node_id':n,'result':result})
                print(json.dumps({'outline':n,'ok':result.get('ok'),'completed':len([x for x in futures if x.done()]),'total':len(ids)},ensure_ascii=True),flush=True)
        state.update(phase='outlines_failed' if failed else 'outlines_ready',outline_failures=failed)
        save(state)
        if failed: raise RuntimeError('部分编写思路生成失败，见 state.json')
    state['phase']='writing';save(state)
    print(writer_service.start_batch(pid),flush=True)
    import time
    while pid in writer_service._batch_running:
        time.sleep(3)
    missing=query("SELECT cn.id,cn.title FROM chapter_node cn LEFT JOIN section_content sc ON sc.chapter_node_id=cn.id WHERE cn.project_id=%s AND sc.id IS NULL",(pid,))
    state.update(phase='writing_failed' if missing else 'written',missing=missing)
    save(state)
    if missing: raise RuntimeError('部分正文未生成，见 state.json')
    path,name=export_project_docx(pid)
    dest=OUT/f'proposal_{pid}_150pages.docx'
    shutil.copy2(path,dest)
    state.update(phase='exported',docx=str(dest),original_unchanged=digest(state['source_id'])==state['original_digest'])
    save(state)
    print(json.dumps(state,ensure_ascii=True),flush=True)
    if not state['original_unchanged']: raise RuntimeError('原项目摘要发生变化')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['prepare','run'])
    parser.add_argument('--source',type=int,default=4)
    parser.add_argument('--pages',type=int,default=150)
    args=parser.parse_args()
    if args.mode=='prepare': prepare(args.source,args.pages)
    else: run(json.loads((OUT/'state.json').read_text(encoding='utf-8')))

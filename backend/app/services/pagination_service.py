"""实测正文页数和同排版字页系数；测量不改正文，按内容指纹识别过期结果。"""
import hashlib
import json
import threading
from datetime import datetime, timezone
from app.core.config import settings
from app.core.database import query, query_one, execute
from app.core.docx_style import get_project_style
from app.core.page_plan import content_words, get_project_plan

_locks = {}
_guard = threading.Lock()
_adjusting = set()


def snapshot(project_id):
    from app.services.section_service import generation_node_ids
    ids = generation_node_ids(project_id)
    rows = query('SELECT n.id,n.title,n.parent_id,n.sort_order,c.content FROM chapter_node n LEFT JOIN section_content c ON c.chapter_node_id=n.id WHERE n.project_id=%s ORDER BY n.id', (project_id,))
    body = []
    for row in rows:
        if row['id'] not in ids: continue
        data = row.get('content') or {}
        if isinstance(data,str): data=json.loads(data)
        body.append({'id':row['id'],'title':row['title'],'parent_id':row['parent_id'],'sort_order':row['sort_order'],
                     'body':data.get('body') or '', 'format':data.get('format') or 'md'})
    style = get_project_style(project_id).to_dict()
    project=query_one('SELECT name FROM project WHERE id=%s',(project_id,)) or {}
    fingerprint = hashlib.sha256(json.dumps({'layout_version':12,'style':style,'body':body,'project':project},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'signature':fingerprint,'style':style,'words':sum(content_words(x) for x in body),
            'complete':bool(body) and all(x['body'].strip() for x in body)}


def stored(project_id):
    row=query_one('SELECT page_plan FROM project WHERE id=%s',(project_id,)) or {}
    value=row.get('page_plan') or {}
    if isinstance(value,str): value=json.loads(value)
    return value.get('measurement')


def measure(project_id, input_path=None):
    from app.services.document_layout import render
    from app.services.docx_export import export_project_docx
    with _guard: lock=_locks.setdefault(project_id,threading.Lock())
    if not lock.acquire(blocking=False): raise ValueError('该项目正在测量页数，请稍后查看')
    try:
        before=snapshot(project_id)
        if not before['complete']: raise ValueError('请先完成当前编制范围全部章节正文，再测量页数')
        directory=settings.uploads_dir/'layout'/f'project_{project_id}'/before['signature']
        previous=stored(project_id)
        if previous and previous.get('signature')==before['signature'] and (directory/'document.docx').is_file() and (directory/'document.pdf').is_file():
            return view(project_id,previous,before)
        path=input_path
        if path is None:path,_=export_project_docx(project_id)
        actual=render(path,directory)
        if snapshot(project_id)['signature']!=before['signature']:
            raise ValueError('测量期间正文或排版有修改，本次结果未用于校准，请重新测量')
        data={**before,**actual,'value':round(before['words']/actual['body_pages'],2),
              'measured_at':datetime.now(timezone.utc).isoformat()}
        execute("UPDATE project SET page_plan=JSON_SET(COALESCE(page_plan,JSON_OBJECT()), '$.measurement', JSON_EXTRACT(%s,'$')) WHERE id=%s",(json.dumps(data,ensure_ascii=False),project_id))
        return view(project_id,data,before)
    finally: lock.release()


def view(project_id,data=None,current=None):
    data=data or stored(project_id)
    if not data:return None
    current=current or snapshot(project_id)
    fresh=data.get('signature')==current['signature']
    plan=get_project_plan(project_id)
    target=plan.target_pages
    return {**data,'fresh':fresh,'target_pages':target,
            'within_tolerance':(abs(data['body_pages']-target)/target<=plan.rewrite_tolerance) if fresh and target else None,
            'deviation':(data['body_pages']-target)/target if fresh and target else None,
            'docx_url':f"/uploads/layout/project_{project_id}/{data['signature']}/document.docx",
            'pdf_url':f"/uploads/layout/project_{project_id}/{data['signature']}/document.pdf"}


def adjustment_nodes(nodes, over, tolerance=.15):
    return [n['id'] for n in nodes if n.get('deviation') is not None
            and (n['deviation']>tolerance if over else n['deviation'] < -tolerance)]


def start_adjust(project_id):
    from app.services.task_runner import task_runner
    from app.services.writer_service import _batch_running
    if not get_project_plan(project_id).target_pages: raise ValueError('请先设置目标正文页数')
    with _guard:
        if project_id in _adjusting or project_id in _batch_running:raise ValueError('项目已有生成或篇幅调整任务')
        _adjusting.add(project_id)
    try:
        task=task_runner.submit('page_adjust',project_id,adjust_to_target,project_id)
        return {'task_id':task}
    except Exception:
        with _guard:_adjusting.discard(project_id)
        raise


def adjust_to_target(task_id,project_id):
    """实测→只重写方向正确的超差章节→复测，最多两轮；不能达标时明确失败。"""
    from app.services.page_plan_service import build_page_plan_view
    from app.services.writer_service import rewrite_chapters, _batch_running
    import time
    try:
        for round_no in range(3):
            execute('UPDATE task SET detail=%s,progress=%s WHERE id=%s',
                    (f'第 {round_no+1} 次测量实际页数',round_no*40,task_id))
            actual=measure(project_id)
            if actual['within_tolerance'] is True:
                execute('UPDATE task SET detail=%s WHERE id=%s',(f"正文 {actual['body_pages']} 页，已在目标范围",task_id))
                return actual
            if not actual['target_pages']:raise ValueError('调整期间目标页数被清空，任务停止')
            if round_no==2:break
            plan=build_page_plan_view(project_id)
            nodes=adjustment_nodes(plan['nodes'],actual['deviation']>0,plan['plan']['rewrite_tolerance'])
            if not nodes:raise ValueError('实际页数超差，但章节字数均在范围；需调整图表布局或章节额度，未强行删减正文')
            started=rewrite_chapters(project_id,nodes)
            if not started.get('started'):raise ValueError(started.get('message') or '无法启动篇幅调整')
            while project_id in _batch_running:time.sleep(1)
            state=query_one('SELECT stage,progress,total FROM compile_session WHERE project_id=%s',(project_id,)) or {}
            if state.get('stage')!='done' or state.get('progress',0)<state.get('total',0):
                raise ValueError('部分章节调整失败，已保留成功稿，请检查后重试')
        raise ValueError(f"调整后正文仍为 {actual['body_pages']} 页，尚未达到目标范围；已保留当前稿与上一版，未标记为达标")
    finally:
        with _guard:_adjusting.discard(project_id)

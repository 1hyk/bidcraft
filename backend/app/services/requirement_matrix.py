"""当前标段的要求—证据矩阵。报告是可失效的检查产物，不是正文或人工验收结果。"""
import hashlib
import json
import os
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote

from app.core.config import settings

_guard=threading.Lock()
_locks={}
REVIEW_VERSION=3
NOTE='基于正文文本候选片段，核验已抽取、未忽略且适用当前标段的要求。请核对原文件是否有抽取遗漏；图片内文字未纳入本项核验。响应证据不等于公司能力已证明，投标操作风险应另行核查，结果须人工复核。'


def requirements(project_id):
    from app.services.requirement_service import list_requirements, REQUIREMENT_WRITE_CATEGORIES
    return [r for r in list_requirements(project_id) if r['category'] in REQUIREMENT_WRITE_CATEGORIES and r['status']!='ignored']


def response_scope(row):
    """仅将标题明确指向其他交付件的事项分流；不明确的继续检查技术正文。"""
    import re
    title=row.get('title') or ''
    if re.search(r'最高限价|报价超限|投标报价|价格评分|付款方式|保证金|资格.{0,4}不符|响应人资格|投标人资格|营业执照|业绩证明|密封|装订|递交|签章|投标上传|设备一致性|串通投标|联合体',title):
        return 'other_documents'
    return 'technical'


def requirement_signature(project_id, rows=None):
    from app.services.lot_service import selected_lot_code
    data={'review_version':REVIEW_VERSION,'lot':selected_lot_code(project_id),'requirements':requirements(project_id) if rows is None else rows}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def _path(project_id):
    return settings.uploads_dir/'requirement_reviews'/f'project_{int(project_id)}.json'


def source_files(project_id):
    from app.core.database import query
    rows=query("SELECT id,file_name,file_path FROM project_file WHERE project_id=%s AND category IN ('tender','clarification') ORDER BY id",(project_id,))
    by_name={}
    for row in rows:by_name.setdefault(row['file_name'],[]).append(row)
    return {name:{'file_id':files[0]['id'],'url':'/uploads/'+quote(str(files[0]['file_path']).replace('\\','/'),safe='/')}
            for name,files in by_name.items() if len(files)==1}


def save_assessment(project_id, data):
    from app.services.pagination_service import snapshot
    if snapshot(project_id)['signature']!=data['signature'] or requirement_signature(project_id)!=data['requirements_signature']:
        raise ValueError('核验期间正文、招标要求或标段有修改，请重新核验')
    report={**data,'checked_at':datetime.now(timezone.utc).isoformat(),'note':NOTE}
    path=_path(project_id);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)
    return matrix(project_id)


def matrix(project_id):
    from app.services.pagination_service import snapshot
    from app.services.proposal_quality import chapters
    rows=requirements(project_id)
    current=snapshot(project_id)['signature'];req_signature=requirement_signature(project_id,rows)
    cached={}
    try:
        loaded=json.loads(_path(project_id).read_text(encoding='utf-8'))
        if isinstance(loaded,dict) and isinstance(loaded.get('items'),list):cached=loaded
    except (OSError,ValueError):pass
    fresh=bool(cached) and cached.get('signature')==current and cached.get('requirements_signature')==req_signature
    saved={i['requirement_id']:i for i in cached.get('items',[]) if isinstance(i,dict) and 'requirement_id' in i} if fresh else {}
    nodes=list(chapters(project_id))
    sources=source_files(project_id)
    items=[]
    for requirement in rows:
        previous=saved.get(requirement['id'],{})
        targets=[{'node_id':n['id'],'chapter':n['title']} for n in nodes
                 if n['title'] in {requirement['title'],requirement.get('suggested_chapter')}]
        scope=response_scope(requirement)
        status=previous.get('status','unverified') if scope=='technical' else 'not_applicable'
        gap=previous.get('gap') or ''
        if status=='unverified' and not gap:
            gap=('未取得本条有效核验结果，请人工核对或重新核验' if fresh else
                 '尚未核验；请执行条款核验' if not cached else
                 '内容、编制设置或检查规则已变化，旧证据不再作为当前依据，请重新核验')
        if scope=='other_documents':gap='请在报价、资格附件或递交材料等对应文件中核查。本项不计入技术正文缺项，仍须遵守招标要求。'
        items.append({**{k:requirement.get(k) for k in ('title','category','content','weight','source_file','source_location','suggested_chapter')},
                      'requirement_id':requirement['id'],'status':status,
                      'missing_points':previous.get('missing_points',[]),'response_scope':scope,'review_state':previous.get('review_state','completed' if previous else 'not_checked'),
                      'source_document':sources.get(requirement.get('source_file')),
                      'evidence':previous.get('evidence',[]),'evidence_verified':previous.get('evidence_verified',False),
                      'gap':gap,
                      'target_nodes':targets,'human_review_required':True})
    technical=[i for i in items if i['response_scope']=='technical']
    counts=Counter(i['status'] for i in technical)
    return {'project_id':project_id,'items':items,'summary':{'total':len(technical),'other_documents':len(items)-len(technical),'review_errors':sum(i['review_state']=='error' for i in technical),**{s:counts[s] for s in ('covered','partial','missing','unverified','not_applicable')}},
            'fresh':fresh,'has_assessment':bool(cached),'checked_at':cached.get('checked_at'),
            'signature':current,'requirements_signature':req_signature,'note':NOTE}


def run_review(project_id):
    with _guard:lock=_locks.setdefault(project_id,threading.Lock())
    if not lock.acquire(blocking=False):raise ValueError('该项目正在核验，请等待本次完成，勿重复提交')
    try:
        from app.services.proposal_quality import review
        return review(project_id)
    finally:lock.release()

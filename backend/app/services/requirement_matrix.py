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
REVIEW_VERSION=11
NOTE='基于正文文本候选片段，核验已抽取、未忽略且适用当前标段的要求。请核对原文件是否有抽取遗漏；图片内文字未纳入本项核验。响应证据不等于公司能力已证明，投标操作风险应另行核查，结果须人工复核。'


def requirements(project_id):
    from app.services.requirement_service import list_requirements, REQUIREMENT_WRITE_CATEGORIES
    return [r for r in list_requirements(project_id) if r['category'] in REQUIREMENT_WRITE_CATEGORIES and r['status']!='ignored']


def review_content(row):
    """保留具体技术义务；将附件指引和文档版式转交编制检查，不删除原要求。"""
    import re
    if re.search(r'索引|逐条响应|响应表|对照表',str(row.get('title') or '')):
        return str(row.get('content') or ''),[]
    technical, deferred = [], []
    for clause in re.split(r'(?<=[。；;])|\n',str(row.get('content') or '')):
        text=clause.strip()
        if not text:continue
        concrete=re.search(r'接口|报文|字段|编码|精度|采集|验证|测试|时延|吞吐|并发|交付|故障|恢复|索引|符合性说明|逐条响应|响应表|对照表|应(?:当)?(?:包括|包含|提供|提交|阐述|说明|列出|覆盖)',text)
        reference=(re.search(r'附件',text) and re.search(r'详见|参见|按照|依据|遵循|按|见',text)
                   and re.search(r'格式|编制|编写|编排|排版',text))
        layout=re.search(r'字体|字号|页边距|行距|纸张|页眉|页脚|装订|签章',text)
        # 评分办法里的装订/页码/字体等版式扣分项同样是编制规则，不该逼着技术正文去"承接"；
        # 只认明确的版式词，不把"方案按附件编制"这类可能含实质要求的评分项一并豁免。
        if row.get('category')=='编制要求' and not concrete and (reference or layout):
            deferred.append(text)
        elif row.get('category')=='评分办法' and not concrete and layout:
            deferred.append(text)
        else:technical.append(text)
    return ''.join(technical),deferred


def response_scope(row):
    """仅将标题明确指向其他交付件的事项分流；不明确的继续检查技术正文。"""
    import re
    title=row.get('title') or ''
    # 业绩/案例是资格与商务评审材料，不是技术方案正文；即使被抽成“评分办法”
    # 也不由技术标承接（2026-10-22：项目31把商务评审的「业绩 12 分」带进了目录）。
    if re.search(r'业绩|成功案例|项目案例', title):
        return 'other_documents'
    # 资格/历史履约证明不是本次拟实施技术方案；同时要求标题和证据形态明确，
    # 不把“人员资格与培训安排”等技术内容一并排除。
    if (re.search(r'资格(?:条件|要求|审查|业绩)|历史业绩|类似项目业绩', title)
            and re.search(r'合同.{0,10}(?:关键页|扫描件|证明)|结算材料|营业执照|资格证明', str(row.get('content') or ''))):
        return 'other_documents'
    # 知识产权/成果归属是采购公告与合同中的商务条款，不进技术标目录、正文和响应核验；
    # 技术方案里提到它只是收尾承诺，不代表这一章以它为主题。
    if re.search(r'知识产权|成果归属|专利归属', title):
        return 'other_documents'
    if re.search(r'最高限价|报价超限|投标报价|价格评分|付款方式|保证金|资格.{0,4}不符|响应人资格|投标人资格|营业执照|业绩证明|密封|装订|递交|签章|投标上传|设备一致性|串通投标|串通响应|虚假响应|恶意低价|最低评审价|最低价法|最低投标价|联合体',title):
        return 'other_documents'
    content,deferred=review_content(row)
    if deferred and not content:return 'compilation_check'
    return 'technical'


def requirement_signature(project_id, rows=None):
    from app.services.lot_service import selected_lot_code
    lot=selected_lot_code(project_id)
    data={'review_version':REVIEW_VERSION,'lot':lot,
          'requirements':requirements(project_id) if rows is None else rows}
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
    from app.services.project_understanding import chapter_candidates
    from app.services.export_validation import current_validation
    exported=current_validation(project_id,current)
    sources=source_files(project_id)
    items=[]
    for requirement in rows:
        previous=saved.get(requirement['id'],{})
        # 候选章节只用于展示要求落在哪些章节，不参与核验范围：证据在全稿中找。
        targets=[{'node_id':n['id'],'chapter':n['title']} for n in chapter_candidates(requirement,nodes)]
        scope=response_scope(requirement)
        status=previous.get('status','unverified') if scope=='technical' else 'not_applicable'
        gap=previous.get('gap') or ''
        if status=='unverified' and not gap:
            gap=('未取得本条有效核验结果，请人工核对或重新核验' if fresh else
                 '尚未核验；请执行条款核验' if not cached else
                 '内容、编制设置或检查规则已变化，旧证据不再作为当前依据，请重新核验')
        if scope=='other_documents':gap='请在报价、资格或商务附件、合同条款、递交材料等对应文件中核查。本项不计入技术正文缺项，仍须遵守招标要求。'
        if scope=='compilation_check':
            status='unverified'
            gap='本条为编制/版式或附件引用指引，不凭正文声明判断通过。请核对附件原文、排版设置与实际导出；附件中的具体技术和评分要求须分别检查覆盖。提交、装订等事项在递交前核查。系统未据此认定格式合格。'
        from app.services.review_artifacts import checks
        artifact_checks=checks(requirement) if scope=='technical' else []
        body_status=status
        if artifact_checks and exported:
            artifact_checks=[dict(check,status='verified',note='当前版本最终文件中的章节页码已核对；不代表真实证明材料或全部格式已验收。') for check in artifact_checks]
        if artifact_checks and status=='covered' and not exported:
            status='pending_stage'
            gap='已取得索引正文的响应证据，但实际页码仍须在最终排版后回填和核对，尚不能判整项完全满足。'
        elif artifact_checks and status=='covered' and exported:
            gap='索引正文已有响应证据，当前版本导出的章节页码已核对；真实材料充分性与其他格式要求仍需人工检查。'
        items.append({**{k:requirement.get(k) for k in ('title','category','content','weight','source_file','source_location','suggested_chapter')},
                      'requirement_id':requirement['id'],'status':status,
                      'missing_points':previous.get('missing_points',[]) if scope=='technical' else [],'response_scope':scope,'review_state':previous.get('review_state','completed' if previous else 'not_checked') if scope=='technical' else 'separate_check',
                      'compilation_notes':review_content(requirement)[1],
                      'obligations':[],'body_status':body_status,'artifact_checks':artifact_checks,
                      'pending_stages':['export'] if artifact_checks and not exported else [],
                      'source_document':sources.get(requirement.get('source_file')),
                      'evidence':previous.get('evidence',[]),'evidence_verified':previous.get('evidence_verified',False),
                      'gap':gap,
                      'target_nodes':targets,'human_review_required':True})
    technical=[i for i in items if i['response_scope']=='technical']
    counts=Counter(i['status'] for i in technical)
    return {'project_id':project_id,'items':items,'summary':{'total':len(technical),'other_documents':sum(i['response_scope']=='other_documents' for i in items),'compilation_check':sum(i['response_scope']=='compilation_check' for i in items),'review_errors':sum(i['review_state']=='error' for i in technical),**{s:counts[s] for s in ('covered','partial','missing','unverified','pending_stage','not_applicable')}},
            'fresh':fresh,'has_assessment':bool(cached),'checked_at':cached.get('checked_at'),
            'signature':current,'requirements_signature':req_signature,'note':NOTE}


def run_review(project_id):
    with _guard:lock=_locks.setdefault(project_id,threading.Lock())
    if not lock.acquire(blocking=False):raise ValueError('该项目正在核验，请等待本次完成，勿重复提交')
    try:
        from app.services.proposal_quality import review
        return review(project_id)
    finally:lock.release()

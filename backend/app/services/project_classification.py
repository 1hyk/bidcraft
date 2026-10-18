"""领域与业务分层：保留领域硬墙，项目类型只表达业务，不作为素材过滤条件。"""
import json
import re
from app.core.domains import get_domain

LEGACY_INDUSTRIES={'低空','无人机','铁路','公路','房建','市政','水利'}


def read_list(value):
    if isinstance(value,str):
        try:value=json.loads(value)
        except (ValueError,TypeError):return []
    return list(dict.fromkeys(str(x).strip() for x in value if isinstance(x,str) and x.strip())) if isinstance(value,list) else []


def project_types(project):
    if project.get('project_types') is not None:return read_list(project['project_types'])
    old=str(project.get('project_type') or '').strip()
    return [old] if old and old not in LEGACY_INDUSTRIES else []


def validate_types(values,domain):
    result=read_list(values)
    options=get_domain(domain).project_types
    if len(result)>8 or any(v not in options for v in result):raise ValueError('请选择该知识领域下的项目类型')
    return result


def validate_objects(values):
    result=read_list(values)
    if len(result)>30 or any(len(v)>40 for v in result):raise ValueError('技术对象最多30项，每项不超过40字')
    return result


def present(row):
    row=dict(row)
    row['project_types']=project_types(row)
    row['technical_objects']=read_list(row.get('technical_objects'))
    row['legacy_project_type']=row.get('project_type') if row.get('project_type') in LEGACY_INDUSTRIES else ''
    return row


def suggest_from_text(text,domain):
    rules={
        '飞行测试验证':r'实飞|飞行试验|飞行测试|指令验证',
        '软件测试':r'软件测试|功能测试|接口测试|性能测试|自动化测试|平台.{0,15}(?:验证|测试)|(?:测试|验证).{0,15}平台',
        '平台软件开发':r'软件开发|平台开发|系统开发|程序开发',
        '系统集成与部署':r'系统集成|安装部署|机巢部署|部署.{0,8}调试|联调',
        '运维与技术保障':r'运维|售后服务|质保|技术支持|技术保障',
    }
    evidence=[]
    for label in get_domain(domain).project_types:
        rule=rules.get(label)
        if not rule:continue
        match=re.search(rule,text)
        if match:evidence.append({'type':label,'excerpt':text[max(0,match.start()-25):match.end()+45]})
    objects=[word for word in ('无人机','机巢','监视设备','调度平台','演示平台','智感平台','飞控系统') if word in text]
    return {'project_types':[e['type'] for e in evidence],'technical_objects':objects,'evidence':evidence,
            'note':'根据当前标段已解析招标内容给出建议，需确认；未命中不代表不涉及，不自动更改知识领域。'}


def suggestions(project_id):
    from app.core.database import query,query_one
    from app.services.requirement_service import list_requirements, _keep_for_lot
    from app.services.lot_service import selected_lot_code
    project=query_one('SELECT * FROM project WHERE id=%s',(project_id,))
    if not project:raise ValueError('项目不存在')
    lot=query_one('SELECT id FROM lot WHERE project_id=%s AND selected=1 ORDER BY id LIMIT 1',(project_id,))
    code=selected_lot_code(project_id)
    reqs=[r for r in list_requirements(project_id) if r.get('status')!='ignored']
    rows=query("SELECT i.content,i.applies_lots AS applicable_lots FROM file_section_index i JOIN project_file f ON f.id=i.file_id WHERE i.project_id=%s AND f.category IN ('tender','clarification') AND (f.lot_id IS NULL OR f.lot_id=%s) ORDER BY i.id",(project_id,lot['id'] if lot else None))
    text='\n'.join([r['title']+' '+r['content'] for r in reqs]+[r.get('content') or '' for r in rows if _keep_for_lot(r,code)])
    return suggest_from_text(text,project['domain'])

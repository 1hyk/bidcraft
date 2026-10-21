"""领域与业务分层：保留领域硬墙，项目类型只表达业务，不作为素材过滤条件。"""
import json
from app.core.domains import get_domain

SERVICE_TYPES = ('测试验证', '开发建设', '集成部署', '运维保障', '维修保养', '咨询评估', '数据服务', '安全服务', '培训服务', '产品供货')

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
    get_domain(domain)  # 校验行业；实际业务名称不受行业候选词表限制。
    if len(result)>8 or any(len(v)>60 for v in result):raise ValueError('服务类型最多8项，每项不超过60字')
    return result


def validate_objects(values):
    result=read_list(values)
    if len(result)>30 or any(len(v)>40 for v in result):raise ValueError('技术对象最多30项，每项不超过40字')
    return result


def present(row):
    row=dict(row)
    row['project_types']=project_types(row)
    row['service_content']=row.get('service_content') or ''
    row['technical_objects']=read_list(row.get('technical_objects'))
    row['legacy_project_type']=row.get('project_type') if row.get('project_type') in LEGACY_INDUSTRIES else ''
    return row




def suggestions(project_id, refresh=False):
    from app.services.project_brief import suggestions as extract
    return extract(project_id, refresh=refresh)

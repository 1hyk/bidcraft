"""配图规划校验：表格、关系图、插图各走一条链路，图名与素材 ID 同源。"""
import re

RELATION = re.compile(r'流程|架构|组织图|时序|甘特|横道|进度计划|拓扑|时间轴|思维导图|因果|逻辑图|关系图|切换示意图|自动切换|(?<![A-Za-z])WBS(?![A-Za-z])|工作分解|任务分解|功能(?:模块|点)分布|模块关系|生命周期|批次划分|权限关系|分类分级', re.I)
TABLE = re.compile(r'矩阵|对照表|清单表|追溯表|参数表|指标表|映射表|分类表|分级表|汇总表|跟踪表|记录表|统计表')


def normalize_outline(outline: dict, assets=()) -> dict:
    result = dict(outline)
    by_id = {a['asset_id']: a for a in assets}
    images, tables = [], list(result.get('table_needs') or [])
    for raw in result.get('image_needs') or []:
        if not isinstance(raw, dict) or not str(raw.get('name') or '').strip():
            continue
        need = dict(raw)
        need['name'] = str(need['name']).strip()
        # 必须真实提供的材料，不可被名称中的“流程/表”等词降级成合成材料。
        real_table = re.search(r'截图|扫描|原件|证照|证书|实测|签字|盖章|客户提供|采购人提供|真实材料|现有材料', need['name']+' '+str(need.get('purpose') or ''))
        if need.get('source') == 'material' and (not TABLE.search(need['name']) or real_table):
            need['allow_generated'] = False
        elif TABLE.search(need['name']):
            tables.append({'name': need['name'], 'purpose': need.get('purpose') or ''})
            continue
        elif RELATION.search(need['name']+' '+str(need.get('purpose') or '')+' '+str(need.get('prompt') or '')):
            # 场景/空间中的实物位置可以画插图；纯数据节点关系必须 Mermaid。
            spatial = re.search(r'三维|空间分布|平面布局|场景', str(need.get('prompt') or ''))
            if not spatial or re.search(r'切换示意图|自动切换',need['name']):
                need.update(source='mermaid', allow_generated=False, prompt='')
        asset_id = need.get('asset_id')
        if not asset_id and need.get('source')=='history':
            exact=[a for a in assets if a['caption']==need['name']]
            if len({a['asset_id'] for a in exact})==1:
                asset_id=exact[0]['asset_id']
                need['asset_id']=asset_id
        if asset_id:
            asset = by_id.get(asset_id)
            if asset and need.get('source') not in {'material', 'mermaid'}:
                need.update(name=asset['caption'], source='history', allow_generated=False)
            else:
                need.pop('asset_id', None)
        images.append(need)
    result.update(image_needs=images, need_image=bool(images))
    if tables:
        result.update(table_needs=tables, need_table=True)
    return result


def outline_assets(project_id: int, title: str, reqs: str = '') -> list[dict]:
    from app.services.image_retrieval import catalog, candidates
    return candidates({'title': title, 'outline': {'thinking': reqs, 'need_image': True}},
                      catalog(project_id), limit=8)

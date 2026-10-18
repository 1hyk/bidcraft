"""配图规划校验：表格、关系图、插图各走一条链路，图名与素材 ID 同源。"""
import re


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
        if need.get('source') == 'material':
            need['allow_generated'] = False
        elif re.search(r'矩阵|对照表|清单表|追溯表|参数表|指标表', need['name']):
            tables.append({'name': need['name'], 'purpose': need.get('purpose') or ''})
            continue
        elif re.search(r'流程|时序|甘特|横道|组织架构|拓扑|时间轴', need['name']):
            # 场景/空间中的实物位置可以画插图；纯数据节点关系必须 Mermaid。
            spatial = re.search(r'三维|空间分布|平面布局|场景', str(need.get('prompt') or ''))
            if not spatial:
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

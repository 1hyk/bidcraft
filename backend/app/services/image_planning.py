"""配图规划校验：表格、关系图、插图各走一条链路，图名与素材 ID 同源。"""
import json
import re

RELATION = re.compile(r'流程|架构|组织图|时序|甘特|横道|进度计划|拓扑|时间轴|思维导图|因果|逻辑图|关系图|切换示意图|自动切换|(?<![A-Za-z])WBS(?![A-Za-z])|工作分解|任务分解|功能(?:模块|点)分布|模块关系|生命周期|批次划分|权限关系|分类分级', re.I)
TABLE = re.compile(r'矩阵|对照表|清单表|追溯表|参数表|指标表|映射表|分类表|分级表|汇总表|跟踪表|记录表|统计表|排班(?:示意图|表|安排)$|轮班(?:表|安排图)$|值班表')
DATA_CHART = re.compile(r'数据.*(?:对比图|分析图)|准确性.*(?:图表|曲线)|误差.*(?:分布图|直方图)|统计图|折线图|柱状图|饼图')
SPATIAL_LAYOUT = 'spatial_layout'
PHYSICAL_SCENE = 'physical_scene'
_SPATIAL_STRUCTURES = {'spatial_scene', PHYSICAL_SCENE, SPATIAL_LAYOUT}


def spatial_style(need):
    """空间内容的画风由“信息靠什么承载”决定，不由图名关键词决定。

    spatial_layout：边界、分区、航线、覆盖范围等现实中没有可见实体的信息，
    必须靠图示符号表达，写实照片去掉符号后无法说明内容。
    physical_scene：信息在真实物体和场地的外观与摆位里，写实场景是默认表达。
    旧记录只有 spatial_scene 时不做语义猜测，保留其显式风格。
    """
    structure = need.get('data_structure')
    if structure == SPATIAL_LAYOUT:
        return 'spatial_illustration'
    if structure == PHYSICAL_SCENE:
        return 'photorealistic'
    return None


def regeneration_style(need, feedback_style='', previous_style=''):
    """重生成画风：默认按语义分类，用户明确要求时以用户判断为准。

    语义分类是规划阶段模型给的判断，可能判断错；用户对这张图明确说“要实物/写实”
    时允许改画成实景图（会在成图说明里提示原有空间信息未保留）。没提画风时，
    spatial_layout 仍按图示符号承载信息 → 示意图。
    旧记录没有语义分类时，尊重原标签和用户明确要求，不再按图名猜。
    """
    structure = need.get('data_structure')
    if structure == SPATIAL_LAYOUT:
        return feedback_style or 'spatial_illustration'
    base = previous_style or spatial_style(need) or 'photorealistic'
    return feedback_style or base


def legacy_tabular(need):
    """兼容缺结构字段的旧思路：表题且用途明确要求列出多个字段。"""
    return bool(str(need.get('name') or '').strip().endswith('表')
                and re.search(r'(?:列明|列出|逐项记录)[^。]{0,100}[、，,]', str(need.get('purpose') or '')))


def representation(need):
    """优先使用内容结构；名称规则仅兼容尚未重新生成思路的旧记录。"""
    if need.get('content_origin') == 'external_evidence':
        return 'material'
    structure = need.get('data_structure')
    if structure in {'records', 'assignment', 'comparison', 'measurements'}:
        return 'table'
    if structure == 'timeline':
        return 'gantt'
    if structure in {'process', 'hierarchy', 'relationships'}:
        return 'mermaid'
    if structure in _SPATIAL_STRUCTURES:
        return 'illustration'
    return None


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
        mode = representation(need)
        if mode == 'material' or real_table:
            need.update(source='material',allow_generated=False)
        elif mode == 'table':
            tables.append({'name':need['name'],'purpose':need.get('purpose') or '',
                           'data_structure':need['data_structure']})
            continue
        elif mode in {'gantt','mermaid'}:
            need.update(source='mermaid',allow_generated=False,prompt='',diagram_type=mode)
        elif mode == 'illustration':
            # 空间示意保留历史优先/允许生成设置，不被图名中的关系词改变。
            # 分类来自规划阶段对“信息载体”的判断：信息靠图示符号承载的必须用平面示意。
            if need.get('data_structure') == SPATIAL_LAYOUT:
                need['visual_style'] = 'spatial_illustration'
            elif need.get('data_structure') == PHYSICAL_SCENE and not need.get('visual_style'):
                need['visual_style'] = 'photorealistic'
        elif need.get('source') == 'material' and (not (TABLE.search(need['name']) or legacy_tabular(need)) or real_table):
            need['allow_generated'] = False
        elif TABLE.search(need['name']) or DATA_CHART.search(need['name']) or legacy_tabular(need):
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


# ---------------------------------------------------------------- 项目级配图偏好

# 规划阶段默认按「信息载体」判断画风（见 spatial_style）。项目可以再标一条偏好，
# 对现场/实物内容优先考虑写实场景图。偏好只影响规划倾向，**不能推翻语义硬约束**：
# 信息靠分区、航线、覆盖范围等图示符号承载时仍必须是 spatial_layout。白名单之外
# 的键一律丢弃，避免库里留一份没人读的配置。

DEFAULT_POLICY = {'prefer_photorealistic_scene': False}


def normalize_policy(raw=None) -> dict:
    """宽松解析（读库/收请求共用）：坏值回到默认，键做白名单。"""
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        data = {}
    return {key: bool(data.get(key, default)) for key, default in DEFAULT_POLICY.items()}


def outline_preference(project_id: int | None) -> str:
    """给编写思路提示词追加项目偏好；未设置时返回空串。"""
    if project_id is None:
        return ''
    from app.core.database import query_one
    row = query_one('SELECT image_policy FROM project WHERE id = %s', (project_id,))
    if not normalize_policy((row or {}).get('image_policy'))['prefer_photorealistic_scene']:
        return ''
    return ('【项目配图偏好】本项目标书正文涉及真实现场、设备、场地或作业过程时，优先规划写实场景图'
            '（data_structure=physical_scene，source=illustration，visual_style=photorealistic），'
            '让读者看到真实物体及其摆位。仍按信息载体判断：分区、航线、覆盖范围等现实中不可见的'
            '图示信息用 spatial_layout，不要为了偏好把示意图改成写实图，也不要为凑图新增需求。'
            '同一张图既需要现场实景、又需要航线/覆盖/分区信息时，拆成两张分别规划：'
            '一张写实场景图 + 一张空间信息图，不要合并。')

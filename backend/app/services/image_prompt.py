"""按字段分配提示词预算；修改意见不得被背景挤掉或静默截断。"""
import re


def _clip(text, budget):
    return str(text or '').encode('utf-8')[:max(0,budget)].decode('utf-8',errors='ignore')


def visual_style(need):
    explicit=need.get('visual_style')
    if explicit in {'photorealistic','spatial_illustration'}:return explicit
    feedback=str(need.get('_feedback') or '')
    if re.search(r'(?:^|[，。；\n])\s*(?:请)?(?:改为|改成|采用)(?:插画|卡通|线稿|工程示意)',feedback):
        return 'spatial_illustration'
    return 'photorealistic'


def build_prompt(need, model):
    # 使用UTF-8字节上界保守控制长度，不把字符数冒充服务端Token数。
    budget=1200 if str(model).startswith('qwen-image-2.0') else 700
    feedback=str(need.get('_feedback') or '').strip()
    style=visual_style(need)
    appearance=('Photorealistic proposed physical scene. One coherent camera perspective, realistic scale, materials and lighting. No collage, cutaway, cartoon or floating equipment. ' if style=='photorealistic' else 'Technical spatial illustration with coherent scale and perspective. ')
    head=(appearance+'AI-generated visualization, not documentary evidence. No text, letters, numbers, logos or captions. '
          'Plain unmarked vehicles, equipment and barriers; blank sign faces. '
          'Clear spatial layout, horizontal composition. Not real evidence.\n')
    priority=('Requested changes: '+feedback+'\n') if feedback else ''
    subject='Subject: '+str(need.get('name') or '')+'\n'
    if len((head+priority+subject).encode('utf-8'))>budget:
        raise ValueError('修改意见或图名过长，请缩短后重试；尚未提交付费生图')
    background=str(need.get('prompt') or '')+'。'+str(need.get('purpose') or '')
    if style=='photorealistic':
        # 清理旧模型提示中的风格冲突；保留场景对象，不将画质要求等同于实拍证据。
        background=re.sub(r'(?:风格|画风)(?:为|是|采用|[：:])[^，,。；;\n]*','',background)
        background=re.sub(r'非写实照片|非写实|工程技术示意图|概念插画|卡通风格|非摄影风格','',background)
    # 对象保留，只将会诱导画文字的表面要求改为无字表面；不删除整句场景。
    background=re.sub(r'(?:带有|带|印有|印着|写有|写着|标有)(?:清晰的|醒目的|明显的)?(?:中文|文字|字样|标识|标志|标签|编号|品牌标识|品牌Logo)(?:的)?', '无文字标识的', background, flags=re.I)
    # 只删除独立的标签绘制指令；不再删除包含这些词的整个场景句。
    background=re.sub(r'(?:^|(?<=[，,。；\n]))\s*(?:请)?(?:标注|添加文字|添加标签|写上|绘制标题)[^，,。；\n]*','',background)
    background=re.sub(r'[‘“’”]','',background)
    remaining=budget-len((head+priority+subject).encode('utf-8'))
    return head+priority+subject+_clip(background,remaining)

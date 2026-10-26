"""按字段分配提示词预算；修改意见不得被背景挤掉或静默截断。"""
import re


def _clip(text, budget):
    return str(text or '').encode('utf-8')[:max(0,budget)].decode('utf-8',errors='ignore')


_PHOTO_HINT = re.compile(r'实物图|实物照片|实物|实拍|实景|写真|真实照片|照片质感|写实摄影|写实')
_SPATIAL_HINT = re.compile(r'示意图|图解|插画|卡通|线稿|工程示意|蓝图'
                           r'|非写实|不要写实|不要照片|别用照片|不用照片|不要实物|别用实物')
_NEGATED = re.compile(r'(?:不要|不用|别用|别要|去掉|取消|避免)(?:写实|照片|实拍|实物)[^，,。；;\n]*')


def style_from_feedback(feedback):
    """从用户修改意见判断画风；没有明确画风要求时返回 None。

    “实物图/实拍/写实”是写实意图，“示意图/蓝图/插画”是非写实意图。
    先遮掉“不要写实/别用照片”这类否定句，避免把否定读成肯定。
    """
    text = str(feedback or '').strip()
    if not text:
        return None
    masked = _NEGATED.sub(lambda m: ' ' * len(m.group(0)), text)
    spatial = max((m.start() for m in _SPATIAL_HINT.finditer(text)), default=-1)
    real = max((m.start() for m in _PHOTO_HINT.finditer(masked)), default=-1)
    if spatial < 0 and real < 0:
        return None
    return 'spatial_illustration' if spatial > real else 'photorealistic'


def visual_style(need):
    # 用户对已有图的明确修改意见优先于一切自动标注：语义分类是规划阶段模型给的判断，
    # 可能判断错，也允许用户按实际需要改画风（含把空间信息图改成实景图）。
    # 没有用户意见时，spatial_layout 仍按图示符号承载信息 → 示意图。
    override = style_from_feedback(need.get('_feedback'))
    if override:
        return override
    if need.get('data_structure') == 'spatial_layout':
        return 'spatial_illustration'
    explicit = need.get('visual_style')
    if explicit in {'photorealistic', 'spatial_illustration'}:
        return explicit
    if need.get('data_structure') == 'physical_scene':
        return 'photorealistic'
    return 'photorealistic'


def build_prompt(need, model):
    # 使用UTF-8字节上界保守控制长度，不把字符数冒充服务端Token数。
    budget=1200 if str(model).startswith('qwen-image-2.0') else 700
    feedback=str(need.get('_feedback') or '').strip()
    style=visual_style(need)
    appearance=('Photorealistic proposed physical scene. One coherent camera perspective, realistic scale, materials and lighting. No collage, cutaway, cartoon or floating equipment. No schematic overlays: no dashed or connector lines, arrows, callouts, labels, measurement marks, highlight rings, translucent coverage cones or floating icons; show relations only through real objects and their placement. ' if style=='photorealistic' else 'Technical spatial illustration with coherent scale and perspective. ')
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
        background=re.sub(r'(?:而非|而不是|代替)[^，,。；;\n]*?(?:照片质感|实拍|写实)','',background)
        background=re.sub(r'非写实照片|非写实|非实景照片|非实景|工程技术示意图'
                          r'|技术蓝图风格|蓝图风格|概念插画|卡通风格|非摄影风格','',background)
        # 虚线、箭头、覆盖扇区、发光连线等叠加只是“示意”，会破坏实景感；关系应靠实物摆位表达。
        background=re.sub(r'(?:及|以及|和|与)?(?:用|以|加|添加|画出|画上|标出|标示|标注)?'
                          r'(?:虚线|箭头|引线|连接线|示意线|辅助线|引导线|圈注'
                          r'|(?:监视|探测|通信|雷达|光学|视距|测试|飞行)?(?:覆盖扇区|覆盖范围|信号范围))'
                          r'(?:连接|指向|标出|标示|表示|画出|圈出)?[^。；;\n]*','',background)
        background=re.sub(r'(?:用|以|加|添加|画出|画上|标出|标示|标注)(?:高亮|光晕|光圈)[^。；;\n]*','',background)
        background=re.sub(r'(?:及|以及|和|与|、)\s*(?=[。；;，,])','',background)
        background=re.sub(r'(?:一道|一条|用)?[^，,。；;\n]{0,8}(?:激光束|光束|射线)'
                          r'[^。；;\n]{0,24}(?:连接|连到|指向|射向|锁定)[^。；;\n]*','',background)
    else:
        # 明确了非写实画风时，反向清掉写实措辞，免得模型又画成照片。
        background=re.sub(r'写实(?:航拍|摄影|风格|质感)?|实拍照片|实景照片(?:质感)?'
                          r'|真实照片(?:质感)?|(?:不追求|不强调|不要)?照片质感'
                          r'|真实场景|真实地貌|实景|实物质感|真实','',background)
        # 旧提示常把“示意图”写成“严禁虚线/箭头/网格”，这会把分区和航线一起禁掉。
        background=re.sub(r'(?:严禁|禁止|不要|不得)(?:出现|画|绘制)?[^，,。；;\n]*'
                          r'(?:虚线|箭头|轨迹|网格|覆盖层|标注|发光|连线)[^，,。；;\n]*','',background)
    # 对象保留，只将会诱导画文字的表面要求改为无字表面；不删除整句场景。
    background=re.sub(r'(?:带有|带|印有|印着|写有|写着|标有)(?:清晰的|醒目的|明显的)?(?:中文|文字|字样|标识|标志|标签|编号|品牌标识|品牌Logo)(?:的)?', '无文字标识的', background, flags=re.I)
    # 只删除独立的标签绘制指令；不再删除包含这些词的整个场景句。
    background=re.sub(r'(?:^|(?<=[，,。；\n]))\s*(?:请)?(?:标注|添加文字|添加标签|写上|绘制标题)[^，,。；\n]*','',background)
    background=re.sub(r'[‘“’”]','',background)
    # 清理删除短语后留下的连续标点。
    background=re.sub(r'([，,。；;])\s*[，,。；;]+', r'\1', background)
    remaining=budget-len((head+priority+subject).encode('utf-8'))
    return head+priority+subject+_clip(background,remaining)

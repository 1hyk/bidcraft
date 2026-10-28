"""报告/签署页空白模板用原生表格承载，不交给文生图绘制文字。"""
import json
import re
from app.services.image_planning import TABLE, DATA_CHART

def blank_report_examples(body, template_context=False):
    """仅清空明确标注样例/模板的报告记录表，不改方案安排表或真实报告。"""
    lines=body.splitlines();i=0
    while i<len(lines):
        if not lines[i].lstrip().startswith('|'):
            i+=1;continue
        start=i
        while i<len(lines) and lines[i].lstrip().startswith('|'):i+=1
        block=lines[start:i]
        before=next((x.strip('*# _') for x in reversed(lines[:start]) if x.strip()),'')
        if (len(block)<3 or not (template_context or re.search(r'样例|模板',before))
                or not re.search(r'实测结果|测试结果|问题编号|签字|签署',block[0])):continue
        count=len(block[0].strip().strip('|').split('|'))
        lines[start+2:i]=['| '+' | '.join(['（待填写）']*count)+' |']
        i=start+3
    return '\n'.join(lines)+ ('\n' if body.endswith('\n') else '')


def has_named_table(body, name, strict=False):
    """相同标题或其策略标题下已有表格时复用，避免补出第二张空表。"""
    stem=re.sub(r'\W', '', re.sub(r'表$', '', name))
    # 正文用独立图表标题而非章节标题时，只认紧邻且同名的表格；不猜相似图名。
    escaped=re.escape(name)
    if re.search(r'(?m)^\s*(?:#{1,6}\s+)?(?:\*\*)?'+escaped+r'(?:\*\*)?\s*\n\s*\n?\|[^\n]+\|\s*\n\s*\|\s*:?-',body):
        return True
    for block in re.split(r'(?m)(?=^#{1,6} )',body):
        heading=re.sub(r'\W', '', block.split('\n',1)[0])
        same=stem and (stem in heading or (not strict and any(heading.endswith(stem[-n:]) for n in range(6,len(stem)+1))))
        if same and re.search(r'^\s*\|.*\|\s*$',block,re.M):
            return True
    return False


def remove_redundant_table_markers(body, table_names=()):
    """仅清理已能确定对应同名表格的非素材占位；无表格时保留问题。"""
    def replace(match):
        name=match[1].strip()
        if '素材ID' in name or re.search(r'截图|扫描|原件|证照|证书|实测',name):return match[0]
        return '' if (TABLE.search(name) or name in table_names) and has_named_table(body,name,strict=True) else match[0]
    return re.sub(r'\[图片占位[：:]\s*([^\]]+)\]',replace,body)


def complete(body: str, skips=(), table_names=(), template_context=False) -> str:
    from app.services.llm import llm
    from app.agents.json_util import extract_json
    from app.services.generated_images import _EVIDENCE
    body=remove_redundant_table_markers(blank_report_examples(body,template_context),table_names)
    pattern = re.compile(r'\[图片占位[：:]\s*([^\]]+)\]')
    names = list(dict.fromkeys(m[1].strip() for m in pattern.finditer(body)
        if (m[1].strip() in table_names or re.search(r'模板|签署页样例',m[1]) or TABLE.search(m[1]) or DATA_CHART.search(m[1])) and '素材ID' not in m[1]
        and not re.search(r'扫描|原件|证照|证书',m[1]) and (m[1].strip() in table_names or not _EVIDENCE.search(m[1]))
        and m[1].strip() not in skips))
    for name in names[:2]:
        try:
            if has_named_table(body,name):
                body=re.sub(r'\[图片占位[：:]\s*'+re.escape(name)+r'\]', '（参见本章对应表格）', body)
                continue
            blank=bool(re.search(r'模板|签署页.*(?:样例|版式|格式|示意)|表样例',name) or DATA_CHART.search(name))
            if blank and re.search(r'签署页',name):
                # 空白签署页无需让模型编造人物与事实，也不必外传正文。
                table='| 字段 | 填写内容 |\n| --- | --- |\n'+ '\n'.join(
                    '| '+field+' | （待填写） |' for field in ['结论','遗留事项','签署单位','签署人及职务','签字或盖章','签署日期'])
                marker=re.compile(r'\[图片占位[：:]\s*'+re.escape(name)+r'\]')
                body=marker.sub(lambda m:'\n\n'+name+'（空白模板）\n\n'+table+'\n\n',body,count=1)
                continue
            raw=llm.chat([
                {'role':'system','content':'为技术方案补充可编辑表格。只输出JSON {"markdown":"Markdown表格"}。'
                 + ('只列字段名称和（待填写）空白值。' if blank else '根据本章已明确提出的方案安排填写表格；缺少依据的事实或数量填（待确认），不得假设实测结果。')
                 + '不得填造姓名、日期、编号、实测数据、结论、签字或印章。'
                 '不要图片、HTML、代码围栏或外链。模板必须与给定章节用途一致。'},
                {'role':'user','content':json.dumps({'模板名称':name,'章节内容':body},ensure_ascii=False)}
            ],temperature=0,max_tokens=2500)
            table=str(extract_json(raw).get('markdown') or '').strip()
            # 模型常用标准单元格换行；转为文本分隔，不能因此拒掉整张方案表。
            table=re.sub(r'<br\s*/?>','；',table,flags=re.I)
            if len(re.findall(r'^\s*\|.*\|\s*$',table,re.M))<3 or any(s in table for s in ['```','<','http','![']):
                continue
            marker=re.compile(r'\[图片占位[：:]\s*'+re.escape(name)+r'\]')
            label='（空白模板，实际内容及签署待填写）' if blank else '（方案表，具体执行内容需确认）'
            body=marker.sub(lambda m:'\n\n'+name+label+'\n\n'+table+'\n\n',body,count=1)
        except Exception as exc:
            print(f'[模板补齐] {name} 未完成：{type(exc).__name__}')
    return body

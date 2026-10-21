"""当前投标文件明确的最终成品字段提醒，不将履约任务转成已完成事实核查。"""
import re


def checks(row):
    text=str(row.get('content') or '')
    # 仅处理明确的编制要求。技术性能、未来报告编排与履约交付不在这里分流。
    if row.get('category')!='编制要求' or not re.search(r'索引|响应表|评分.{0,10}目录',str(row.get('title') or '')+text):
        return []
    result=[]
    for match in re.finditer(r'(?:填写|注明|标注|标明|列明)[^。；（）()\n]{0,45}(?:所在页码|实际页码|对应页码)',text):
        if re.search(r'无需|无须|不用|不需|不要求|可不',text[max(0,match.start()-8):match.start()]):continue
        result.append({'kind':'final_page_reference','quote':match[0],'status':'pending',
                       'note':'索引建立与章节对应检查正文；实际页码须在最终排版回填并核对，当前未自动验收。'})
    return result


def body_content(row, content):
    for check in checks(row):content=content.replace(check['quote'],'')
    return re.sub(r'[（(]\s*[）)]','',content)

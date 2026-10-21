"""正式图注保留来源与真实性说明，核对状态仅属于工作台。"""
import re


def display_caption(value):
    text=str(value or '').strip()
    text=re.sub(r'([（(])AI生成示意[，,、\s]*待人工核对([）)])',r'\1AI生成示意\2',text)
    return re.sub(r'[（(]待人工核对[）)]','',text).strip()


def reference_caption(value, asset):
    text=display_caption(value)
    return re.sub(r'[（(](?:AI生成示意|数据仅作示例，非实测结果)[）)]','',text).strip()

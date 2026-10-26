"""正式图注保留来源与真实性说明，核对状态仅属于工作台。"""
import re


def display_caption(value):
    text=str(value or '').strip()
    text=re.sub(r'([（(])AI生成示意[，,、\s]*待人工核对([）)])',r'\1AI生成示意\2',text)
    return re.sub(r'[（(]待人工核对[）)]','',text).strip()


def reference_caption(value, asset):
    text=display_caption(value)
    return re.sub(r'[（(](?:AI生成示意|数据仅作示例，非实测结果)[）)]','',text).strip()


def print_caption(value):
    """导出件图注：不带「AI生成示意」这类内部生成方式标记，其余来源说明保留。

    标记可能与别的说明挤在同一个括号里（如「AI生成示意，数据仅作示例」），也可能
    出现在占位文字或待补清单里；导出件一律只删标记本身，保留其余来源说明。
    """
    text=re.sub(r'AI\s*生成示意(?:\s*[，,、]\s*待人工核对)?\s*[，,、]?\s*','',display_caption(value))
    text=re.sub(r'[（(]\s*[）)]','',text)   # 删完标记只剩空括号时一并去掉
    return text.strip()

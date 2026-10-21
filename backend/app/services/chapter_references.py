"""仅修正汇总索引中的实际子章节引用，不消除真实证明材料缺项。"""
import re


def normalize_index(body: str, child_titles: list[str]) -> str:
    from app.core.chapter_title import normalize
    names = {normalize(name): name for name in child_titles}
    changed = False

    def replace(match):
        nonlocal changed
        title = names.get(normalize(match.group(1)))
        if not title:
            return match.group(0)
        changed = True
        return f'详见“{title}”章节（页码见最终目录）'

    result = re.sub(r'\[待补充[：:]\s*([^\]\n]+)\]', replace, body)
    # 仅针对本次确实修正了章节引用的索引，不泛改其他证明材料表。
    if changed:
        result = result.replace('证明材料所在页码', '响应章节及页码')
    return result

"""用原文连续片段定位要求；不以重复编号或模型出处作为命中依据。"""
import re


def normalize(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text or '')


def locate(requirement, sections):
    content = re.split(r'[（(]注[：:]', requirement.get('content') or '')[0]
    anchors = [normalize(s) for s in re.split(r'[；;。\n]', content)]
    anchors = [s for s in anchors if len(s) >= 10]
    # 抽取摘要常合并表格多行；只拆分明确分隔符，仍要求连续原文，不能模糊改字。
    parts = [normalize(re.sub(r'^\s*\d+[.、．]\s*', '', s))
             for s in re.split(r'[；;。\n，,：:]', content)]
    parts = [s for s in parts if len(s) >= 10]
    if not anchors:
        return {'matched': False, 'message': '要求缺少足够长的连续原文，请手动选择章节核对。'}
    candidates = []
    for section in sections:
        text = normalize(section.get('content'))
        hits = [a for a in anchors if a in text]
        if not hits:
            hits = [a for a in parts if a in text]
        if hits:
            candidates.append((sum(map(len, hits)), section))
    if not candidates:
        return {'matched': False, 'message': '未找到可确认的连续原文匹配，请手动选章或下载原件核对。'}
    best = max(score for score, _ in candidates)
    winners = [s for score, s in candidates if score == best]
    # 父章可能包含子章全文：只在一个最小章节包含于其他命中章时消歧。
    winners.sort(key=lambda s: len(s.get('content') or ''))
    first = winners[0]
    if any(normalize(first.get('content')) not in normalize(s.get('content')) for s in winners[1:]):
        return {'matched': False, 'message': '多个章节包含相同条款，暂不自动选定，请人工核对。'}
    # 返回真实段落与相邻段落，不把归一化文本或要求摘要冒充原文。
    blocks = [line for line in (first.get('content') or '').splitlines() if line.strip()]
    hit_indexes = [i for i, block in enumerate(blocks) if any(a in normalize(block) for a in anchors + parts)]
    indexes = {j for i in hit_indexes for j in range(max(0, i-1), min(len(blocks), i+2))}
    # 表格命中保留完整表格（含表头），否则片段会被渲染为残缺的 Markdown。
    for i in hit_indexes:
        if blocks[i].lstrip().startswith('|'):
            start=end=i
            while start>0 and blocks[start-1].lstrip().startswith('|'):start-=1
            while end+1<len(blocks) and blocks[end+1].lstrip().startswith('|'):end+=1
            indexes.update(range(start,end+1))
    indexes = sorted(indexes)
    excerpt = '\n'.join(blocks[i] for i in indexes)
    full = normalize(content) in normalize(first.get('content'))
    return {'matched': True, 'section_id': first['id'], 'excerpt': excerpt, 'match_type': 'full' if full else 'partial',
            'message': ('要求全文连续匹配。' if full else '按要求中的部分连续原文片段定位，摘要全文未逐字对应；不代表原文已支持摘要全部内容。')
                       + '以下显示命中段落及前后文，可切换查看整章核对。'}

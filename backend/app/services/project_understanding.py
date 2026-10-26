"""项目理解与本机公司档案。独立元数据，不保存或替代编辑器正文。"""
import hashlib
import json
import threading
import uuid
import re
from pathlib import Path
from datetime import datetime, timezone

from app.core.config import settings

_lock = threading.RLock()


def root():
    from app.core.config import PROJECT_ROOT
    path = PROJECT_ROOT / 'data' / 'company_workspace'
    path.mkdir(parents=True, exist_ok=True)
    return path


def read(name, default):
    path = root() / name
    if not path.exists():
        legacy = Path(settings.uploads_dir) / 'company_workspace' / name
        if legacy.exists():
            value = json.loads(legacy.read_text(encoding='utf-8'))
            write(name,value)
            return value
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write(name, value):
    with _lock:
        path = root() / name
        tmp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)


def company():
    from app.services.company_materials import profile
    return profile()


def company_context():
    profile = company()
    confirmed = [e for e in profile.get('entries', []) if e.get('confirmed') is True and not e.get('conflict') and not e.get('expired') and (not e.get('doc_id') or e.get('source_status') == 'extracted')]
    selected=[];size=0
    for entry in confirmed:
        cost=len(json.dumps(entry,ensure_ascii=False,default=str))
        if size+cost>18000:continue
        selected.append(entry);size+=cost
    confirmed=selected
    return ('【本公司资料与承诺边界】\n公司名称：' + profile.get('name', '')
            + '\n业务定位（不构成具体资质或能力证明）：' + profile.get('positioning', '')
            + '\n用户确认的资料：' + json.dumps(confirmed, ensure_ascii=False, default=str)
            + '\n仅上述已确认资料可作为公司事实依据；历史标书只参考方法，不继承其人员、业绩、资质、设备和承诺。'
              '招标要求不是我方能力证明；招标要求应明确响应并写明落实措施；岗位职责、流程、进度可生成拟实施方案，不因缺公司材料留空。仅缺少招标明确要求的真实资质、人员身份、业绩或实测证明时标注待补材料。不得将拟实施安排表述为已经具备的事实。'
              '应用行业不代表我方具有该行业的生产、制造、施工或运营资质。')


def requirement_group(row):
    """编制约束和通用投标规则不要求单独关联技术正文章节。"""
    from app.services.requirement_matrix import response_scope
    if row.get('category') == '编制要求':
        return 'document_rules'
    scope = response_scope(row)
    if scope == 'other_documents':
        return 'bid_notices'
    if scope == 'compilation_check':
        return 'document_rules'
    if row.get('category') == '废标风险' and re.fullmatch(
            r'实质性条款(?:响应(?:无效|不满足)?|不满足|符合性|要求|偏离)', str(row.get('title') or '').strip()):
        return 'bid_notices'
    return 'technical'


def chapter_candidates(row, nodes):
    """先按抽取标题定位；无结果时，仅接受要求原文明确点名的现有章节。"""
    from app.core.chapter_title import related, normalize
    if requirement_group(row) != 'technical':
        return []
    targets = [n for n in nodes if related(row.get('suggested_chapter') or row['title'], n['title'])]
    if not targets:
        content = normalize(row.get('content') or '')
        targets = [n for n in nodes if len(normalize(n['title'])) >= 6 and normalize(n['title']) in content]
    result = [{'id':n['id'],'title':n['title'], 'basis':'要求标题或原文与章节标题对应'} for n in targets]
    if result:
        return result
    # 连续词片段只用于推荐位置，不构成响应结论；不设行业或条款专用词表。
    content = re.sub(r'[（(][^）)]*(?:标记|关键项|实质性)[^）)]*[）)]', '', row.get('content') or '')
    text = normalize((row.get('title') or '') + content)
    title = normalize(row.get('title') or '')
    title_grams = {title[i:i+2] for i in range(max(0, len(title)-1))}
    grams = {text[i:i+3] for i in range(max(0, len(text)-2))}
    ranked = []
    for n in nodes:
        outline = n.get('outline') or {}
        if isinstance(outline, str):
            try: outline = json.loads(outline)
            except (ValueError, TypeError): outline = {}
        evidence = str(outline.get('thinking') or '') + '；' + '；'.join(outline.get('key_points') or [])
        normalized = normalize(evidence)
        hits = sorted(g for g in grams if g in normalized)
        if len(hits) < 3:
            continue
        # 展示原始思路片段供人复核，避免只给不可解释的相似度。
        sentences = re.split(r'[。\n]', evidence)
        snippet = next((s for s in sentences if any(g in normalize(s) for g in hits)), evidence)
        topical = sum(g in normalize(n['title']) for g in title_grams)
        score = len(hits) + topical * 8
        if outline.get('_writing_role') == 'introduction':
            score *= .5
        ranked.append((score, {'id':n['id'], 'title':n['title'], 'basis':snippet[:260]}))
    ranked.sort(key=lambda item: (-item[0], item[1]['id']))
    return [item[1] for item in ranked[:3]]


def coverage_gaps(requirements, nodes):
    """找出连候选章节都没有的技术要求。

    章节对应只用来提示目录缺口：没有候选章节说明目录可能漏项，需要补/调目录，
    不是让用户逐条安排章节。有候选也不代表正文已经响应。
    """
    return [r for r in requirements
            if requirement_group(r) == 'technical' and not chapter_candidates(r, nodes)]


def snapshot(project_id):
    from app.core.database import query_one, query
    from app.services.requirement_service import list_requirements
    from app.services.lot_service import selected_lot_code
    from app.services.project_classification import present
    from app.services.section_service import generation_node_ids
    from app.core.chapter_title import related
    project = query_one('SELECT * FROM project WHERE id = %s', (project_id,))
    if not project:
        raise ValueError('项目不存在')
    project = present(project)
    lot = selected_lot_code(project_id) or ''
    requirements = [r for r in list_requirements(project_id) if r.get('status') != 'ignored']
    allowed = generation_node_ids(project_id)
    nodes = [n for n in query('SELECT id, title, outline FROM chapter_node WHERE project_id = %s ORDER BY sort_order, id', (project_id,)) if n['id'] in allowed]
    from app.services.requirement_matrix import response_scope
    for r in requirements:
        r['response_scope'] = response_scope(r)
        r['display_group'] = requirement_group(r)
        r['chapter_candidates'] = chapter_candidates(r,nodes)
        r['has_corresponding_chapter'] = bool(r['chapter_candidates'])
    gaps = coverage_gaps(requirements, nodes)
    technical = [r for r in requirements if r['display_group'] == 'technical']
    selected = query_one('SELECT id FROM lot WHERE project_id = %s AND selected = 1', (project_id,))
    files = query("SELECT id, file_name, status, lot_id FROM project_file WHERE project_id = %s AND category IN ('tender','clarification') ORDER BY id", (project_id,))
    if selected:
        files = [f for f in files if f.get('lot_id') in (None, selected['id'])]
    payload = {'lot':lot, 'domain':project.get('domain'), 'types':project.get('project_types'),
               'service_content':project.get('service_content'), 'objects':project.get('technical_objects'), 'requirements':requirements, 'files':files}
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    record = read(f'project_{project_id}_{hashlib.sha1(lot.encode()).hexdigest()[:12]}.json', {})
    return {'project':project, 'requirements':requirements, 'files':files, 'lot':lot,
            'directory_nodes':[{'id':n['id'],'title':n['title']} for n in nodes],
            'has_directory': bool(nodes),
            # 只用于目录覆盖检查：有多少条技术要求找不到承接章节，以及具体是哪几条。
            'coverage':{'technical':len(technical), 'unmatched':len(gaps),
                        'requirement_ids':[r['id'] for r in gaps]},
            'fingerprint':fingerprint, 'notes':record.get('notes',''),
            'confirmed':record.get('fingerprint') == fingerprint,
            'confirmed_at':record.get('confirmed_at'),
            'warning':'章节候选只用于检查目录是否漏项，不代表正文已响应；请在成稿检查中核对。'}


def confirm(project_id, fingerprint, notes):
    current = snapshot(project_id)
    if not current['requirements']:
        raise ValueError('尚无已解析招标要求，请先完成解析')
    if current['fingerprint'] != fingerprint:
        raise ValueError('招标内容、分类或目录已经变化，请刷新后重新确认')
    name = f"project_{project_id}_{hashlib.sha1(current['lot'].encode()).hexdigest()[:12]}.json"
    write(name, {'fingerprint':fingerprint, 'notes':notes,
                 'confirmed_at':datetime.now(timezone.utc).isoformat()})


def generation_context(project_id):
    info = snapshot(project_id)
    project = info['project']
    scope = ('\n【项目已保存的业务与工作对象】\n实际服务类型：'
             + ('、'.join(project.get('project_types') or []) or '未指定，以招标原文为准')
             + '\n本次服务内容：' + (project.get('service_content') or '未指定，以招标原文为准')
             + '\n工作对象：' + ('、'.join(project.get('technical_objects') or []) or '未指定，以招标原文为准')
             + '\n这些字段用于理解工作范围，不构成我方能力证明；详细任务、参数与验收条件以招标原文为准。')
    return company_context() + scope + ('\n【用户确认的项目理解补充】\n' + info['notes']
                                if info['confirmed'] and info['notes'] else '')

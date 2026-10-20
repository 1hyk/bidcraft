"""项目理解与本机公司档案。独立元数据，不保存或替代编辑器正文。"""
import hashlib
import json
import threading
import uuid
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
    nodes = [n for n in query('SELECT id, title FROM chapter_node WHERE project_id = %s ORDER BY sort_order, id', (project_id,)) if n['id'] in allowed]
    for r in requirements:
        r['chapter_candidates'] = [{'id':n['id'], 'title':n['title']} for n in nodes
                                   if related(r.get('suggested_chapter') or r['title'], n['title'])]
    selected = query_one('SELECT id FROM lot WHERE project_id = %s AND selected = 1', (project_id,))
    files = query("SELECT id, file_name, status, lot_id FROM project_file WHERE project_id = %s AND category IN ('tender','clarification') ORDER BY id", (project_id,))
    if selected:
        files = [f for f in files if f.get('lot_id') in (None, selected['id'])]
    payload = {'lot':lot, 'domain':project.get('domain'), 'types':project.get('project_types'),
               'objects':project.get('technical_objects'), 'requirements':requirements, 'files':files}
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    record = read(f'project_{project_id}_{hashlib.sha1(lot.encode()).hexdigest()[:12]}.json', {})
    return {'project':project, 'requirements':requirements, 'files':files, 'lot':lot,
            'fingerprint':fingerprint, 'notes':record.get('notes',''),
            'confirmed':record.get('fingerprint') == fingerprint,
            'confirmed_at':record.get('confirmed_at'),
            'warning':'章节关联仅为候选位置，不代表正文已响应；请在成稿检查中核对。'}


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
    return company_context() + ('\n【用户确认的项目理解补充】\n' + info['notes']
                                if info['confirmed'] and info['notes'] else '')

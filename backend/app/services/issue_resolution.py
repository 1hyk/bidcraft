"""人工处理记录独立于规则结果；证据变化后原确认失效，不修改正文或覆盖结论。"""
import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from app.core.config import settings

_lock = threading.RLock()
REVIEW_KINDS={'image_review','claim_review','person_role_review','structure_review'}


def can_close(item, action):
    return item.get('kind') in REVIEW_KINDS and action in {'confirmed','false_positive'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _path(project_id):
    return settings.uploads_dir / 'issue_resolutions' / f'project_{int(project_id)}.json'


def records(project_id):
    path = _path(project_id)
    if not path.exists():
        return []
    # 损坏时明确报错，不把丢失记录伪装成空列表。
    return json.loads(path.read_text(encoding='utf-8'))


def annotate(project_id, issues, contexts):
    with _lock:
        history = records(project_id)
    latest = {r['issue_id']: r for r in history}
    active, handled = [], []
    seen = set()
    for raw in issues:
        item = dict(raw)
        key = digest([item.get('node_id'), item['kind'], item.get('excerpt'), item['message']])
        if key in seen:
            continue
        seen.add(key)
        fingerprint = digest([key, contexts.get(item.get('node_id'))])
        item.update(issue_id=key, fingerprint=fingerprint)
        record = latest.get(key)
        if record and record['fingerprint'] == fingerprint and can_close(item,record['action']):
            handled.append(dict(item, resolution=record))
        else:
            item['reopened'] = bool(record and record['action'] != 'reopen')
            if record:
                item['workflow_note']='旧处理未完成实际闭环，请重新处理' if record['action']=='not_applicable' else '请完成正文操作并刷新检查；仍存在的问题不会自动关闭'
            active.append(item)
    # 操作记录与当前正文核对：只有目标素材确实离开正文才认定移除/替换完成。
    from app.services.image_retrieval import selected_ids
    current_keys={i['issue_id'] for i in active+handled}
    audit=[]
    for record in history:
        entry=dict(record)
        if record['action']=='edit_requested':
            context=contexts.get(record.get('node_id'))
            payload=context[0] if isinstance(context,list) and context and isinstance(context[0],dict) else None
            complete=payload is not None and bool((payload.get('body') or '').strip()) and record['issue_id'] not in current_keys
            entry['workflow_status']='completed' if complete else 'pending'
            if complete and latest.get(record['issue_id']) is record:
                handled.append(dict(record,resolution=dict(entry,note=record['note']+'；复查已不再检出原问题')))
        if record['action'] in {'replace_requested','remove_requested'}:
            context=contexts.get(record.get('node_id'))
            payload=context[0] if isinstance(context,list) and context and isinstance(context[0],dict) else None
            current=selected_ids(payload.get('body') or '') if payload is not None else None
            old=set(record.get('before_asset_ids') or [])
            removed=current is not None and record.get('asset_id') not in current
            replacements=sorted(current-old) if removed else []
            complete=removed and (record['action']=='remove_requested' or bool(replacements))
            entry.update(workflow_status='completed' if complete else 'pending',replacement_asset_ids=replacements)
            if complete and latest.get(record['issue_id']) is record and record['issue_id'] not in current_keys:
                handled.append(dict(record,resolution=dict(entry,note=record['note']+'；当前正文已核对操作完成，新图问题另行检查')))
        audit.append(entry)
    return active, handled, audit


def resolve(project_id, issue_id, fingerprint, action, note):
    from app.services.proposal_quality import check
    with _lock:
        report = check(project_id)
        item = next((i for i in report['issues'] + report['handled_issues'] if i['issue_id'] == issue_id), None)
        if item is None or item['fingerprint'] != fingerprint:
            raise ValueError('正文或配图已变化，请刷新问题后重新确认')
        if action not in {'confirmed','false_positive','reopen','replace_requested','remove_requested','edit_requested'}:
            raise ValueError('无效处理方式')
        if action in {'confirmed','false_positive'} and not can_close(item,action):
            raise ValueError('实际缺项仍存在，请先修改正文或补材料，再刷新检查，不能直接关闭')
        if action in {'replace_requested','remove_requested'} and not item.get('asset_id'):
            raise ValueError('未找到可操作的图片，请先定位正文处理')
        if not note.strip():
            raise ValueError('请填写核对依据或处理说明')
        history = records(project_id)
        before_ids=[]
        if action in {'replace_requested','remove_requested'}:
            from app.services.image_retrieval import selected_ids
            from app.services.proposal_quality import chapters
            chapter=next((r for r in chapters(project_id) if r['id']==item['node_id']),None)
            before_ids=sorted(selected_ids(chapter['body'])) if chapter else []
        history.append(dict(item, resolution=None, action=action, note=note.strip(),before_asset_ids=before_ids,
                            checked_at=datetime.now(timezone.utc).isoformat()))
        path = _path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return check(project_id)

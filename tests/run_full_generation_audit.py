"""显式运行的真实端到端验收：新建独立项目→上传→解析→目录思路→正文→导出。"""
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import settings
from app.core.database import query, query_one, execute

source = int(sys.argv[1])
run_dir = ROOT / "uploads" / "_verification" / ("full_run_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
run_dir.mkdir(parents=True)
report = {"source_project": source, "started": datetime.now().isoformat(), "events": []}
client = httpx.Client(base_url="http://127.0.0.1:8000", timeout=1800, trust_env=False)

def fingerprint():
    rows = query('SELECT n.*,c.content FROM chapter_node n LEFT JOIN section_content c ON c.chapter_node_id=n.id WHERE n.project_id=%s ORDER BY n.id', (source,))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()

def save(stage, **values):
    report.update(values)
    event = {"at": datetime.now().isoformat(), "stage": stage, **values}
    report["events"].append(event)
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    brief = {k:v for k,v in event.items() if k not in ('quality','page_plan')}
    if 'quality' in event:
        brief['quality_counts'] = {k:event['quality'].get(k) for k in ('chapters','pending_facts','pending_images')}
    if 'page_plan' in event:
        m = event['page_plan'].get('measurement') or {}
        brief['measurement'] = {k:m.get(k) for k in ('body_pages','within_tolerance','total_pages')}
    print(json.dumps(brief, ensure_ascii=True, default=str), flush=True)

def request(method, url, **kwargs):
    r = client.request(method, url, **kwargs)
    r.raise_for_status()
    return r

def wait_for(label, read, finished, failed):
    deadline = time.monotonic() + 7200
    previous = None
    while time.monotonic() < deadline:
        value = read()
        if value != previous:
            save(label, progress=value)
            previous = value
        if failed(value):
            raise RuntimeError(f"{label}: {value}")
        if finished(value):
            return value
        time.sleep(10)
    raise TimeoutError(label)

before = fingerprint()
try:
    p = request("GET", f"/api/projects/{source}").json()
    body = {k: p.get(k) for k in ("description", "domain", "project_type", "project_types", "technical_objects") if p.get(k) is not None}
    body["name"] = "完整回归测试（项目%d副本）%s" % (source, datetime.now().strftime("%m%d-%H%M"))
    pid = request("POST", "/api/projects", json=body).json()["id"]
    execute('UPDATE project SET docx_style=%s,page_plan=%s WHERE id=%s',
            (p.get("docx_style") if isinstance(p.get("docx_style"), str) else json.dumps(p.get("docx_style")),
             json.dumps({k: v for k, v in (json.loads(p['page_plan']) if isinstance(p.get('page_plan'),str) else p.get('page_plan') or {}).items() if k in ('target_pages','rewrite_tolerance')}), pid))
    save("created", test_project=pid, directory=str(run_dir), original_fingerprint=before)
    files = query('SELECT * FROM project_file WHERE project_id=%s ORDER BY id', (source,))
    for f in files:
        path = settings.uploads_dir / f['file_path']
        with path.open('rb') as stream:
            uploaded = request('POST', f'/api/projects/{pid}/files', params={'category':f['category']}, files={'file':(f['file_name'],stream)}).json()
        fid = uploaded['id']
        save('uploaded', file_id=fid)
        wait_for('parsing', lambda: query_one('SELECT status FROM project_file WHERE id=%s',(fid,)),
                 lambda v:v['status']=='parsed', lambda v:v['status']=='failed')
    request('POST','/api/compile/start',json={'project_id':pid})
    def progress():
        s=request('GET',f'/api/compile/status/{pid}').json()
        return {k:s.get(k) for k in ('stage','progress','total','error')}
    wait_for('compiling',progress,lambda v:v['stage']=='outlines_ready',lambda v:v['stage']=='failed')
    tree=request('GET',f'/api/sections/{pid}/tree').json()
    (run_dir/'tree.json').write_text(json.dumps(tree,ensure_ascii=False,indent=2),encoding='utf-8')
    save('writing_started', batch=request('POST','/api/compile/write_all',json={'project_id':pid}).json())
    wait_for('writing',progress,lambda v:v['stage'] in ('done','outlines_ready'),lambda v:v['stage']=='failed')
    quality=request('GET',f'/api/export/{pid}/check').json()
    save('quality', quality=quality)
    save('exporting')
    result=request('POST',f'/api/export/{pid}/docx')
    (run_dir/'proposal.docx').write_bytes(result.content)
    save('exported', bytes=len(result.content))
    plan=request('GET',f'/api/projects/{pid}/page-plan').json()
    (run_dir/'page_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    quality=request('GET',f'/api/export/{pid}/check').json()
    (run_dir/'final_quality.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
    save('completed', original_unchanged=fingerprint()==before, page_plan=plan)
except BaseException as exc:
    save('failed', error=str(exc), original_unchanged=fingerprint()==before)
    raise

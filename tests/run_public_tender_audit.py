"""显式运行公开招标 PDF 回归，不写入公司库或覆盖原项目。支持复用本次项目。"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from datetime import datetime

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.database import query, query_one

URL = 'https://ruzhou.zfcg.henan.gov.cn/cmsweb81e27e/henan/rootfiles/2026/01/27/86a26b60566940339fff7a3e58a35685.pdf'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=int)
    parser.add_argument('--pages', type=int, default=60)
    args = parser.parse_args()
    directory = ROOT / 'uploads/_verification' / ('public_tender_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    directory.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'uploads/_verification/public_tender_20261019/henan-software-testing.pdf'
    assert source.read_bytes().startswith(b'%PDF'), '需先下载公开原文件'
    report = {'url': URL, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'started': datetime.now().isoformat(), 'target_pages': args.pages, 'events': []}
    def fingerprint(pid):
        data = query('SELECT n.*,c.content FROM chapter_node n LEFT JOIN section_content c ON c.chapter_node_id=n.id WHERE n.project_id=%s ORDER BY n.id', (pid,))
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    before = {pid: fingerprint(pid) for pid in (9, 10, 11)}
    def save(stage, **data):
        report.update(data)
        report['events'].append({'stage': stage, 'at': datetime.now().isoformat()})
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(json.dumps({'stage': stage, **data}, ensure_ascii=True, default=str), flush=True)
    with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False, timeout=1800) as client:
        def request(method, url, **kwargs):
            r = client.request(method, url, **kwargs)
            r.raise_for_status()
            return r
        def wait(label, get, done):
            deadline = time.monotonic() + 7200
            prev = None
            while time.monotonic() < deadline:
                status = get()
                if status != prev:
                    save(label, progress=status)
                    prev = status
                if status.get('stage', status.get('status')) in ('failed', 'cancelled'):
                    raise RuntimeError(str(status))
                if done(status): return
                time.sleep(10)
            raise TimeoutError(label)
        try:
            pid = args.project or request('POST', '/api/projects', json={
                'name': '公开文件回归：河南智慧交通软件测试（独立测试）', 'domain': 'transport',
                'project_types': ['软件测试'], 'technical_objects': ['智慧交通服务云平台'],
                'description': '公开文件回归样本；公司材料为空，不用于实际投标。来源：'+URL,
            }).json()['id']
            save('created', project_id=pid)
            request('PUT', f'/api/projects/{pid}/page-plan', json={'target_pages': args.pages, 'rewrite_tolerance': .15})
            files = request('GET', f'/api/projects/{pid}/files').json()
            if files:
                fid = files[0]['id']
            else:
                with source.open('rb') as stream:
                    fid = request('POST', f'/api/projects/{pid}/files', params={'category': 'tender'},
                                  files={'file': ('河南智慧交通软件测试招标文件.pdf', stream, 'application/pdf')}).json()['id']
            save('uploaded', file_id=fid)
            wait('parsing', lambda: query_one('SELECT status FROM project_file WHERE id=%s', (fid,)), lambda s:s['status']=='parsed')
            request('POST', '/api/compile/start', json={'project_id': pid})
            def progress():
                status = request('GET', f'/api/compile/status/{pid}').json()
                return {k:status.get(k) for k in ('stage','progress','total','error')}
            wait('outline', progress, lambda s:s['stage']=='outlines_ready')
            tree = request('GET', f'/api/sections/{pid}/tree').json()
            (directory/'tree.json').write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding='utf-8')
            started = request('POST', '/api/compile/write_all', json={'project_id': pid}).json()
            save('writing_started', batch=started)
            wait('writing', progress, lambda s:s['stage']=='done')
            save('exporting')
            response = request('POST', f'/api/export/{pid}/docx')
            (directory/'proposal.docx').write_bytes(response.content)
            plan = request('GET', f'/api/projects/{pid}/page-plan').json()
            quality = request('GET', f'/api/export/{pid}/check').json()
            for name, value in [('page-plan',plan), ('quality',quality)]:
                (directory/f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            save('completed', measurement=plan.get('measurement'), originals_unchanged={pid:fingerprint(pid)==sig for pid,sig in before.items()})
        except BaseException as exc:
            save('failed', error=str(exc), originals_unchanged={pid:fingerprint(pid)==sig for pid,sig in before.items()})
            raise


if __name__ == '__main__':
    main()

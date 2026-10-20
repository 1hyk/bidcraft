"""在明确的测试副本中重跑受后续修复影响的节点，不手改正文。"""
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.services import writer_service
from app.core.database import query

if __name__=='__main__':
    pid=int(sys.argv[1])
    rows=query('SELECT id,parent_id,title FROM chapter_node WHERE project_id=%s',(pid,))
    ids={r['id'] for r in rows if any(c['parent_id']==r['id'] for c in rows)
         or r['title'] in {'报告模板','局点实飞演示场景支撑响应','新飞机采购全量指令验证响应'}}
    if len(sys.argv)>2:
        ids={int(i) for i in sys.argv[2:]} & {r['id'] for r in rows}
    print(json.dumps({'project':pid,'nodes':sorted(ids)}),flush=True)
    print(writer_service.rewrite_chapters(pid,sorted(ids)),flush=True)
    while pid in writer_service._batch_running:
        time.sleep(5)
    print('REGENERATION_FINISHED',flush=True)

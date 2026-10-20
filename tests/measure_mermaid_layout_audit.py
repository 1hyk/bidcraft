"""显式调用真实导出与排版服务，仅更新指定测试项目的测量，不重写正文。"""
import hashlib
import json
import sys
from pathlib import Path
from collections import Counter
import pypdfium2 as pdf
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.core.database import query
from app.services.pagination_service import measure
from app.services.proposal_quality import check

def fingerprint(pid):
    rows=query('SELECT n.*,c.content FROM chapter_node n LEFT JOIN section_content c ON c.chapter_node_id=n.id WHERE n.project_id=%s ORDER BY n.id',(pid,))
    return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()

if __name__=='__main__':
    pid=int(sys.argv[1]) if len(sys.argv)>1 else 11
    directory=ROOT/(sys.argv[2] if len(sys.argv)>2 else f'uploads/_verification/mermaid_readability_20261019/project_{pid}')
    directory.mkdir(parents=True,exist_ok=True)
    before={p:fingerprint(p) for p in {9,10,11,pid}}
    actual=measure(pid)
    qa=check(pid)
    path=ROOT/actual['pdf_url'].lstrip('/')
    text=[p.extract_text() or '' for p in PdfReader(path).pages]
    for name,value in [('measurement',actual),('quality',qa),('pdf-text',text)]:
        (directory/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    # 缺陷闭环图的前后页，用文字标题定位，不将旧页码当作新页码。
    if pid==11:
        location=next((i for i,t in enumerate(text) if '闭环逻辑说明' in t),99)
        document=pdf.PdfDocument(path)
        for i in range(max(0,location-1),min(len(document),location+2)):
            document[i].render(scale=1.3).to_pil().save(directory/f'page-{i+1}.png')
    summary={'project':pid,'body_pages':actual['body_pages'],'total_pages':actual['total_pages'],
             'within_tolerance':actual['within_tolerance'],'pdf_url':actual['pdf_url'],
             'originals_unchanged':{p:fingerprint(p)==sig for p,sig in before.items()},
             'mermaid_failure_pages':sum('待补图：Mermaid' in t for t in text),
             'issues':dict(Counter(i['kind'] for i in qa['issues']))}
    (directory/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True),flush=True)

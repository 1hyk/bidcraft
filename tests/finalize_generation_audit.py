"""导出测试副本、核对素材实际嵌入、分页和原项目保护，保存可复查证据。"""
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import httpx
from PIL import Image,ImageOps
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.core.database import query
from app.core.page_plan import count_words

if __name__=='__main__':
    pid=int(sys.argv[1]);directory=Path(sys.argv[2]);source=int(sys.argv[3])
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=1800,trust_env=False) as c:
        response=c.post(f'/api/export/{pid}/docx');response.raise_for_status()
        (directory/'proposal-final.docx').write_bytes(response.content)
        plan=c.get(f'/api/projects/{pid}/page-plan').json()
        qa=c.get(f'/api/export/{pid}/check').json()
    for name,value in [('final_page_plan.json',plan),('final_quality_after_rerun.json',qa)]:
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    rows=query('SELECT n.id,n.parent_id,n.title,c.content FROM chapter_node n LEFT JOIN section_content c ON c.chapter_node_id=n.id WHERE n.project_id=%s ORDER BY n.id',(pid,))
    for n in rows:
        d=json.loads(n['content']) if isinstance(n['content'],str) else n.get('content') or {}
        n['body']=d.get('body') or '';n['payload']=d
    (directory/'final_bodies.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    original=query('SELECT n.*,c.content FROM chapter_node n LEFT JOIN section_content c ON c.chapter_node_id=n.id WHERE n.project_id=%s ORDER BY n.id',(source,))
    sig=hashlib.sha256(json.dumps(original,sort_keys=True,default=str).encode()).hexdigest()
    pdf=ROOT/plan['measurement']['pdf_url'].removeprefix('/');texts=[p.extract_text() or '' for p in PdfReader(pdf).pages]
    (directory/'final_pdf_text.json').write_text(json.dumps(texts,ensure_ascii=False,indent=2),encoding='utf-8')
    pixels=[]
    with ZipFile(directory/'proposal-final.docx') as z:
        for name in z.namelist():
            if name.startswith('word/media/'):
                with Image.open(io.BytesIO(z.read(name))) as im:
                    rgb=ImageOps.exif_transpose(im).convert('RGBA')
                    pixels.append(hashlib.sha256(str(rgb.size).encode()+rgb.tobytes()).hexdigest())
    summary={'body_pages':plan['measurement']['body_pages'],'within_tolerance':plan['measurement']['within_tolerance'],
             'total_pages':len(texts),'issues':dict(Counter(i['kind'] for i in qa['issues'])),
             'original_unchanged':sig==json.loads((directory/'report.json').read_text(encoding='utf-8'))['original_fingerprint'],
             'intro_words':{n['title']:count_words(n['body']) for n in rows if any(c['parent_id']==n['id'] for c in rows)},
             'raw_asset_id_pages':sum('素材ID' in t for t in texts),'initial_acceptance_pages':sum('初验' in t for t in texts),
             'mermaid_failure_pages':sum('待补图：Mermaid' in t for t in texts),
             'images':len(pixels),'unique_images':len(set(pixels))}
    (directory/'final_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))

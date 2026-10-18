"""全稿结构、图源、篇幅与招标条款的可追溯检查；不改正文。"""
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.core.config import settings
from app.core.database import query
from app.core.page_plan import count_words
from app.services.page_plan_service import build_page_plan_view
from app.services.image_ownership import project_image_index_flat
from app.services.llm import llm

OUT=settings.uploads_dir/'_verification'/'full_proposal'


def main():
    state=json.loads((OUT/'state.json').read_text(encoding='utf-8'))
    pid=state['project_id']
    rows=query('SELECT cn.id,cn.title,cn.parent_id,sc.content FROM chapter_node cn JOIN section_content sc ON sc.chapter_node_id=cn.id WHERE cn.project_id=%s ORDER BY cn.id',(pid,))
    images=project_image_index_flat(pid)
    refs=defaultdict(list); mermaids=defaultdict(list); paragraphs=defaultdict(list)
    pending=[];errors=[];chapters=[];full=[]
    for row in rows:
        obj=json.loads(row['content']) if isinstance(row['content'],str) else row['content']
        body=obj.get('body','')
        full.append(f"\n【章节{row['id']}：{row['title']}】\n{body}")
        code=re.findall(r'```mermaid\s*\n(.*?)```',body,re.S)
        for source in code: mermaids[hashlib.sha256(source.strip().encode()).hexdigest()].append(row['id'])
        plain=re.sub(r'```[^\n]*\n.*?```','',body,flags=re.S)
        for para in plain.split('\n\n'):
            text=para.strip()
            if len(text)>120: paragraphs[text].append(row['id'])
        for m in re.finditer(r'\[图片占位[：:]\s*([^\]]+)\]',plain):
            name=m.group(1)
            canonical,path=images._match(name)
            if path:
                refs[str(path.resolve())].append({'chapter_id':row['id'],'name':canonical})
            else: pending.append({'chapter_id':row['id'],'title':row['title'],'name':name})
        used=obj.get('used') or {}
        for error in used.get('image_generation_errors') or []: errors.append({'chapter_id':row['id'],'error':error})
        selected=[x for x in used.get('images',[]) if x.get('selected')]
        chapters.append({'id':row['id'],'title':row['title'],'words':count_words(body),'mermaid':len(code),'selected_images':[{'caption':x['caption'],'generated':x.get('generated',False),'asset_id':x['asset_id']} for x in selected]})
    text='\n'.join(full)
    (OUT/'full_body.md').write_text(text,encoding='utf-8')
    plan=build_page_plan_view(pid)
    report={'project_id':pid,'chapters':chapters,'page_plan':plan,'pending_images':pending,'image_errors':errors,
            'duplicate_image_references':{k:v for k,v in refs.items() if len(v)>1},
            'duplicate_mermaid_sources':{k:v for k,v in mermaids.items() if len(v)>1},
            'duplicate_long_paragraphs':[{'text':k,'chapters':v} for k,v in paragraphs.items() if len(v)>1],
            'original_unchanged':state.get('original_unchanged'),'actual_pages':None}
    if (OUT/'audit.json').exists():
        previous=json.loads((OUT/'audit.json').read_text(encoding='utf-8'))
        for key in ('actual_pages','historical_candidate_references','historical_selected','generated_selected','pending_fact_markers','mermaid_after_reserved_id_fix'):
            if key in previous: report[key]=previous[key]
    (OUT/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'chapters':len(rows),'words':sum(x['words'] for x in chapters),'pending':len(pending),'image_errors':len(errors),'duplicate_image_groups':len(report['duplicate_image_references']),'mermaid':sum(x['mermaid'] for x in chapters)},ensure_ascii=True),flush=True)
    reqs=query("SELECT id,category,title,content,weight FROM tender_requirement WHERE project_id=%s AND status!='ignored' AND category IN ('评分办法','技术要求','编制要求','废标风险') ORDER BY id",(pid,))
    prompt='核验以下技术方案正文是否覆盖每条招标要求。仅按提供正文判定，不能用常识补齐。区分covered/partial/missing/not_applicable（整份响应文件的资格商务要求在仅编第四章技术方案时不适用）。每条输出 requirement_id,status,chapter_ids,evidence,gap。evidence必须复制正文中连续的一小段原文，20到60字；严格禁止添加省略号、拼接多段、改写或总结。程序将执行 evidence in 正文 的逐字比对，拼接引用会导致核验不通过。证据不足写partial或missing，不能因提到关键词就算覆盖。输出JSON对象 {"items": [...],"conflicts": [{"chapter_ids":[],"evidence":[],"problem":""}]}。跨章时限、指标和承诺冲突也请核对。\n招标要求：'+json.dumps(reqs,ensure_ascii=False,default=str)+'\n完整正文：'+text
    response=llm.chat([{'role':'user','content':prompt}],max_tokens=9000,temperature=0,timeout=300)
    (OUT/'coverage_raw.txt').write_text(response,encoding='utf-8')
    raw=re.sub(r'^```(?:json)?\s*|\s*```$','',response.strip())
    coverage=json.loads(raw)
    for item in coverage.get('items',[]):
        evidence=item.get('evidence') or ''
        item['evidence_verified']=bool(evidence and evidence in text)
    (OUT/'coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2),encoding='utf-8')
    print('coverage',dict(Counter(x['status'] for x in coverage['items'])),flush=True)


if __name__=='__main__': main()

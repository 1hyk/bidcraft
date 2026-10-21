"""要求义务与证据逐项核对；正文证据不能代替导出、提交或真实证明检查。"""
import re

STAGES={'body','export','submission','external_evidence'}


def _compact(text):
    return re.sub(r'[\W_]+','',text,flags=re.UNICODE)


def parse_obligations(rows, requirements):
    """义务必须逐字来自本条，且合起来覆盖全部原文；不能漏拆后宣布全部通过。"""
    result={}
    for req in requirements:
        matches=[r for r in rows if isinstance(r,dict) and r.get('requirement_id')==req['id']]
        if len(matches)!=1:raise ValueError('要求义务拆分不完整，保留旧报告，请重试或人工核对')
        parts=matches[0].get('obligations')
        if not isinstance(parts,list) or not parts:raise ValueError('要求义务为空，未提交通过结论')
        text=req['content'];covered=[False]*len(text);items=[]
        for n,part in enumerate(parts):
            if not isinstance(part,dict):raise ValueError('义务结构无效')
            quote=part.get('quote');stage=part.get('stage')
            if not isinstance(quote,str) or not _compact(quote) or quote not in text or stage not in STAGES:
                raise ValueError('义务原文或检查阶段无效，未提交通过结论')
            for match in re.finditer(re.escape(quote),text):
                covered[match.start():match.end()]=[True]*len(quote)
            items.append({'obligation_id':f"R{req['id']}O{n+1}",'quote':quote,'stage':stage,**({'source_id':part['source_id']} if part.get('source_id') else {})})
        if _compact(''.join(c for c,yes in zip(text,covered) if not yes)):
            raise ValueError('义务拆分遗漏原文，未提交通过结论')
        result[req['id']]=items
    return result


def source_units(text):
    """原文由服务端切片并保留全部字符，模型只引用编号，不重抄正文。"""
    parts=re.findall(r'[^。；;\n（）()]+[。；;\n（）()]*|[。；;\n（）()]+',text)
    return {f'S{n+1}':part for n,part in enumerate(parts) if _compact(part)}


def parse_source_obligations(rows, req):
    units=source_units(req['content'])
    matches=[r for r in rows if isinstance(r,dict) and type(r.get('requirement_id')) is int and r['requirement_id']==req['id']]
    if len(matches)!=1:raise ValueError('未返回本条义务列表')
    parts=matches[0].get('obligations')
    if not isinstance(parts,list) or not parts:raise ValueError('本条义务列表为空')
    used=set();items=[]
    for part in parts:
        if not isinstance(part,dict):raise ValueError('义务结构无效')
        ids=part.get('source_ids');stage=part.get('stage')
        if not isinstance(ids,list) or not ids or stage not in STAGES or any(not isinstance(i,str) or i not in units for i in ids):
            raise ValueError('原文片段编号或阶段无效')
        for identity in dict.fromkeys(ids):
            items.append({'obligation_id':f"R{req['id']}O{len(items)+1}",'quote':units[identity],'stage':stage,'source_id':identity})
            used.add(identity)
    missing=set(units)-used
    if missing:raise ValueError('遗漏原文片段：'+','.join(sorted(missing)))
    return items


def decompose(requirements, project_id=None):
    import json
    import hashlib
    import os
    import uuid
    from app.core.config import settings
    from app.services.llm import llm
    from app.agents.json_util import extract_json
    if not requirements:return {}
    source=[{'requirement_id':r['id'],'content':r['content']} for r in requirements]
    cache=None
    if project_id is not None:
        key=hashlib.sha256(json.dumps({'version':2,'requirements':source},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        cache=settings.uploads_dir/'review_obligations'/f'project_{int(project_id)}'/(key+'.json')
        try:return parse_obligations(json.loads(cache.read_text(encoding='utf-8')),requirements)
        except (OSError,ValueError,TypeError):pass
    result={}
    for start in range(0,len(requirements),5):
        batch=requirements[start:start+5]
        try:
            raw=llm.chat([
                {'role':'system','content':'仅划分给定招标原文片段的核查义务，不核对正文、不新增要求、不重抄原文。输出JSON {"items":[{"requirement_id":1,"obligations":[{"source_ids":["S1"],"stage":"body|export|submission|external_evidence"}]}]}。本条所有片段编号必须使用，不漏编号、标题或引导语；同一片段含不同阶段义务可以分别引用。独立义务分开列。body指正文可核对的措施和内容；export指当前投标稿的最终页码和实际排版；submission指递交、签章、装订；external_evidence指必须核对真实原件或实测事实。性能数值与拟议方法仍属于body，不能因含数字或“页码”就归export。保持原文语境，不推导未要求的索引、字体或承诺。'},
                {'role':'user','content':json.dumps([{'requirement_id':r['id'],'content':r['content'],'source_units':source_units(r['content'])} for r in batch],ensure_ascii=False)}
            ],temperature=0,max_tokens=6000,timeout=120)
            returned=extract_json(raw).get('items') or []
        except Exception:
            returned=[]
        for req in batch:
            try:result[req['id']]=parse_source_obligations(returned,req)
            except (ValueError,TypeError) as exc:
                result[req['id']]=[{'obligation_id':f"R{req['id']}O1",'quote':req['content'],'stage':'body',
                                   'decomposition_error':f"要求#{req['id']} {req.get('title') or ''}：{exc}"}]
    if cache is not None and not any(p.get('decomposition_error') for parts in result.values() for p in parts):
        cache.parent.mkdir(parents=True,exist_ok=True)
        temporary=cache.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps([{'requirement_id':r['id'],'obligations':result[r['id']]} for r in requirements],ensure_ascii=False),encoding='utf-8')
            os.replace(temporary,cache)
        finally:temporary.unlink(missing_ok=True)
    return result


def validate(items, requirements, fragments, allowed, obligations):
    result=[]
    for req in requirements:
        failures=[p['decomposition_error'] for p in obligations[req['id']] if p.get('decomposition_error')]
        if failures:
            result.append({'requirement_id':req['id'],'title':req['title'],'status':'unverified','review_state':'error',
                           'obligations':obligations[req['id']],'body_status':'unverified','pending_stages':[],
                           'missing_points':[],'evidence':[],'evidence_verified':False,'gap':'义务拆分未完成，仅本条待重试或人工核对。'+'；'.join(failures),'human_review_required':True})
            continue
        item=next((r for r in items if isinstance(r,dict) and r.get('requirement_id')==req['id']),{})
        returned=item.get('obligations') or []
        if not isinstance(returned,list):returned=[]
        checked=[];evidence={};error=False
        for part in obligations[req['id']]:
            out=dict(part)
            if part['stage']!='body':
                out.update(status='pending_stage',gap='须在对应最终环节核对；正文声明不等于实际完成')
            else:
                hits=[r for r in returned if isinstance(r,dict) and r.get('obligation_id')==part['obligation_id']]
                row=hits[0] if len(hits)==1 else {}
                ids=row.get('evidence_ids') or []
                status=row.get('status')
                valid=isinstance(ids,list) and 0<len(ids)<=6 and all(isinstance(i,str) and i in fragments and i in allowed.get(req['id'],[]) for i in ids)
                if status not in {'covered','partial','missing'} or (status in {'covered','partial'} and not valid):
                    status='unverified';error=True
                out.update(status=status,gap=str(row.get('gap') or ''),evidence_ids=list(dict.fromkeys(ids)) if valid else [])
                if valid:evidence.update({i:fragments[i] for i in ids})
            checked.append(out)
        body=[p for p in checked if p['stage']=='body']
        pending=[p for p in checked if p['stage']!='body']
        gaps=[p for p in body if p['status'] in {'partial','missing'}]
        if error:status='unverified';gap='部分义务没有取得有效证据结论，请重试或人工对照，不据此重复补写正文。'
        elif gaps:
            status='partial' if evidence else 'missing'
            gap='；'.join(p['gap'] or ('尚未完整响应：'+p['quote']) for p in gaps)
        elif pending:status='pending_stage';gap='正文范围已逐项核对；仍有最终导出、递交或真实证明义务待核对，尚不能判为整项完全满足。'
        else:status='covered';gap='正文义务已逐项找到响应证据，仍需人工复核承诺与充分性。'
        result.append({'requirement_id':req['id'],'title':req['title'],'status':status,'review_state':'error' if error else 'completed',
                       'obligations':checked,'body_status':'unverified' if error else 'partial' if gaps else 'covered',
                       'pending_stages':sorted({p['stage'] for p in pending}),'missing_points':[p['quote'] for p in gaps],
                       'evidence':list(evidence.values()),'evidence_verified':bool(evidence) and not error,'gap':gap,'human_review_required':True})
    return result

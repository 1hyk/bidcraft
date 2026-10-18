"""公司资料提炼、候选审核及原件回查。仅用于本机单公司工作台。"""
import hashlib
import json
import uuid
from datetime import date
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.core.database import execute,query,query_one,get_conn
from app.services import company_materials as service
from app.services.task_runner import task_runner

router=APIRouter(prefix='/api/company',tags=['company'])


@router.get('/documents')
def documents():
    return query('SELECT id,name,status,error,task_id,created_at FROM company_document ORDER BY id DESC')


def start_extract(doc_id):
    with get_conn().cursor() as cur:
        cur.execute("UPDATE company_document SET status='extracting',error=NULL WHERE id=%s AND status IN ('uploaded','failed')",(doc_id,))
        if not cur.rowcount:return
    try:
        task=task_runner.submit('company_extract',doc_id,service.extract_task,doc_id)
        execute('UPDATE company_document SET task_id=%s WHERE id=%s',(task,doc_id))
    except Exception:
        execute("UPDATE company_document SET status='failed',error='启动提取失败，请重试' WHERE id=%s",(doc_id,));raise


@router.post('/documents')
async def upload(file:UploadFile=File(...)):
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in {'.docx','.pdf','.png','.jpg','.jpeg','.xlsx'}:raise HTTPException(400,'支持docx、文字型PDF、图片及xlsx')
    data=await file.read(25*1024*1024+1)
    if not data or len(data)>25*1024*1024:raise HTTPException(400,'文件不能为空且不得超过25MB')
    digest=hashlib.sha256(data).hexdigest()
    old=query_one('SELECT id,name,status FROM company_document WHERE sha256=%s',(digest,))
    if old:
        return {**old,'duplicate':True,'url':f"/api/company/documents/{old['id']}/file"}
    path=service.storage()/(uuid.uuid4().hex+suffix)
    path.write_bytes(data)
    try:
        doc_id=execute('INSERT INTO company_document (name,sha256,path) VALUES (%s,%s,%s)',(Path(file.filename or '资料').name[:255],digest,str(path)))
    except Exception:
        path.unlink(missing_ok=True)
        old=query_one('SELECT id,name,status FROM company_document WHERE sha256=%s',(digest,))
        if old:return {**old,'duplicate':True,'url':f"/api/company/documents/{old['id']}/file"}
        raise
    start_extract(doc_id)
    return {'id':doc_id,'name':file.filename,'status':'extracting','url':f'/api/company/documents/{doc_id}/file'}


@router.post('/documents/{doc_id}/extract')
def retry(doc_id:int):
    row=query_one('SELECT status FROM company_document WHERE id=%s',(doc_id,))
    if not row:raise HTTPException(404,'资料不存在')
    if row['status']=='extracted':raise HTTPException(409,'已完成提取；审核结果不会被重提取覆盖')
    start_extract(doc_id)
    return {'ok':True}


@router.get('/documents/{doc_id}/file')
def original(doc_id:int):
    row=query_one('SELECT name,path FROM company_document WHERE id=%s',(doc_id,))
    if not row or not Path(row['path']).is_file():raise HTTPException(404,'原件不存在')
    path=Path(row['path']).resolve()
    if not path.is_relative_to(service.storage().resolve()):raise HTTPException(404,'原件路径无效')
    return FileResponse(path,filename=row['name'])


@router.get('/facts')
def facts():return service.facts()


class Fact(BaseModel):
    category:str=Field(default='其他',max_length=80)
    subject:str=Field(default='',max_length=200)
    field_name:str=Field(default='',max_length=200)
    value:str=Field(min_length=1,max_length=4000)
    valid_until:date|None=None
    applicability:str=Field(default='',max_length=2000)
    status:str='pending'
    revision:int=1


def validate(body):
    if body.status not in {'pending','confirmed','rejected','disabled'}:raise HTTPException(400,'无效审核状态')
    if body.category not in service.CATEGORIES:raise HTTPException(400,'无效资料类别')
    if not body.value.strip():raise HTTPException(400,'事实值不能为空')


@router.post('/facts')
def create(body:Fact):
    validate(body)
    fid=execute('INSERT INTO company_fact (category,subject,field_name,value,evidence,location,valid_until,applicability,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (body.category,body.subject,body.field_name,body.value,body.value,'用户手工录入',body.valid_until,body.applicability,body.status))
    return {'id':fid}


@router.patch('/facts/{fact_id}')
def revise(fact_id:int,body:Fact):
    validate(body)
    with get_conn().cursor() as cur:
        cur.execute('UPDATE company_fact SET category=%s,subject=%s,field_name=%s,value=%s,valid_until=%s,applicability=%s,status=%s,revision=revision+1 WHERE id=%s AND revision=%s',
                    (body.category,body.subject,body.field_name,body.value,body.valid_until,body.applicability,body.status,fact_id,body.revision))
        if not cur.rowcount:raise HTTPException(409,'资料已变化或不存在，请刷新后核对')
    return {'ok':True}


class Basic(BaseModel):
    name:str=Field(default='',max_length=200)
    positioning:str=Field(default='',max_length=4000)


@router.put('/profile')
def basic(body:Basic):
    execute('INSERT INTO company_profile (id,content) VALUES (1,%s) ON DUPLICATE KEY UPDATE content=VALUES(content)',(body.model_dump_json(),))
    return {'ok':True}


@router.get('/projects/{project_id}/chapters')
def chapters(project_id:int):
    from app.services.section_service import generation_node_ids
    allowed=generation_node_ids(project_id)
    return [r for r in query('SELECT n.id,n.title FROM chapter_node n JOIN section_content c ON c.chapter_node_id=n.id WHERE n.project_id=%s ORDER BY n.sort_order,n.id',(project_id,)) if r['id'] in allowed]


@router.post('/projects/{project_id}/chapters/{node_id}/suggest')
def suggestions(project_id:int,node_id:int):
    from app.services.company_fill import suggest
    try:return suggest(project_id,node_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


class Apply(BaseModel):
    tokens:list[str]=Field(min_length=1,max_length=80)


@router.post('/projects/{project_id}/chapters/{node_id}/apply')
def apply(project_id:int,node_id:int,body:Apply):
    from app.services.company_fill import apply as fill
    try:return fill(project_id,node_id,body.tokens)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/projects/{project_id}/revisions')
def revisions(project_id:int):
    from app.services.section_service import generation_node_ids
    allowed=generation_node_ids(project_id)
    rows=query('SELECT r.id,r.node_id,r.status,r.facts,r.created_at,n.title FROM company_fill_revision r JOIN chapter_node n ON n.id=r.node_id WHERE r.project_id=%s ORDER BY r.id DESC LIMIT 200',(project_id,))
    for row in rows:row['facts']=service.decode(row['facts'],[])
    return [r for r in rows if r['node_id'] in allowed]


@router.post('/projects/{project_id}/revisions/{revision_id}/undo')
def undo(project_id:int,revision_id:int):
    from app.services.company_fill import undo as revert
    try:return revert(project_id,revision_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc

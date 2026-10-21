"""本机公司资料与当前标段项目理解。"""
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from app.services import project_understanding as service

router = APIRouter(prefix='/api', tags=['understanding'])


class Entry(BaseModel):
    category: str = Field(max_length=80)
    content: str = Field(max_length=4000)
    source: str = Field(default='', max_length=500)
    confirmed: bool = False


class Profile(BaseModel):
    name: str = Field(default='', max_length=200)
    positioning: str = Field(default='', max_length=4000)
    entries: list[Entry] = Field(default_factory=list, max_length=150)


@router.get('/company-profile')
def company_profile():
    return service.company()


@router.put('/company-profile')
def save_company(body: Profile):
    # 旧版整份列表写回会丢来源和审核版本，拒绝静默覆盖。
    raise HTTPException(409,'公司资料已升级，请刷新页面，使用资料条目审核保存')


@router.post('/company-profile/files')
async def upload_company(file: UploadFile = File(...)):
    from app.api.company import upload
    return await upload(file)


@router.get('/projects/{project_id}/understanding')
def understanding(project_id: int):
    try:
        return service.snapshot(project_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class Confirmation(BaseModel):
    fingerprint: str
    notes: str = Field(default='', max_length=10000)


class Arrangement(BaseModel):
    node_ids: list[int] = Field(default_factory=list, max_length=100)


@router.put('/projects/{project_id}/requirements/{req_id}/arrangement')
def arrange(project_id: int, req_id: int, body: Arrangement):
    try:
        service.save_arrangement(project_id, req_id, body.node_ids)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'ok':True}


@router.put('/projects/{project_id}/understanding')
def confirm(project_id: int, body: Confirmation):
    try:
        service.confirm(project_id, body.fingerprint, body.notes)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'ok':True}

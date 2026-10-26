"""编辑器素材预览和单图候选生成；不直接修改正文，用户采用后由编辑器保存。"""
import io
import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, UploadFile, Form
from pydantic import BaseModel, Field
from PIL import Image, ImageOps
from app.core.config import settings
from app.core.database import query_one
from app.services import generated_images
from app.services.image_prompt import style_from_feedback
from app.services.image_retrieval import catalog, image_identity

router = APIRouter(prefix='/api/projects/{project_id}/image-assets', tags=['image-assets'])


def public(asset):
    result = {k:asset.get(k) for k in ('asset_id','caption','url','content','generated','review_pending','review_reason','source_title')}
    from app.services.image_caption import display_caption
    result['caption']=display_caption(result.get('caption'))
    try:
        from app.services.image_vision import local_path_of
        path = asset.get('path') or local_path_of(asset['url'])
        with Image.open(path) as im: result.update(width=im.width,height=im.height)
    except Exception: pass
    return result


def check_project(project_id):
    if not query_one('SELECT id FROM project WHERE id=%s',(project_id,)):
        raise HTTPException(404,'项目不存在')


def _outline_need(project_id:int,node_id:int,name:str):
    """按图名取当前章节思路里的配图定义，用于兼容没有语义分类的旧素材。"""
    row=query_one('SELECT outline FROM chapter_node WHERE id=%s AND project_id=%s',(node_id,project_id)) or {}
    outline=row.get('outline') or {}
    if isinstance(outline,str):
        try: outline=json.loads(outline)
        except ValueError: return None
    matches=[n for n in outline.get('image_needs') or []
             if name.startswith(str(n.get('name') or '\0'))]
    return matches[0] if len(matches)==1 else None


@router.get('')
def list_assets(project_id:int):
    check_project(project_id)
    return {'items':[public(a) for a in catalog(project_id)]}


class Regenerate(BaseModel):
    asset_id: str = Field(max_length=64)
    node_id: int
    feedback: str = Field(min_length=1,max_length=1500)
    quality: str = 'high'
    request_id: str = Field(min_length=8,max_length=64)
    mode: Literal['edit','redesign'] = 'edit'


class MermaidRevision(BaseModel):
    node_id: int
    code: str = Field(min_length=1,max_length=24000)
    feedback: str = Field(min_length=1,max_length=2000)


@router.post('/mermaid/revise')
def revise_mermaid(project_id:int, body:MermaidRevision):
    check_project(project_id)
    from app.services.section_service import generation_node_ids
    if body.node_id not in generation_node_ids(project_id):
        raise HTTPException(404,'章节不属于当前生成范围')
    from app.services.diagram_edit import revise
    row=query_one('SELECT c.title,c.outline,s.content FROM chapter_node c LEFT JOIN section_content s ON s.chapter_node_id=c.id WHERE c.id=%s AND c.project_id=%s',(body.node_id,project_id)) or {}
    try:
        return {'code':revise(body.code,body.feedback,json.dumps(row,ensure_ascii=False,default=str))}
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None
    except Exception:
        raise HTTPException(502,'单图修改暂未完成，请稍后重试；原图未修改') from None


@router.post('/regenerate')
def regenerate(project_id:int, body:Regenerate):
    check_project(project_id)
    from app.services.section_service import generation_node_ids
    if body.node_id not in generation_node_ids(project_id):
        raise HTTPException(404,'章节不属于当前生成范围')
    asset = next((a for a in catalog(project_id) if a['asset_id']==body.asset_id),None)
    if not asset: raise HTTPException(404,'素材当前不可用')
    if body.quality not in {'standard','high'}: raise HTTPException(400,'清晰度选项无效')
    if body.quality=='high' and not settings.image_generation_model.startswith('qwen-image-2.0'):
        raise HTTPException(400,'当前模型尚未验证高清尺寸，请选择标准清晰度')
    name = str(asset.get('caption') or '').split('（AI生成')[0]
    from app.services.image_planning import TABLE, DATA_CHART, RELATION, regeneration_style
    if TABLE.search(name) or DATA_CHART.search(name) or RELATION.search(name):
        raise HTTPException(400,'这张内容应使用可编辑表格或Mermaid，不能靠位图重生成修正文字和数据；请替换为对应表格或图表。')
    original=asset.get('generation_need') or {}
    outline_need=_outline_need(project_id,body.node_id,name)
    if not original:
        original=outline_need or {}
    elif outline_need and original.get('data_structure') not in {'physical_scene','spatial_layout'}:
        # 旧素材没有新的语义分类时，用当前章节思路里的分类纠正，不按图名猜。
        original={**original,'data_structure':outline_need.get('data_structure') or original.get('data_structure')}
    need={'name':name,'source':'illustration','purpose':'用于说明本项目拟议场景与空间安排',
          'prompt':f"原始用途：{str(original.get('purpose') or asset.get('content') or '')[:400]}。原始绘图要求：{str(original.get('prompt') or '')[:600]}。清晰边缘、简洁构图，避免密集细节。没有明确地图需求时，不要以宏观地图替代具体场景。",
          'data_structure':original.get('data_structure') or '',
          'content_origin':original.get('content_origin') or '',
          '_feedback':body.feedback,
          '_request_id':body.request_id}
    from app.services.image_vision import local_path_of
    original_path = local_path_of(asset.get('url') or '')
    if original_path is None:
        raise HTTPException(400,'当前原图文件不可用，请刷新素材或重新上传；未执行文字重画')
    previous_style=original.get('visual_style') or asset.get('visual_style') or ''
    feedback_style=style_from_feedback(body.feedback)
    try:
        resolved_style=regeneration_style(original,feedback_style,previous_style)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None
    need.update(_reference_image_path=str(original_path),_edit_source_asset_id=asset['asset_id'],
                visual_style=resolved_style)
    mode=body.mode
    if previous_style and resolved_style!=previous_style:
        # 换画风（如实物图→示意图）必须重画，拿旧图编辑改不过来。
        mode='redesign'
    if mode=='redesign':
        # 先看当前图并结合反馈整理场景描述，用户无需自己写完整生图提示。
        from app.services.generated_images import describe_redesign
        try:
            need['prompt']=describe_redesign(original_path,body.feedback,name,
                                             original.get('purpose') or '',resolved_style)
        except Exception:
            raise HTTPException(502,'当前图片分析失败，未提交付费生图，请稍后重试') from None
        need.pop('_reference_image_path',None)
        need.pop('_edit_source_asset_id',None)
    try:
        with Image.open(original_path) as im:
            width,height=im.size
        scale=(2048 if body.quality=='high' else 1536)/max(width,height)
        out_w=max(16,round(width*scale/16)*16)
        out_h=max(16,round(height*scale/16)*16)
        if out_w*out_h < 512*512:
            raise ValueError('原图长宽比过大，请先裁剪后上传')
        need['_generation_size']=f'{out_w}*{out_h}'
    except (OSError,ValueError) as exc:
        raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else '无法读取原图尺寸') from None
    try: result=generated_images.generate(project_id,body.node_id,need,'')
    except generated_images.ImageGenerationError as exc: raise HTTPException(400,str(exc)) from None
    return public(result)


@router.post('/upload')
async def upload_asset(project_id:int, file:UploadFile, caption:str=Form('用户上传图片')):
    check_project(project_id)
    data=await file.read(20*1024*1024+1)
    if len(data)>20*1024*1024: raise HTTPException(400,'图片不能超过20MB')
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width*im.height>16_000_000: raise ValueError('尺寸过大')
            image=ImageOps.exif_transpose(im).convert('RGB')
            root=generated_images._directory(project_id);root.mkdir(parents=True,exist_ok=True)
            stem='upload_'+uuid.uuid4().hex
            path=root/(stem+'.png');image.save(path,format='PNG')
    except Exception: raise HTTPException(400,'请上传有效的PNG、JPEG或WebP图片，最大1600万像素') from None
    record={'project_id':project_id,'asset_id':image_identity(path),'caption':caption[:150],
            'url':'/uploads/'+path.relative_to(settings.uploads_dir).as_posix(),
            'generated':False,'validated':True,'source_title':'用户上传','content':caption[:150]}
    (root/(stem+'.json')).write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')
    return public(record)

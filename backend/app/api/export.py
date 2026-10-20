"""导出 API：把项目编制成果导出成可投递的 Word 交付件。

排版与转换全在 app/services/docx_export.py，本模块只管 HTTP 这一层：
参数校验、错误映射、中文文件名的 RFC 5987 编码。

文件名走 filename* (RFC 5987)：HTTP 头是 latin-1，中文项目名直接写进
Content-Disposition 会乱码，必须 percent-encode；同时保留一个 ASCII 的
filename= 作为老客户端的兜底。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.docx_export import export_project_docx

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get('/{project_id}/check')
def check_proposal(project_id: int):
    from app.core.database import query_one
    if not query_one('SELECT id FROM project WHERE id=%s',(project_id,)):
        raise HTTPException(404,'项目不存在')
    from app.services.proposal_quality import check
    return check(project_id)


@router.post('/{project_id}/review')
def review_proposal(project_id: int):
    from app.core.database import query_one
    if not query_one('SELECT id FROM project WHERE id=%s',(project_id,)):
        raise HTTPException(404,'项目不存在')
    from app.services.requirement_matrix import run_review
    try:return run_review(project_id)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc


@router.get('/{project_id}/requirements')
def requirement_matrix(project_id: int):
    from app.core.database import query_one
    if not query_one('SELECT id FROM project WHERE id=%s',(project_id,)):
        raise HTTPException(404,'项目不存在')
    from app.services.requirement_matrix import matrix
    return matrix(project_id)


@router.post("/{project_id}/docx")
def export_docx(project_id: int):
    """导出 .docx。

    排版与封面文档名全部取自项目的排版设置（GET/PUT /api/projects/{id}/docx-style，
    见 app/core/docx_style.py），本接口不收参数——同一个文档名只有一处能改，
    避免"面板里刚改的值被一个看不见的调用方静默覆盖"。
    """
    try:
        path, filename = export_project_docx(project_id)
        from app.services.document_layout import available
        from app.services.pagination_service import measure
        from app.core.config import settings
        if available():
            # 真实排版完成后交付含目录页码的 Word；部分章节未写时仍允许导出检查稿。
            from app.services.pagination_service import snapshot
            if snapshot(project_id)['complete']:
                result=measure(project_id,input_path=path)
                path=settings.uploads_dir/'layout'/f'project_{project_id}'/result['signature']/'document.docx'
    except ValueError as e:
        # 项目不存在 / 没有目录树 —— 都是用户能自己解决的前置条件，回 400 带原文
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"导出失败：{e}") from e

    return FileResponse(
        path=str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
        headers={
            "Content-Disposition": (
                f'attachment; filename="bidcraft.docx"; '
                f"filename*=UTF-8''{quote(filename)}"
            ),
            # 前端要读文件名时用；同时避免代理缓存住这份一次性文件
            "Access-Control-Expose-Headers": "Content-Disposition",
            "Cache-Control": "no-store",
        },
    )

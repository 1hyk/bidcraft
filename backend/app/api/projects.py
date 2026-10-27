"""项目管理 API。"""
from datetime import datetime
import json

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.core.database import execute, query, query_one

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    project_type: str = ""  # 工程类型；候选选项按领域取（Domain.project_types，见 /api/domains）
    domain: str = ""        # 领域（app/core/domains.py key）；空=系统默认领域
    project_types: list[str] | None = None
    technical_objects: list[str] | None = None
    service_content: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    domain: str | None = None
    project_type: str | None = None
    project_types: list[str] | None = None
    technical_objects: list[str] | None = None
    service_content: str | None = None


@router.get("")
def list_projects(keyword: str = ""):
    sql = "SELECT * FROM project"
    params: tuple = ()
    if keyword:
        sql += " WHERE name LIKE %s"
        params = (f"%{keyword}%",)
    sql += " ORDER BY updated_at DESC"
    projects = query(sql, params)
    # 附带标段摘要（卡片展示：标段代码列表 + 当前选定）
    if projects:
        ids = [p["id"] for p in projects]
        marks = ",".join(str(int(i)) for i in ids)
        lot_rows = query(
            f"SELECT project_id, lot_code, lot_name, selected FROM lot WHERE project_id IN ({marks}) ORDER BY id"
        )
        by_proj: dict = {}
        for l in lot_rows:
            by_proj.setdefault(l["project_id"], []).append(l)
        file_rows = query(
            f"SELECT project_id, COUNT(*) AS total, SUM(status='parsed') AS parsed "
            f"FROM project_file WHERE category='tender' AND project_id IN ({marks}) GROUP BY project_id"
        )
        files_by_project = {row['project_id']: row for row in file_rows}
        for p in projects:
            files = files_by_project.get(p['id'], {})
            p['has_tender'] = bool(files.get('total'))
            p['has_parsed_tender'] = bool(files.get('parsed'))
            lots = by_proj.get(p["id"]) or []
            p["lots"] = [
                {"lot_code": l["lot_code"], "lot_name": l["lot_name"], "selected": bool(l["selected"])}
                for l in lots
            ]
    from app.services.project_classification import present
    return [present(p) for p in projects]


@router.post("")
def create_project(body: ProjectCreate):
    """建项目：domain 决定此后这个项目匹配哪个领域的知识库、用哪套专业标签词表。"""
    from app.core.domains import get_domain

    # 未知 key 回退默认领域（避免脏领域进库）**并打一条警告**——静默回退是 2026-10-15
    # 那个跨域 bug 的成因，见 domains._fallback_domain
    dom = get_domain(body.domain or "softtest")
    from app.services.project_classification import validate_types, validate_objects
    try:
        types=validate_types(body.project_types,dom.key) if body.project_types is not None else None
        objects=validate_objects(body.technical_objects) if body.technical_objects is not None else None
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    if len(body.service_content or '')>2000:raise HTTPException(400,'本次服务内容不能超过2000字')
    if not body.name.strip():raise HTTPException(400,'请填写项目名称')
    now = datetime.now()
    pid = execute(
        "INSERT INTO project (name, description, project_type, domain, project_types, technical_objects, service_content, status, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)",
        (body.name, body.description, body.project_type, dom.key,json.dumps(types,ensure_ascii=False) if types is not None else None,json.dumps(objects,ensure_ascii=False) if objects is not None else None, (body.service_content or '').strip(), now, now),
    )
    return {"id": pid, "domain": dom.key}


@router.get('/service-type-options')
def service_type_options():
    from app.services.project_classification import SERVICE_TYPES
    return {'items': list(SERVICE_TYPES)}


@router.get("/{project_id}")
def get_project(project_id: int):
    item = query_one("SELECT * FROM project WHERE id = %s", (project_id,))
    if not item:
        raise HTTPException(404, "项目不存在")
    from app.services.project_classification import present
    return present(item)


@router.get('/{project_id}/classification-suggestions')
def classification_suggestions(project_id: int):
    from app.services.project_classification import suggestions
    try:return suggestions(project_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    except Exception as exc:raise HTTPException(502,'业务提取失败，已保存结果保留，请稍后重试') from exc


@router.post('/{project_id}/classification-suggestions/refresh')
def refresh_classification_suggestions(project_id: int):
    from app.services.project_classification import suggestions
    try:return suggestions(project_id, refresh=True)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    except Exception as exc:raise HTTPException(502,'重新提取失败，已保存结果保留，请稍后重试') from exc


@router.patch("/{project_id}")
def update_project(project_id: int, body: ProjectUpdate):
    """编辑项目。domain 只在请求里显式带上时才改——否则编辑个名字就会把领域重置掉。"""
    current=query_one('SELECT * FROM project WHERE id=%s',(project_id,))
    if not current:raise HTTPException(404,'项目不存在')
    from app.core.domains import get_domain
    from app.services.project_classification import validate_types,validate_objects
    fields=body.model_fields_set
    domain=get_domain(body.domain).key if 'domain' in fields else current['domain']
    changes={k:getattr(body,k) for k in ('name','description','project_type','service_content') if k in fields}
    if 'service_content' in changes:
        changes['service_content']=(changes['service_content'] or '').strip()
        if len(changes['service_content'])>2000:raise HTTPException(400,'本次服务内容不能超过2000字')
    if 'name' in changes and not (changes['name'] or '').strip():raise HTTPException(400,'请填写项目名称')
    if 'domain' in fields:changes['domain']=domain
    try:
        if 'project_types' in fields:changes['project_types']=json.dumps(validate_types(body.project_types,domain),ensure_ascii=False)
        elif 'domain' in fields and domain!=current['domain']:
            from app.services.project_classification import project_types
            validate_types(project_types(current),domain)
        if 'technical_objects' in fields:changes['technical_objects']=json.dumps(validate_objects(body.technical_objects),ensure_ascii=False)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    changes['updated_at']=datetime.now()
    sets=[key+' = %s' for key in changes]
    params=list(changes.values())
    params.append(project_id)
    execute(f"UPDATE project SET {', '.join(sets)} WHERE id = %s", tuple(params))
    return {"ok": True}


@router.delete("/{project_id}")
def delete_project(project_id: int):
    execute("DELETE FROM project WHERE id = %s", (project_id,))
    return {"ok": True}


# ---------------------------------------------------------------- 标段（M1-2）

class LotSelect(BaseModel):
    lot_id: int | None = None  # None = 无标段划分，直接进入


@router.get("/{project_id}/lots")
def get_lots(project_id: int):
    """项目标段列表 + 当前选中。三种形态：多条→选一个 / 一条→确认 / 空→直接进入。"""
    from app.services.lot_service import list_lots

    return list_lots(project_id)


@router.post("/{project_id}/lots/select")
def select(project_id: int, body: LotSelect):
    """选定标段（多选一/单标段确认），lot_id 为空表示无标段直接进入。"""
    from app.services.lot_service import select_lot

    return select_lot(project_id, body.lot_id)


class LotExtractBody(BaseModel):
    overwrite: bool = False  # 已有标段时须置 true（前端二次确认后重传）


@router.post("/{project_id}/lots/extract")
def extract_lots(project_id: int, body: LotExtractBody | None = None):
    """从已解析的招标文件重新识别标段划分（后台任务，前端轮询 task 进度）。

    识别吃的是**已解析完成的招标文件原文**，所以未解析完直接拒绝——否则任务必然抛错，
    而 task.detail 里带的是整段 traceback，会原样冒到界面上（同 /lot-profile/rebuild 的教训）。

    已有标段时必须显式 overwrite：重建是**破坏性**的（replace_lots 清空 lot 表，而标段档案
    就存在 lot.profile 里 → 连带清掉，需重新抽取），故要求前端二次确认后重传。
    """
    from app.core.domains import domain_of_project, lot_fields_of
    from app.services.lot_service import has_parsed_tender, list_lots, start_extract_lots

    # 本域没有标段这一层次时直接说清楚（同 /lot-profile/rebuild）：否则任务会照提示词去
    # 原文里硬找"标段划分"，把"分包给某公司"之类当成标包划分抽出来落库。**在这一层挡掉**。
    if not lot_fields_of(domain_of_project(project_id)):
        raise HTTPException(400, "本项目所属领域没有「标段」这一层次，无需识别标段划分；"
                                 "编制将基于项目整体进行")
    if not has_parsed_tender(project_id):
        raise HTTPException(400, "尚无解析完成的招标文件，请先上传招标文件并等待解析完成")
    cur = list_lots(project_id)
    if cur["lots"] and not (body and body.overwrite):
        return {
            "ok": False,
            "need_confirm": True,
            "message": (f"本项目已有 {len(cur['lots'])} 个标段。重新识别会清空现有标段，"
                        f"并连带清除已建立的标段档案（需重新抽取）。确定继续？"),
        }
    return {"ok": True, "task_id": start_extract_lots(project_id)}


@router.get("/{project_id}/lots/status")
def lots_status(project_id: int):
    """标段识别总览：供工作台弹窗区分四种空态。

    "没有标段"有四种互不相干的原因——**还没传招标文件 / 正在解析 / 识别任务在跑 /
    确实未划分标段**，界面必须分开说，否则用户看到"未划分标段"会以为没救，
    而正确动作可能是"去传文件"或"等解析完"。
    """
    from app.services.lot_service import list_lots

    files = query(
        "SELECT pf.id, pf.file_name, pf.status,"
        " t.id AS task_id, t.progress AS task_progress, t.detail AS task_detail"
        " FROM project_file pf"
        " LEFT JOIN task t ON t.id = (SELECT MIN(t2.id) FROM task t2"
        "   WHERE t2.ref_id = pf.id AND t2.task_type = 'extract_facts' AND t2.status = 'running')"
        " WHERE pf.project_id = %s AND pf.category = 'tender' ORDER BY pf.id",
        (project_id,),
    )
    row = query_one(
        "SELECT id, status, progress, detail FROM task"
        " WHERE task_type = 'lot_extract' AND ref_id = %s ORDER BY id DESC LIMIT 1",
        (project_id,),
    )
    cur = list_lots(project_id)
    return {
        "tender_files": files,
        "has_tender": bool(files),                                    # 传过招标文件
        "parsed": any(f["status"] == "parsed" for f in files),        # 至少一个解析完成
        "parsing": any(f["status"] != "parsed" for f in files),       # 还有没解析完的
        "running": bool(row and row["status"] == "running"),          # 识别任务进行中
        "task_id": row["id"] if row else None,
        "progress": (row["progress"] or 0) if row else 0,
        "detail": (row["detail"] or "") if row else "",
        "extracted": bool(row),                                       # 识别过（成功或失败）
        "lots": cur["lots"],
        "selected_id": cur["selected_id"],
    }


# ---------------------------------------------------------------- 排版设置（导出的 Word 长什么样）

@router.get("/{project_id}/docx-style")
def get_docx_style(project_id: int):
    """本项目的 Word 排版设置 + 全部受控取值。

    options 一并返回（字体/字号/行距/页边距预设的清单在 app/core/docx_style.py），
    前端下拉直接用它、不硬编码——和领域清单同一个约定。
    """
    from app.core.docx_style import get_project_style, options

    if not query_one("SELECT id FROM project WHERE id = %s", (project_id,)):
        raise HTTPException(404, "项目不存在")
    return {"style": get_project_style(project_id).to_dict(), "options": options()}


@router.put("/{project_id}/docx-style")
def save_docx_style(project_id: int, body: dict = Body(...)):
    """保存排版设置。

    走 strict 解析：非法值当场 400 报出是哪一项，而不是悄悄换成默认值——
    这是用户刚填的值，静默改掉比报错更让人莫名其妙。
    （读库那条路径反过来，用非 strict：库里被手改坏的值不该让整份标书导不出来。）
    """
    from app.core.docx_style import from_dict, save_project_style

    if not query_one("SELECT id FROM project WHERE id = %s", (project_id,)):
        raise HTTPException(404, "项目不存在")
    try:
        style = from_dict(body, strict=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    save_project_style(project_id, style)
    return {"ok": True, "style": style.to_dict()}


# ---------------------------------------------------------------- 篇幅控制（目标正文页数）

@router.get("/{project_id}/page-plan")
def get_page_plan(project_id: int):
    """篇幅控制面板的全部数据。

    一次给全（同 /docx-style 的约定，面板打开一个来回就够）：目标页数、当前排版下的
    「字/页」系数及其**来源**（估算/实测，前端要如实标注）、目标总字数、每章的
    层级/权重/目标字数/实际字数/偏差。
    """
    from app.services.page_plan_service import build_page_plan_view

    if not query_one("SELECT id FROM project WHERE id = %s", (project_id,)):
        raise HTTPException(404, "项目不存在")
    return build_page_plan_view(project_id)


@router.post("/{project_id}/page-plan/measure")
def measure_page_plan(project_id: int):
    if not query_one('SELECT id FROM project WHERE id=%s',(project_id,)):
        raise HTTPException(404,'项目不存在')
    from app.services.pagination_service import measure
    try:
        return measure(project_id)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@router.post('/{project_id}/page-plan/adjust')
def adjust_page_plan(project_id: int):
    if not query_one('SELECT id FROM project WHERE id=%s',(project_id,)):
        raise HTTPException(404,'项目不存在')
    from app.services.pagination_service import start_adjust
    try:return start_adjust(project_id)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc


@router.put("/{project_id}/page-plan")
def save_page_plan(project_id: int, body: dict = Body(...)):
    """保存篇幅设置：目标正文页数 + 重写偏差阈值（`page_plan` JSON 列）。

    两部分语义**不同**，别混：
    - `target_pages` / `rewrite_tolerance` 是**整体覆盖**（同 /docx-style）：漏填即视为
      不设目标。前端每次提交整张表单，所以够用；单独 PUT 一个 overrides 会把目标清掉。
    - `overrides` 是**稀疏补丁**：只更新里面给出的节点，值为空 = 清掉该章的额度、回到
      按权重分摊。不在表里的节点不动——避免前端只提交改动项时误伤其它章。

    strict 解析：非法值当场 400 报出哪一项不对，而不是悄悄换成默认（同 /docx-style）。
    """
    from app.core.page_plan import from_dict, save_project_plan
    from app.services.page_plan_service import save_page_overrides

    if not query_one("SELECT id FROM project WHERE id = %s", (project_id,)):
        raise HTTPException(404, "项目不存在")
    payload = dict(body or {})
    overrides = payload.pop("overrides", None)
    try:
        plan = from_dict(payload, strict=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    save_project_plan(project_id, plan)
    if overrides:
        save_page_overrides(project_id, overrides)
    return {"ok": True, "plan": plan.to_dict()}


# ---------------------------------------------------------------- 标段档案（本标段包含什么）

@router.get("/{project_id}/lot-profile")
def get_lot_profile(project_id: int):
    """当前标段档案。

    **档案有哪些字段由项目所属领域声明**（`domains.lot_fields`）：本域没声明字段时，
    这里返回 {found: false}——不是"还没建"，是**本域没有标段这一层次**（低空/软件测试
    都是单一交付）。前端据此不显示「标段档案」入口。
    """
    from app.services.lot_profile_service import get_profile

    prof = get_profile(project_id)
    return {"found": bool(prof), "profile": prof}


class LotProfileBody(BaseModel):
    text: str | None = None          # 编辑后的"一段文字"（自动解析回结构化字段）
    milestones: list | None = None   # 关键里程碑（表格编辑）
    profile: dict | None = None      # 兼容：整体覆盖


@router.put("/{project_id}/lot-profile")
def save_lot_profile(project_id: int, body: LotProfileBody):
    """人工编辑保存标段档案（一段文字 + 里程碑）。"""
    from app.services.lot_profile_service import save_profile
    from app.services.lot_service import selected_lot_code

    code = selected_lot_code(project_id)
    if not code:
        raise HTTPException(400, "该项目未选定标段")
    ok = save_profile(project_id, code, text=body.text,
                      milestones=body.milestones, profile=body.profile)
    return {"ok": ok}


@router.post("/{project_id}/lot-profile/rebuild")
def rebuild_lot_profile(project_id: int):
    """重新抽取标段档案（后台任务：含 LLM 补全，前端轮询 task 进度）。"""
    from app.core.domains import domain_of_project, lot_fields_of
    from app.services.lot_profile_service import start_rebuild
    from app.services.lot_service import list_lots

    # 本域没有标段这一层次时直接说清楚：否则会起一个"抽不出任何字段"的空任务，
    # 用户看到进度条走完却什么也没有。**先在这一层挡掉**，别让它跑到后台。
    if not lot_fields_of(domain_of_project(project_id)):
        raise HTTPException(400, "本项目所属领域没有「标段」这一层次（未声明标段档案字段），"
                                 "无需建立标段档案；编制将基于项目整体进行")
    # 前置校验：没有选定标段就没有"本标段档案"可建。若直接起后台任务，它必然抛
    # RuntimeError，而 task.detail 里带的是整段 traceback，会原样冒到界面上。
    lots = list_lots(project_id).get("lots") or []
    if not lots:
        raise HTTPException(400, "本项目招标文件未划分标段，无需建立标段档案")
    if not any(l.get("selected") for l in lots):
        raise HTTPException(400, "尚未选定标段，请先选择要编制档案的标段")
    return {"ok": True, "task_id": start_rebuild(project_id)}


@router.get("/{project_id}/lot-profile/rebuild-status")
def lot_profile_rebuild_status(project_id: int):
    """该项目最近一次"档案抽取"任务的状态。

    供前端在**刷新页面/重新打开弹窗**时恢复"抽取中"的遮罩（任务在服务端跑，不会因刷新中断）。
    """
    row = query_one(
        "SELECT id, status, progress, detail FROM task"
        " WHERE task_type = 'lot_profile' AND ref_id = %s ORDER BY id DESC LIMIT 1",
        (project_id,),
    )
    if not row:
        return {"running": False, "task_id": None, "status": None, "progress": 0, "detail": ""}
    return {
        "running": row["status"] == "running",
        "task_id": row["id"],
        "status": row["status"],
        "progress": row["progress"] or 0,
        "detail": row["detail"] or "",
    }


@router.post("/{project_id}/lot-relabel")
def start_lot_relabel(project_id: int):
    """启动标段归属全量重标（规则 + AI 补判定，后台任务，进度见 task）。"""
    from app.services.lot_scope_service import start_relabel

    task_id = start_relabel(project_id)
    return {"ok": True, "task_id": task_id}


# ---------------------------------------------------------------- 招标要求（覆盖检查清单）

@router.get("/{project_id}/requirements")
def get_requirements(project_id: int, category: str = ""):
    """招标要求清单（评分办法/技术要求，含建议章节与自检状态）。"""
    from app.services.requirement_service import list_requirements

    return list_requirements(project_id, category)


class ReqStatus(BaseModel):
    status: str  # pending / confirmed / ignored


@router.patch("/requirements/{req_id}/status")
def set_requirement_status(req_id: int, body: ReqStatus):
    from app.services.requirement_service import set_status

    if not set_status(req_id, body.status):
        raise HTTPException(400, "非法状态或要求不存在")
    return {"ok": True}


class ReqEdit(BaseModel):
    title: str | None = None
    content: str | None = None
    weight: float | None = None
    category: str | None = None
    suggested_chapter: str | None = None


@router.patch("/requirements/{req_id}")
def update_requirement(req_id: int, body: ReqEdit):
    """编辑招标要求（标题/内容/分值/类别/建议章节）。"""
    from app.services.requirement_service import update_requirement as _upd

    if not _upd(req_id, body.model_dump(exclude_none=True)):
        raise HTTPException(400, "没有可更新的字段或要求不存在")
    return {"ok": True}


@router.delete("/requirements/{req_id}")
def delete_requirement(req_id: int):
    """删除招标要求（AI 抽错或与本标段无关的条款）。"""
    from app.services.requirement_service import delete_requirement as _del

    if not _del(req_id):
        raise HTTPException(404, "要求不存在")
    return {"ok": True}


class ReqConfirmAllBody(BaseModel):
    project_id: int


@router.post("/requirements/confirm_all")
def confirm_all_requirements(body: ReqConfirmAllBody):
    """一键批量确认：全部待确认招标要求置为已确认。"""
    from app.services.requirement_service import confirm_all

    n = confirm_all(body.project_id)
    return {"ok": True, "confirmed": n}

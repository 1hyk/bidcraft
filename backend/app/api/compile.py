"""编制流程 API（异步）：start 立即返回 → 轮询 status 拿进度。

解决 79 节全量生成导致同步 HTTP 超时的问题：start 在后台线程跑「目录 → 思路」，
**跑到底自动落库**；前端轮询 status 拿进度与流式目录快照。

⚠ v0.2（2026-09-10）起图里**没有 interrupt**（见 compile_graph.build_compile_graph），
所以「跑到中断挂起 → 用户点确认 → resume」这条链路**已经不存在**。本文件剩下的
`/resume` 端点与 `CompileResume` 模型、`compile_service` 里的 `STAGE_CONFIRM_*` /
`_auto_confirm` / `resume()`、以及前端那两个确认卡片（`WorkspaceView` 的
`confirmToc`/`confirmOutline`），全是那套机制的遗留，**现在走不到**——它们只可能在
`compile_session` 里还留着改版前 `stage='confirm_toc'` 的历史行时被唤醒。
**新写代码不要照着 resume 抄。**
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.services import compile_service, writer_service

router = APIRouter(prefix="/api/compile", tags=["compile"])


class CompileStart(BaseModel):
    project_id: int
    # 以下均为可选覆盖项：留空则由后端从 DB 组装（assemble_inputs）
    professions: list[str] = []
    tender_requirements: str = ""


class CompileResume(BaseModel):
    project_id: int
    approval: dict  # {"action": "confirm", "toc": {...}} 等


@router.post("/start")
def start(body: CompileStart):
    """启动编制流程（后台线程），立即返回当前状态。

    项目信息/已确认事实/专业标签由后端从 DB 组装，前端只需传 project_id。
    """
    inputs = compile_service.assemble_inputs(body.project_id)
    return compile_service.start_compile(
        project_id=body.project_id,
        project_info=inputs["project_info"],
        professions=body.professions or inputs["professions"],
        tender_requirements=body.tender_requirements or inputs["tender_requirements"],
        facts=inputs["facts"],
        knowledge_pages={},  # 知识页改由 matching 服务在思路生成时按标题路径匹配
        tender_toc=inputs["tender_toc"],  # 招标文件规定的施组目录（有则优先）
    )


@router.get("/status/{project_id}")
def status(project_id: int):
    """查询编制进度（`interrupt` 字段恒为 None，见模块 docstring）。"""
    return compile_service.get_status(project_id)


@router.post("/resume")
def resume(body: CompileResume):
    """⚠ 遗留端点：没有中断点可确认了（见模块 docstring），仅旧会话才可能走到。"""
    return compile_service.resume(body.project_id, body.approval)


# ---------------------------------------------------------------- 正文批量生成

class BatchBody(BaseModel):
    project_id: int


@router.post("/write_batch")
def write_batch(body: BatchBody):
    """批量生成正文：并发消费 thought_ready 章节（进度走 /status 轮询）。"""
    return writer_service.start_batch(body.project_id)


@router.post("/write_all")
def write_all(body: BatchBody):
    """**全文编制**：为所有「已有编写思路、尚未生成正文」的章节生成正文。

    （2026-09-10 用户拍板：只补未写的，**不覆盖**已生成/已确认的章节——
    等价于批量补齐待写章节；要重写某章请用单章"生成本章"。）
    """
    return writer_service.start_batch(body.project_id)


@router.post("/write_stop")
def write_stop(body: BatchBody):
    """中途停止批量生成：进行中的章节写完后停，已写内容保留。"""
    return writer_service.stop_batch(body.project_id)

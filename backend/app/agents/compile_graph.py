"""LangGraph 编排状态机：目录生成 → 编写思路（正文按章触发）。

自主度设计（方案 3.4，**2026-09-10 修订**）：
- 里程碑**不再有人工确认中断**：目录/思路生成完直接落库，用户在目录树与对话里调整
  （见 `build_compile_graph` 的 v0.2 决策注释）。原先「用 interrupt() 写死人工把关」的
  说法已作废——本文件里**没有任何 interrupt() 调用**，`START → generate_toc →
  generate_outlines → END` 三条直线边跑到底；
- 环节内部由 LLM 自主决定（目录装配、思路生成）。

⚠ 这段注释曾与 `build_compile_graph` 自相矛盾（一个说有人工把关、一个说没有），
后果不小：`tests/verify_compile_flow.py` 照着**已摘掉的机制**写、去等一个不存在的中断点，
于是每次都静默跳过、退出码还是 0，还拿 `project_id=999` 真跑落库（2026-10-16 重写：
改为断言本文件的当前契约，并加了一条源码级检查——模块里出现任何 `interrupt(...)`
调用就报红）。同源的 `tests/verify_async_compile.py` 已删；`compile_service`、
`api/compile.py`、`chat_agent` 里留下的 confirm_* 分支也各自标了「现已走不到」。
**改流程时请把这段一起改。**

持久化（方案 3.6）：checkpointer 用 PyMySQLSaver（MySQL，与业务库同库）。
pymysql 连接非线程安全，故每次 invoke 在调用线程内新建连接与 graph 实例，
靠 thread_id 在 MySQL 检查点续接，进程重启不丢状态（`compile_service` 里
`_finish_from_result`/`_auto_confirm` 的 interrupted 分支是这套机制的遗留，现已走不到）。

正文生成不在本状态机内（2026-09-03 决策）：确认思路后流程结束，
正文由 services/writer_service 按章触发（单章流式 / 批量并发可停止）。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypedDict

import pymysql
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agents.orchestrate_agent import generate_outline
from app.core.config import settings

# 思路生成并发度（模型 tpm 500 万，20 并发仍有余量；限流由 llm 指数退避兜底）
OUTLINE_CONCURRENCY = 20


class CompileState(TypedDict):
    project_id: int
    project_info: str              # 项目名/工程类型/标段概况
    professions: list[str]         # 本项目涉及的专业
    tender_requirements: str       # 招标要求与评分办法
    tender_toc: dict               # 招标文件规定的施组目录（有则最高优先级，方案 v0.2）
    facts: list[dict]              # 全局事实子集（已确认）

    toc: dict                      # 生成的目录
    toc_confirmed: bool            # 目录是否确认
    outlines: list[dict]           # 逐章编写思路（含 _page_ids 匹配的知识页）
    outline_confirmed: bool


# ---- 节点 ----

def _flatten_toc(toc: dict) -> list[dict]:
    """把章→节树展平为章节列表，每项 {title, level, parent?}。"""
    flat = []
    def walk(nodes, level=1, parent=None):
        for ch in nodes:
            item = {"title": ch["title"], "level": level}
            if parent is not None:
                item["parent"] = parent
            flat.append(item)
            walk(ch.get("children") or [], level + 1, ch["title"])

    walk(toc.get("chapters") or [])
    return flat


def _chapter_skeleton(domain: str, tender_toc: dict | None) -> list[str] | None:
    """章级骨架候选：**招标规定的目录 > 本领域声明的 chapters > 不给**（None）。

    这一档此前根本不存在：`generate_toc_titles` 无条件吃 `orchestrate_agent.
    SKELETON_CHAPTERS`（11 个铁路土建章名），lowalt 项目照领一份，而知识库里对得上
    评审办法的语料在**章级**压根够不着（`chapter_skeleton` 只喂子节层）。

    最后一档（None）不是"降级"——没招标、域也没声明时，让模型照项目信息自己判断该有
    哪几章，比撞一张不知道属于哪个领域的章名表强。

    招标已规定目录时**返回 None 而不是领域骨架**：红线已定，再摆一份候选只会让模型
    在两者之间摇摆。
    """
    from app.core.domains import chapters_of

    if (tender_toc or {}).get("chapters"):
        return None
    return list(chapters_of(domain))


def node_generate_toc(state: CompileState) -> dict:
    """两阶段流式生成目录：先出章骨架（秒级可见），再并发逐章展开子节，
    每章完成即写 partial 快照——前端目录栏实时"长出来"，用户可看生成进度。
    生成完成后**自动落库**（v0.2 决策：不设确认环节，用户在目录树上自行调整）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.agents.orchestrate_agent import generate_toc_children, generate_toc_titles
    from app.core.domains import domain_of_project
    from app.services import compile_service

    project_id = state["project_id"]
    _dom = domain_of_project(project_id)
    _tender_toc = state.get("tender_toc") or None
    titles = generate_toc_titles(
        state["project_info"], state["professions"],
        state["tender_requirements"], _tender_toc,
        chapters=_chapter_skeleton(_dom, _tender_toc), domain=_dom,
    )
    if not titles:
        raise ValueError("目录章骨架生成失败（空结果）")
    # 阶段一快照：章骨架立即可见
    toc = {"chapters": [{"title": t["title"], "children": []} for t in titles]}
    compile_service.update_progress(project_id, total=len(titles))
    compile_service.save_partial_toc(project_id, toc)

    # 阶段二：并发逐章展开子节，每章完成即推快照
    # 知识库骨架：同类标书里每一章实际用过哪些子节。招标格式平铺时（本项目九个章名
    # 一个子节都没规定），子节此前只能由模型现编，而知识库里躺着一份对得上评审办法的
    # 现成骨架——目录生成阶段从没拿到过知识页，它一直没机会参与。**页摘要只读一次、
    # 逐章复用**，不要在闭包里每章重读一遍库。
    from app.services.matching import chapter_skeleton, load_page_summaries

    # 域在上面取章级骨架时已解析好；project_id 是第二道墙（同域别的项目的标书不进池）
    _pages = load_page_summaries(_dom, project_id)

    def _children(ch):
        ch["children"] = generate_toc_children(
            ch["title"], state["project_info"], state["professions"],
            state["tender_requirements"],
            # 招标规定目录要传到子节层：规定了就用它的子节原样照抄，没规定才让模型编。
            # 见 generate_toc_children 的注释——这条线此前只传到章那一层。
            tender_toc=state.get("tender_toc") or None,
            # 知识库骨架同传：招标有子节、知识库也有骨架时，两者都摆给模型择优合并。
            skeleton=chapter_skeleton(ch["title"], _pages), domain=_dom,
        )
        return ch

    with ThreadPoolExecutor(max_workers=OUTLINE_CONCURRENCY) as pool:
        futures = {pool.submit(_children, ch): ch for ch in toc["chapters"]}
        done = 0
        errors = []
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001 收齐错误后统一失败，保留原目录
                print(f"[目录] 子节生成失败: {e}")
                errors.append(f"{futures[fut]['title']}: {e}")
            done += 1
            compile_service.update_progress(project_id, progress=done)
            compile_service.save_partial_toc(project_id, toc)

    if errors:
        raise ValueError("子节生成失败，原目录未替换：" + "；".join(errors))

    # 落库：**先清空旧目录树（连同已写正文与思路）再写入新目录**。
    # 2026-09-10 用户拍板：「重新生成目录」＝真正的重来，前端已二次确认并明确告知
    # "已编写内容将全部清空"。此前 save_tree 在已有目录时直接跳过（写入 0 个节点），
    # 导致新目录不生效、思路却按新目录跑一遍，出现"目录来回切换"的怪象。
    try:
        from app.services.section_service import clear_tree, save_tree

        clear_tree(project_id)
        n = save_tree(project_id, toc.get("chapters", []))
        print(f"[目录落库] 写入 {n} 个节点")
    except Exception as e:  # noqa: BLE001 落库失败必须上报，不能显示成功
        print(f"[目录落库失败] {e}")
        raise
    return {"toc": toc, "toc_confirmed": True}


def node_generate_outlines(state: CompileState) -> dict:
    """逐章生成编写思路，并发执行避免串行过慢。

    知识页匹配（matching 三级漏斗）在此进行：每章匹配结果记入 outline._page_ids，
    正文生成按 _page_ids 直接取用。生成后**自动落库**（v0.2 决策：无确认环节）。
    """
    from app.core.domains import domain_of_project
    from app.services.experience_service import match_experiences
    from app.services.lot_profile_service import lot_brief
    from app.services.matching import load_page_summaries, match_pages

    from app.core.config import settings
    from app.core.generation_scope import select_roots

    # 历史检查点也可能保留整份目录，恢复时同样只处理当前范围。
    from app.services.section_service import get_tree

    roots = state["toc"].get("chapters") or []
    scoped = select_roots(roots, settings.generation_root_title)
    if roots and not scoped:
        scoped = select_roots(roots, settings.generation_root_title,
                              [n["title"] for n in get_tree(state["project_id"])])
    flat = _flatten_toc({"chapters": scoped})
    # 知识页只在项目所属领域里找：跨域知识页（如铁路施组）对本项目没有参考价值
    dom = domain_of_project(state.get("project_id"))
    # 第二道墙（同域别的项目的标书不进池）——见 matching._scope_sql
    summaries = load_page_summaries(dom, state.get("project_id"))
    # 本标段工程构成：思路只能围绕本标段实际包含的工程展开（与目录裁剪同规则）
    lot_text = lot_brief(state["project_id"]) if state.get("project_id") else ""

    def _one(item: dict) -> dict:
        path = [item["parent"]] if item.get("parent") else []
        pages = match_pages(item["title"], path, summaries, domain=dom,
                            project_id=state.get("project_id"))
        outline = generate_outline(
            chapter_title=item["title"],
            chapter_type="",  # 章节类型字典已废弃，匹配按标题路径
            project_facts=state["facts"],
            knowledge_pages=pages,
            experiences=match_experiences(item["title"], path, dom),
            lot_brief=lot_text, domain=dom, project_id=state.get("project_id"),
        )
        outline["_toc_title"] = item["title"]
        outline["_parent"] = item.get("parent", "")
        outline["_level"] = item["level"]
        outline["_page_ids"] = [p["id"] for p in pages]
        return outline

    outlines: list[dict] = [None] * len(flat)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=OUTLINE_CONCURRENCY) as pool:
        futures = {pool.submit(_one, item): i for i, item in enumerate(flat)}
        for fut in as_completed(futures):
            i = futures[fut]
            outlines[i] = fut.result()
            print(f"[思路] {i + 1}/{len(flat)} {flat[i]['title'][:30]}")

    # 思路自动落库（v0.2 决策：不设确认环节）
    try:
        from app.services.section_service import save_outline

        for o in outlines or []:
            if not save_outline(state["project_id"], o.get("_toc_title", ""), o):
                raise ValueError(f"章节思路未保存：{o.get('_toc_title', '')}")
        print(f"[思路落库] {len(outlines)} 章状态置 thought_ready")
    except Exception as e:  # noqa: BLE001
        print(f"[思路落库失败] {e}")
        raise
    return {"outlines": outlines, "outline_confirmed": True}


def build_compile_graph() -> StateGraph:
    """编译编制状态机（v0.2 决策：无人工确认 interrupt，目录/思路生成后自动落库，
    用户在目录树与对话中自行调整）。"""
    g = StateGraph(CompileState)

    g.add_node("generate_toc", node_generate_toc)
    g.add_node("generate_outlines", node_generate_outlines)

    g.add_edge(START, "generate_toc")
    g.add_edge("generate_toc", "generate_outlines")
    g.add_edge("generate_outlines", END)

    return g


# ---- 执行入口（MySQL 检查点，每 invoke 独立连接，线程安全） ----

_saver_ready = False  # saver.setup() 每进程只需一次（CREATE TABLE IF NOT EXISTS）


def _align_checkpoint_collation(conn) -> None:
    """让检查点表与 langgraph 查询里的 JSON_TABLE 派生列用同一种排序规则。

    langgraph 的建表 DDL 把 utf8mb4_general_ci 写死（为兼容 MySQL 5.7），但它的
    get_tuple 查询会 join 一个 JSON_TABLE 派生列，该列未指定 COLLATE，取的是
    **utf8mb4 的默认排序规则**（MySQL 8 = utf8mb4_0900_ai_ci）。两个都是 IMPLICIT
    排序规则，MySQL 不肯隐式转换，于是编制流程一启动就报：

        (1267, "Illegal mix of collations (utf8mb4_general_ci,IMPLICIT)
                and (utf8mb4_0900_ai_ci,IMPLICIT) for operation '='")

    把本会话的 utf8mb4 默认排序规则降回 general_ci，派生列就与框架的表对齐。
    注意不能用连接的 collation 参数——那只改 collation_connection，派生列不看它。

    该连接只服务检查点表（业务查询走 app.core.database，不受影响）。
    """
    with conn.cursor() as cur:
        cur.execute("SET SESSION default_collation_for_utf8mb4 = 'utf8mb4_general_ci'")


def _invoke(payload, thread_id: str) -> dict:
    """以 MySQL 检查点执行/恢复状态机。payload 为输入 dict、Command(resume=...) 或 None。"""
    global _saver_ready
    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        # 检查点表的排序规则必须与 JSON_TABLE 派生列一致，否则 get_tuple 报 1267
        # （见 _align_checkpoint_collation）。每 invoke 一条新连接，故每次都要设。
        _align_checkpoint_collation(conn)
        saver = PyMySQLSaver(conn)
        if not _saver_ready:
            saver.setup()
            _saver_ready = True
        graph = build_compile_graph().compile(checkpointer=saver)
        return graph.invoke(payload, {"configurable": {"thread_id": thread_id}})
    finally:
        conn.close()


def _extract_interrupt(result: dict) -> tuple[dict, bool, dict | None]:
    """从 invoke 结果中拆出 (state, interrupted, interrupt_value)。"""
    interrupts = result.pop("__interrupt__", None) if isinstance(result, dict) else None
    if interrupts:
        # 取第一个中断点的 value（可能是 dict 或任意类型）
        first = interrupts[0]
        value = first.value if hasattr(first, "value") else first
        return result, True, value
    return result, False, None


def new_compile(thread_id: str, project_id: int, project_info: str, professions: list[str], tender_requirements: str = "", facts: list[dict] | None = None, tender_toc: dict | None = None) -> dict:
    """启动一次编制流程，**一路跑到 END**（v0.2 起图里没有 interrupt，见 build_compile_graph）。

    thread_id 由调用方生成（每次新 run 换新 id，避免复用旧检查点状态残留）。
    返回：{state, interrupted, interrupt}——没有 interrupt 节点，所以 interrupted 恒为 False；
    后两个字段保留，只为让 `compile_service._finish_from_result` 那条遗留分支不至于报错。
    """
    result = _invoke(
        {
            "project_id": project_id,
            "project_info": project_info,
            "professions": professions,
            "tender_requirements": tender_requirements,
            "tender_toc": tender_toc or {},
            "facts": facts or [],
            "toc": {},
            "toc_confirmed": False,
            "outlines": [],
            "outline_confirmed": False,
        },
        thread_id,
    )
    state, interrupted, value = _extract_interrupt(result)
    return {"state": state, "interrupted": interrupted, "interrupt": value}


def resume_compile(thread_id: str, approval: dict) -> dict:
    """从检查点续跑，传入用户确认/修改结果。

    ⚠ **没有 interrupt 可恢复了**（v0.2）：本图跑到底，`resume_compile` 现在等于
    "拿这个 thread_id 再 invoke 一次"。旧代码里它承担「用户在页面上点确认目录/确认思路」
    的语义，那套流程已改成目录/思路自动落库、用户在目录树里直接改。
    仅 `compile_service._auto_confirm` 那条**现已走不到**的分支还在调它。
    """
    result = _invoke(Command(resume=approval), thread_id)
    state, interrupted, value = _extract_interrupt(result)
    return {"state": state, "interrupted": interrupted, "interrupt": value}


def resume_interrupted(thread_id: str) -> dict:
    """进程重启后续跑未完成的状态机（从最近检查点继续，非 interrupt 恢复）。"""
    result = _invoke(None, thread_id)
    state, interrupted, value = _extract_interrupt(result)
    return {"state": state, "interrupted": interrupted, "interrupt": value}

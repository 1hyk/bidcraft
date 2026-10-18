"""校验「编制状态机」当前**真的**承诺了什么（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_compile_flow.py

**上一版坏在哪**（2026-10-16 重写）：它在等一个**已经被摘掉的** `interrupt()`——
v0.2 起图里没有中断点，`new_compile` 一路跑到 END，于是它每次都落到
「⚠ 未在确认目录处中断」然后 return，**退出码 0**。查的地方根本不存在，
于是它既从不报警，又每次真拿 `project_id=999` 跑一遍完整落库，往一个不存在的项目里
灌 18 个 `chapter_node` 行（连同 `verify_async_compile.py` 那批，已于 2026-10-16 清理）。
**静默跳过 + 污染数据库**，一样不少。

现在它验的是图今天承诺的契约：
  1. 一次 invoke 跑到 END，`interrupted` 恒为 False（没有任何中断可恢复）；
  2. 目录落库有内容：章有子节、思路条数 = 展平后的节点数，子节带父章名；
  3. 落库顺序是 **先 clear_tree 再 save_tree**（2026-09-10「重生成目录不生效」的成因）；
  4. 正文**不在图里**（2026-09-03 决策，正文归 `writer_service`）——state 里没有 chapters；
  5. `_chapter_skeleton`：招标已规定目录时不再摆领域骨架（招标文件为准）；
  6. **源码级**：整个模块的代码里不存在 `interrupt(...)` 调用（docstring 里提这个词不算）。

**它不碰模型，也不写业务表**：模型调用与落库边界全被换成本地桩函数（见 `_stub_all`），
所以一次运行是毫秒级、零成本、不产生任何业务数据。代价说清楚：这里验的是
**接线与契约**（谁被调用、什么顺序、产出什么形状），**不验模型输出质量**，
也不验真实 SQL 写入——那两条只能靠人工起一次真编制、看目录树与生成的正文。

唯一的库交互是 LangGraph 检查点（框架表），跑完按 thread_id 自行清理，不留垃圾。
"""
from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import app.agents.compile_graph as cg                      # noqa: E402
from app.core.domains import chapters_of                   # noqa: E402

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


# ---- 本次假项目的"目录"：章 → 子节。展平后 3 + 2 + 3 + 1 = 9 个节点 ----
CHAPTERS = ["第一章 编制依据", "第二章 技术方案", "第三章 质量保证措施"]
SECTIONS = {
    "第一章 编制依据": ["第一节 招标文件响应", "第二节 编制说明"],
    "第二章 技术方案": ["第一节 总体技术路线", "第二节 关键算法设计", "第三节 平台部署"],
    "第三章 质量保证措施": ["第一节 质量控制流程"],
}
FLAT_N = len(CHAPTERS) + sum(len(v) for v in SECTIONS.values())
DOMAIN = "lowalt"          # 领域骨架走真实声明（chapters_of），不复刻一份到测试里
FAKE_PAGE_IDS = [700, 701]

calls: dict = {}


def _stub_all() -> dict:
    """把模型与落库边界全换成桩，返回一份「谁被怎么调用了」的记录。

    打桩位置要照**导入发生的位置**来：`generate_outline` 在 compile_graph 顶层 import
    （绑在本模块命名空间，得打 cg 上的），其余都是函数体内 import（调用时才去
    原模块取，打原模块属性即可）。打错了不会报错，只会静默地真跑——所以下面
    每一条都有对应断言在查它有没有被调用。
    """
    from app.agents import orchestrate_agent as oa
    from app.core import domains
    from app.services import compile_service, experience_service, lot_profile_service
    from app.services import matching, section_service

    rec = {
        "titles": 0, "skeleton_for_titles": None, "children": [], "children_skeletons": [],
        "outlines": [], "tree": [], "outlines_saved": [], "partial": 0, "totals": [],
    }
    real = {}


    def _swap(owner, name, fn):
        real[(id(owner), name)] = (owner, name, getattr(owner, name))
        setattr(owner, name, fn)

    def titles(project_info, professions, tender_requirements, tender_toc, *, chapters=None, domain=None):
        rec["titles"] += 1
        rec["skeleton_for_titles"] = chapters
        return [{"title": t} for t in CHAPTERS]

    def children(title, project_info, professions, tender_requirements, *, tender_toc=None, skeleton=None, domain=None):
        rec["children"].append(title)
        rec["children_skeletons"].append(skeleton)
        return [{"title": s} for s in SECTIONS[title]]

    def outline(chapter_title, chapter_type, project_facts, knowledge_pages, experiences, lot_brief):
        rec["outlines"].append(chapter_title)
        return {"chapter_title": chapter_title, "thinking": f"{chapter_title} 的写法"}

    _swap(oa, "generate_toc_titles", titles)
    _swap(oa, "generate_toc_children", children)
    _swap(cg, "generate_outline", outline)

    _swap(domains, "domain_of_project", lambda pid: DOMAIN)
    _swap(matching, "load_page_summaries", lambda *a, **k: [{"id": 1, "title": "样式语料"}])
    _swap(matching, "chapter_skeleton", lambda *a, **k: ["知识库骨架·子节一"])
    _swap(matching, "match_pages", lambda *a, **k: [{"id": i} for i in FAKE_PAGE_IDS])
    _swap(experience_service, "match_experiences", lambda *a, **k: [])
    _swap(lot_profile_service, "lot_brief", lambda *a, **k: "本标段：机巢部署、飞控算法")

    def clear_tree(pid):
        rec["tree"].append(("clear", pid))

    def save_tree(pid, chs):
        rec["tree"].append(("save", pid, len(chs)))
        return 12

    def save_outline(pid, title, o):
        rec["outlines_saved"].append(title)

    def update_progress(pid, *, total=None, progress=None):
        if total is not None:
            rec["totals"].append(total)

    def save_partial_toc(pid, toc):
        rec["partial"] += 1

    _swap(section_service, "clear_tree", clear_tree)
    _swap(section_service, "save_tree", save_tree)
    _swap(section_service, "save_outline", save_outline)
    _swap(compile_service, "update_progress", update_progress)
    _swap(compile_service, "save_partial_toc", save_partial_toc)

    return {"rec": rec, "real": real}


def _restore(real: dict) -> None:
    for owner, name, fn in real.values():
        setattr(owner, name, fn)


def _interrupt_calls(src: str) -> list[int]:
    """源码里所有 `interrupt(...)` 调用所在行号。

    只认 `ast.Call`，所以 docstring / 注释里提「interrupt」不会被误判——
    本文件的教训正是「文字说一套、代码做一套」，这条查的是代码那套。
    """
    return sorted(n.lineno for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "interrupt")


def _purge_checkpoints(*thread_ids: str) -> int:
    """删掉这些 thread_id 的检查点行。表名从 information_schema 现查，不写死。"""
    from app.core.database import execute, query
    tables = query(
        "SELECT table_name AS t FROM information_schema.columns"
        " WHERE table_schema = DATABASE() AND column_name = 'thread_id'"
    )
    n = 0
    for row in tables:
        for tid in thread_ids:
            execute(f"DELETE FROM `{row['t']}` WHERE thread_id = %s", (tid,))
            n += 1
    return n


def main() -> int:
    src = Path(cg.__file__).read_text(encoding="utf-8")

    # ---- 1. 源码级：图里没有中断点 ----
    print("1) 图里没有任何 interrupt() 调用（不看注释，看代码）")
    check("全模块的无 interrupt 调用", _interrupt_calls(src), [])
    check("build 出来的是 2 个节点", len(cg.build_compile_graph().nodes), 2)

    # ---- 2. 章骨架的选源顺序（纯函数，不连库不调模型） ----
    print("\n2) _chapter_skeleton：招标规定目录 > 领域声明")
    check("给了招标目录 → 返回 None（不再摆领域骨架，免得模型两边摇摆）",
          cg._chapter_skeleton(DOMAIN, {"chapters": [{"title": "第一章 总则"}]}), None)
    check("没给招标目录 → 用本领域声明的章骨架",
          cg._chapter_skeleton(DOMAIN, None), list(chapters_of(DOMAIN)))
    check("招标目录是空壳（chapters 为空）→ 仍走领域骨架",
          cg._chapter_skeleton(DOMAIN, {"chapters": []}), list(chapters_of(DOMAIN)))

    # ---- 3. 一次 invoke 跑到底 ----
    thread_id = f"verify_compile_flow_{int(time.time())}"
    print(f"\n3) new_compile 一次跑到底（thread_id={thread_id}）")
    stubs = _stub_all()
    try:
        r = cg.new_compile(
            thread_id=thread_id,
            project_id=0,            # 落库全被打了桩，这个 id 只是个标签，写不进任何业务表
            project_info="某市低空无人机综合管控平台建设项目，含机巢部署、飞控算法、空域协调",
            professions=["机巢部署", "飞控算法", "空域协调", "多机协同"],
            tender_requirements="招标文件要求：技术方案需覆盖技术路线、资源配置、管理措施",
            facts=[],
        )
    finally:
        _restore(stubs["real"])
        purged = _purge_checkpoints(thread_id, "verify_compile_flow")
        print(f"     （已恢复桩函数；清理检查点行：{purged} 条/表项）")
    rec = stubs["rec"]
    state = r["state"]

    check("interrupted 恒为 False（没有中断可恢复）", r["interrupted"], False)
    check("没有中断值", r["interrupt"], None)
    check("state 里没有 __interrupt__ 残留", "__interrupt__" in state, False)

    # ---- 4. 目录 ----
    print("\n4) 目录：章骨架 + 逐章子节")
    check("章数", [c["title"] for c in state["toc"]["chapters"]], CHAPTERS)
    check("每章都展开了子节",
          {c["title"]: [s["title"] for s in c["children"]] for c in state["toc"]["chapters"]},
          {k: [s for s in v] for k, v in SECTIONS.items()})
    check("章骨架只生成一次", rec["titles"], 1)
    check("章骨架取自领域声明",
          rec["skeleton_for_titles"], list(chapters_of(DOMAIN)))
    check("每章各调一次子节生成", rec["children"], CHAPTERS)
    check("子节也拿到了知识库骨架（子节层能吃到语料）",
          rec["children_skeletons"], [["知识库骨架·子节一"]] * len(CHAPTERS))
    check("总进度按章数上报", rec["totals"], [len(CHAPTERS)])
    check("生成过程中有 partial 快照（前端目录栏实时长出来）", rec["partial"] >= len(CHAPTERS), True)

    # ---- 5. 思路 ----
    print("\n5) 思路：条数、父子、知识页接线")
    check("思路条数 = 展平后的节点数", len(state["outlines"]), FLAT_N)
    check("每条都过上模型", rec["outlines"], [o["_toc_title"] for o in state["outlines"]])
    lv2 = {o["_toc_title"]: o["_parent"] for o in state["outlines"] if o["_level"] == 2}
    check("子节挂到了正确的父章", lv2, {s: k for k, v in SECTIONS.items() for s in v})
    check("一级节点无父章", [o["_parent"] for o in state["outlines"] if o["_level"] == 1], [""] * len(CHAPTERS))
    check("知识页 id 已接线到 _page_ids（正文按它取语料）",
          sorted({tuple(o["_page_ids"]) for o in state["outlines"]}), [tuple(FAKE_PAGE_IDS)])
    check("思路逐条落库", len(rec["outlines_saved"]), FLAT_N)

    # ---- 6. 落库顺序与"正文不在图里" ----
    print("\n6) 落库顺序 / 状态字段")
    kinds = [c[0] for c in rec["tree"]]
    check("先清空旧树、再写新树（2026-09-10 的成因）", kinds, ["clear", "save"])
    check("写的正是本次的章数", rec["tree"][1][2], len(CHAPTERS))
    check("目录/思路都是自动确认（无人确认环节）",
          (state["toc_confirmed"], state["outline_confirmed"]), (True, True))
    check("正文不在图里（2026-09-03 决策）", "chapters" in state, False)

    print("\n结论：" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

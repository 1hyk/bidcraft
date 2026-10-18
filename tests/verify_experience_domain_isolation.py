"""校验「经验库」这条链按领域隔离（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_experience_domain_isolation.py

背景（2026-10-15，第 3 组）：`experience_item` 是**审计里唯一真正没隔离的那张表**——
`knowledge_doc`/`knowledge_page` 都有 `domain` 列、匹配时按域过滤，经验库没有：

  · 读侧：`match_experiences` 读**全表**，只按章节标题相似度筛 → 任何领域的经验都可能
    注进任何项目的任何一章（`chat_agent` 的 `search_experiences` 工具同理）；
  · 写侧：`distill_*_task` 手里**明明有** `project_id`，却在 `save_experience` 那一跳
    只把它塞进 `source` JSON 里，没落成可查询的列。

修法：落 `domain` 而**不是** `project_id`——经验是跨项目复用的写法规则，按项目隔离
等于废掉这个库；按域隔离才和知识库同一套口径。`source.project_id` 只作来源留痕。

检查：
  1. 表结构：`domain` 列在、NOT NULL、有 idx_domain；建表 SQL 里也有（新库直接带上）；
  2. 落域：显式传 / 从 `source.project_id` 反查，两条路都落对；去重**按域**做；
  3. 按域匹配：A 域的经验不会出现在 B 域的匹配里，反之亦然；
     不传 domain 时才读全库（那是盘点口径，正常匹配务必传）；
  4. 调用点：五处 `match_experiences` 都传了域，`search_experiences` 工具按域过滤；
  5. 真库：项目 4（低空）的经验只在低空域里匹配得到。

本脚本会**临时插两条经验**（内容带 `__verify_exp__` 标记）跑完即删；不碰真实数据。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

ok = True
MARK = "__verify_exp__"
#: 一个**一次性**的域 key：只在本次校验里当"另一个领域"用，不注册进 DOMAINS
#: （注册了前端就会多出一个假领域，见 verify_lot_profile_isolation 的同一条教训）
OTHER_DOMAIN = "__verify_other_domain__"
CHAPTER = "施工总平面布置"


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def check_true(label: str, got) -> None:
    check(label, bool(got), True)


# ---------------------------------------------------------------- 1. 表结构
def section_1() -> None:
    print("\n1) 表结构：experience_item 有 domain 列（且建表 SQL 里也有）")
    from app.core.database import query

    cols = {list(c.values())[0]: c for c in query("SHOW COLUMNS FROM experience_item")}
    check_true("domain 列存在", "domain" in cols)
    check("domain 是 NOT NULL", (cols.get("domain") or {}).get("Null"), "NO")
    keys = {list(k.values())[2] for k in query("SHOW INDEX FROM experience_item")}
    check_true("有 idx_domain 索引（匹配按域过滤要走它）", "idx_domain" in keys)

    ddl = (ROOT / "scripts" / "init_db.sql").read_text(encoding="utf-8")
    block = ddl.split("CREATE TABLE IF NOT EXISTS experience_item", 1)[-1].split(";", 1)[0]
    check_true("建表 SQL 里带 domain（新库不必再迁移）", "domain VARCHAR(30) NOT NULL" in block)
    check_true("建表 SQL 里带 idx_domain", "idx_domain" in block)


# ---------------------------------------------------------------- 2. 落域
def section_2() -> None:
    print("\n2) 写入时落域（两条路：显式传 / 从 source.project_id 反查）")
    from app.services import experience_service as ES
    from app.services.experience_service import save_experience

    made: list[int] = []

    def mk(content: str, **kw) -> int | None:
        i = save_experience({"exp_type": "编写经验", "chapter_type": CHAPTER,
                             "content": content, **kw})
        if i:
            made.append(i)
        return i

    try:
        # (a) 显式传域
        a = mk(f"{MARK} 显式传域")
        check_true("显式传域：入库成功", a)
        # (b) 从 source.project_id 反查（三个提炼入口走的都是这条）
        b = mk(f"{MARK} 反查来源项目", source={"project_id": 4, "kind": "edit_diff"})
        check_true("反查来源项目：入库成功", b)
        # (c) 同域同内容 → 去重
        c = mk(f"{MARK} 显式传域")
        check("同域同内容被去重", c, None)
        # (d) 换一个域、同样内容 → **不算重复**（各域是各自的经验库）
        d = save_experience({"exp_type": "编写经验", "chapter_type": CHAPTER,
                             "content": f"{MARK} 显式传域"}, OTHER_DOMAIN)
        if d:
            made.append(d)
        check_true("另一个域的同内容条目不重复（各域独立）", d)

        from app.core.database import query

        got = {r["id"]: r["domain"] for r in
               query("SELECT id, domain FROM experience_item WHERE id IN (%s, %s, %s)"
                     % (a, b, d))}
        check("显式传域落对", got.get(a), "lowalt")
        check("反查来源项目落对（项目 4 是低空）", got.get(b), "lowalt")
        check("另一域落对", got.get(d), OTHER_DOMAIN)
    finally:
        _cleanup(made)


# ---------------------------------------------------------------- 3. 按域匹配
def section_3() -> None:
    print("\n3) 按域匹配：跨域不串")
    from app.services.experience_service import match_experiences, save_experience

    made: list[int] = []
    try:
        ia = save_experience({"exp_type": "编写经验", "chapter_type": CHAPTER,
                              "content": f"{MARK} A 域经验"}, "lowalt")
        ib = save_experience({"exp_type": "纠偏规则", "chapter_type": CHAPTER,
                              "content": f"{MARK} B 域经验"}, OTHER_DOMAIN)
        made += [i for i in (ia, ib) if i]
        check_true("两条测试经验都已入库", len(made) == 2)

        a = match_experiences(CHAPTER, [], "lowalt")
        b = match_experiences(CHAPTER, [], OTHER_DOMAIN)
        check("低空域只拿到自己的那条",
              [x["content"] for x in a], [f"{MARK} A 域经验"])
        check("另一域只拿到自己的那条",
              [x["content"] for x in b], [f"{MARK} B 域经验"])
        # A 域的经验**绝不能**出现在 B 域的结果里——这就是这次修的洞
        check_true("A 域经验不出现在 B 域",
                   all(MARK + " A 域经验" != x["content"] for x in b))
        # 不传域是"盘点"口径（读全库），正常匹配务必传
        both = [x["content"] for x in match_experiences(CHAPTER, [])]
        check_true("不传域时才读全库（盘点用）",
                   f"{MARK} A 域经验" in both and f"{MARK} B 域经验" in both)
    finally:
        _cleanup(made)


# ---------------------------------------------------------------- 4. 调用点
def section_4() -> None:
    print("\n4) 五个匹配调用点都传了域；对话工具按域过滤")
    files = ["app/agents/chat_agent.py", "app/agents/compile_graph.py",
             "app/services/compile_service.py", "app/services/writer_service.py"]
    for rel in files:
        src = (ROOT / "backend" / rel).read_text(encoding="utf-8")
        calls, ok_calls = 0, 0
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("experiences=match_experiences(") or \
               s.startswith("experiences = match_experiences("):
                calls += 1
                # 三参调用：位置参数 + 域
                if s.rstrip(",)") .count(",") >= 2:
                    ok_calls += 1
        check_true(f"{rel}: {calls} 处调用都带域", calls > 0 and calls == ok_calls)

    from app.agents import chat_agent as CA

    tool_src = inspect.getsource(CA._tool_search_experiences)
    check_true("search_experiences 工具按域过滤", "domain = %s" in tool_src)
    check_true("search_experiences 工具从项目取域", "domain_of_project(project_id)" in tool_src)
    check_true("工具派发处传了 project_id",
               "_tool_search_experiences(project_id," in inspect.getsource(CA))


# ---------------------------------------------------------------- 5. 真库
def section_5() -> None:
    print("\n5) 真库：项目 4（低空）的经验只在低空域里匹配得到")
    from app.core.database import query
    from app.core.domains import domain_of_project
    from app.services.experience_service import match_experiences

    dom = domain_of_project(4)
    check("项目 4 是低空域", dom, "lowalt")
    rows = query("SELECT id, chapter_type, domain FROM experience_item")
    check_true("库里每条经验都有域（迁移没漏行）",
               all((r["domain"] or "").strip() for r in rows))
    # 项目 4 现有的两条都是「响应函」，按标题匹配得到
    got = match_experiences("响应函", [], dom)
    check_true("项目 4 下「响应函」能匹配到自己的经验",
               any("响应函" == r["chapter_type"] for r in got))
    # 换个域就一条都拿不到（现有经验全属低空）
    check("换到别的域拿不到低空的响应函经验", match_experiences("响应函", [], OTHER_DOMAIN), [])


def _cleanup(ids: list[int]) -> None:
    """删掉本次插的测试经验（按标记兜底，防止 id 漏记）。"""
    from app.core.database import execute
    if ids:
        execute("DELETE FROM experience_item WHERE id IN (%s)"
                % ",".join(str(int(i)) for i in ids))
    execute("DELETE FROM experience_item WHERE content LIKE %s", (f"%{MARK}%",))


def main() -> int:
    try:
        section_1()
        section_2()
        section_3()
        section_4()
        section_5()
    finally:
        _cleanup([])
        from app.core.database import query

        left = query("SELECT COUNT(*) c FROM experience_item WHERE content LIKE %s",
                     (f"%{MARK}%",))
        check("测试数据已清干净", left[0]["c"], 0)
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

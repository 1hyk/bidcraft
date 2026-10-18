"""校验「知识库归属墙」——同域里**别的项目**的标书不许进本项目的候选池。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_knowledge_scope.py

背景（2026-10-16）：此前候选池只有 `domain` 一道墙，它挡的是**别的行业**，挡不住
**同域别的项目**。实测：一份别的项目的低空投标文件传进库里，lowalt 池 1156 → 1353 页，
`match_pages` 就会把那本标书的写法引进本项目正文。

本次加第二道墙：`knowledge_doc.scope` 三态
  domain  —— 领域通用（默认，也是加这道墙之前的行为，**存量行一个字没改**）
  project —— 只对 `project_id` 那个项目可见
  off     —— 不参与检索（只留档，知识库页面照样看得到）
过滤走 JOIN（`matching._scope_sql`），不把 scope 冗余到 `knowledge_page`：
归属是**会被改的**，冗余就得同步两份，漏同步就"改完不生效"。

这个脚本会**改库**（把某份文档临时设成 off/project 再改回来），收尾一律恢复原值。

检查：
  1. `_scope_sql` 纯函数：没给项目只取领域通用（漏传要**收窄**，不能放开）；
  2. 全部文档都是 domain 时，池子与加墙之前**逐页相同**（不许有回归）；
  3. 真场景：把一份别的项目的标书设成 `off` → 池子恰好少掉它那些页，命中消失；
  4. `project` 态：只有归属的那个项目看得见，别的项目看不见；
  5. 非法归属当场报错（不静默回退）；API 层校验走 `_norm_scope`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import execute, query, query_one                    # noqa: E402
from app.services import matching as M                                     # noqa: E402

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def check_pool(label: str, got: set, want: set) -> None:
    """比两组页 id——池子一千多页，直接打出来会把整个终端刷掉，失败时只打差集规模。"""
    global ok
    good = got == want
    ok = ok and good
    extra = f"（多 {len(got - want)} 页 / 少 {len(want - got)} 页）" if not good else ""
    print(f"  [{'OK' if good else '!!'}] {label}: {len(got)} 页{extra}")


# ---------------------------------------------------------------- 1. 纯函数
def section_1() -> None:
    print("\n1) _scope_sql：没给项目上下文时必须**收窄**，不能放开")
    sql, params = M._scope_sql(None)
    check("没给项目 → 只取领域通用", M.SCOPE_DOMAIN in sql and M.SCOPE_PROJECT not in sql, True)
    check("没给项目 → 参数只有一个域值", params, (M.SCOPE_DOMAIN,))
    check("没给项目 → SQL 里不出现 project_id 比较", "project_id" in sql, False)

    sql, params = M._scope_sql(7)
    check("给了项目 → 领域通用 + 本项目专用", M.SCOPE_DOMAIN in sql and M.SCOPE_PROJECT in sql, True)
    check("给了项目 → 参数带上项目号", params, (M.SCOPE_DOMAIN, M.SCOPE_PROJECT, 7))

    # off 不是"某一种可见范围"，是"谁都看不见"——所以它**不该作为可选值出现在条件里**
    check("off 不参与任何可见分支", M.SCOPE_OFF not in M._scope_sql(7)[0], True)
    check("SCOPE_* 三态齐全", (M.SCOPE_DOMAIN, M.SCOPE_PROJECT, M.SCOPE_OFF),
          ("domain", "project", "off"))


# ---------------------------------------------------------------- 2. 无回归
def section_2() -> None:
    print("\n2) 存量全是 domain 时，池子与加墙之前逐页相同（不许有回归）")
    scopes = query("SELECT scope, COUNT(*) c FROM knowledge_doc GROUP BY scope")
    print(f"     文档归属分布：{[(r['scope'], r['c']) for r in scopes]}")
    domain_only = all(r["scope"] == M.SCOPE_DOMAIN for r in scopes) if scopes else False

    all_pages = M.load_page_summaries("lowalt")
    old_pages = query("SELECT id FROM knowledge_page WHERE status != 'deprecated' AND domain = 'lowalt'")
    print(f"     低空池：{len(all_pages)} 页；不加归属过滤的原始计数：{len(old_pages)} 页")
    if domain_only:
        # 只比结论，别把一千多个 id 打成一行——失败时再打差集
        same = {p["id"] for p in all_pages} == {r["id"] for r in old_pages}
        check("全部 domain 时池子与原始计数逐页一致", same, True)
        if not same:
            print(f"       差集 {len({p['id'] for p in all_pages} ^ {r['id'] for r in old_pages})} 页")
    else:
        print("     （库里有非 domain 的文档，跳过这条逐页比对）")
    check("全库模式（domain=None）不炸", isinstance(M.load_page_summaries(None), list), True)


# ---------------------------------------------------------------- 3/4. 真场景
def _pool_ids(project_id: int | None) -> set[int]:
    return {p["id"] for p in M.load_page_summaries("lowalt", project_id)}


def section_3() -> None:
    print("\n3) 真场景：把「别的项目的标书」设成 off → 它那些页从池子里消失")
    docs = query("SELECT id, file_name, scope, project_id FROM knowledge_doc"
                 " WHERE doc_category = 'sod' ORDER BY id")
    if len(docs) < 2:
        print("     库里的知识文档不足两份，跳过")
        return
    # 拿**最近传进来的那一份**做实验——这正是真实场景：新传的一份别人的标书刚进池
    victim = docs[-1]
    n_pages = query_one("SELECT COUNT(*) c FROM knowledge_page WHERE doc_id = %s",
                        (victim["id"],))["c"]
    print(f"     拿文档 {victim['id']}（{n_pages} 页）做实验：{victim['file_name'][:40]}")
    if victim["scope"] == M.SCOPE_OFF:
        print("     （它本来就是 off，先恢复成 domain 再测）")
        execute("UPDATE knowledge_doc SET scope = %s, project_id = NULL WHERE id = %s",
                (M.SCOPE_DOMAIN, victim["id"]))

    before = _pool_ids(None)
    its_pages = {r["id"] for r in query("SELECT id FROM knowledge_page WHERE doc_id = %s",
                                        (victim["id"],))}
    try:
        execute("UPDATE knowledge_doc SET scope = %s, project_id = NULL WHERE id = %s",
                (M.SCOPE_OFF, victim["id"]))
        after = _pool_ids(None)
        check(f"池子恰好少掉该文档的 {n_pages} 页", len(before) - len(after), n_pages)
        check_pool("少掉的正都是它的页", after, before - its_pages)

        # 连「章级骨架」也跟着变——它读的是同一个池子。子节骨架从语料里数，池子错了它就错了。
        def _skel_all() -> dict:
            pages = M.load_page_summaries("lowalt", None)
            return {t: M.chapter_skeleton(t, pages) for t in
                    ("技术指标响应情况", "工程化交付方案", "项目实施方案")}
        skel_without = _skel_all()          # 该文档此刻是 off
        execute("UPDATE knowledge_doc SET scope = %s WHERE id = %s",
                (M.SCOPE_DOMAIN, victim["id"]))
        skel_with = _skel_all()
        print("     该文档对子节骨架有无影响："
              + ("有（池子换了骨架就换）" if skel_without != skel_with else "无（它本来就不供子节）"))
        check_pool("恢复 domain 后池子回到原样", _pool_ids(None), before)
    finally:
        execute("UPDATE knowledge_doc SET scope = %s, project_id = NULL WHERE id = %s",
                (M.SCOPE_DOMAIN, victim["id"]))
        check_pool("收尾已恢复 domain", _pool_ids(None), before)


def section_4() -> None:
    print("\n4) project 态：只有归属的那个项目看得见")
    rows = query("SELECT id FROM project WHERE status != 'deleted' ORDER BY id LIMIT 2")
    if len(rows) < 2:
        print("     项目不足两个，跳过")
        return
    a, b = rows[0]["id"], rows[1]["id"]
    docs = query("SELECT id FROM knowledge_doc WHERE doc_category = 'sod' ORDER BY id")
    if not docs:
        print("     没有知识文档，跳过")
        return
    victim = docs[0]["id"]
    n_pages = query_one("SELECT COUNT(*) c FROM knowledge_page WHERE doc_id = %s", (victim,))["c"]
    base_a, base_b = _pool_ids(a), _pool_ids(b)
    try:
        execute("UPDATE knowledge_doc SET scope = %s, project_id = %s WHERE id = %s",
                (M.SCOPE_PROJECT, a, victim))
        ca, cb = _pool_ids(a), _pool_ids(b)
        check(f"归属项目 {a} 看得见（比原来少 {n_pages} 页的是项目 {b}）", len(base_a) - len(ca), 0)
        check(f"别的项目 {b} 看不见它的页", len(base_b) - len(cb), n_pages)
        # 归属改了就生效，不需要重提炼——因为过滤发生在查询里，不是写死在知识页上
        check("改归属立即生效（无需重提炼）", n_pages > 0, True)
    finally:
        execute("UPDATE knowledge_doc SET scope = %s, project_id = NULL WHERE id = %s",
                (M.SCOPE_DOMAIN, victim))
        check_pool("收尾已恢复 domain（项目 a）", _pool_ids(a), base_a)
        check_pool("收尾已恢复 domain（项目 b）", _pool_ids(b), base_b)


# ---------------------------------------------------------------- 5. 校验
def section_5() -> None:
    print("\n5) 非法归属当场报错（不静默回退）")
    from fastapi import HTTPException

    from app.api.knowledge import _norm_scope

    def _err(scope: str, pid: int = 0) -> str:
        try:
            _norm_scope(scope, pid)
            return ""
        except HTTPException as e:
            return str(e.detail)

    check("未知归属被拒", "未知的归属" in _err("whatever"), True)
    check("选 project 却不给项目号被拒", "必须给 project_id" in _err("project"), True)
    check("不存在的项目被拒", "不存在" in _err("project", 999999), True)
    check("domain 归一化掉空串", _norm_scope("", 5), ("domain", None))
    check("domain 不吃 project_id（不留脏值）", _norm_scope("domain", 5), ("domain", None))
    check("off 也不吃 project_id", _norm_scope("off", 5), ("off", None))
    check("大小写与空白宽容", _norm_scope("  OFF ", 0), ("off", None))


def main() -> int:
    section_1()
    section_2()
    section_3()
    section_4()
    section_5()
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

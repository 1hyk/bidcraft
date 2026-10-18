"""校验「第一级过滤的自我修正」：泛滥的专业标签自动退出过滤（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_tag_filter.py

背景：三级漏斗的第一级是「待写章节路径里撞到的专业词 ∩ 知识页 tags」，用来缩小候选池。
可这个词表是**手写**的，而手写词表迟早会遇上某个词在语料里泛滥——实测 lowalt 域 1156 页
里「实飞测试」覆盖 91.1%，凡是路径里只撞到它一个词的章节，"过滤"与不过滤完全等价。
系统此前**没有任何机制能发现这件事**，只能靠人数页。

本文件验的就是那条机制（`matching.generic_tags` / `matching.filter_pool`）：
覆盖率越过 GENERIC_TAG_RATIO 的词自动退出过滤，词表里一个词都没删。

检查四件事：
  1. 覆盖率算法本身：边界、小池子、词表外标签、重复标签、字段名可换；
  2. filter_pool 的四种情形：无专业词 / 只撞泛滥词 / 撞到真词（收窄）/ 真词零命中，
     前三种都必须**原池返回**——规则不该把任何章节饿死；
  3. **真实语料**：lowalt 域当前被判为泛滥的是哪几个词（打印出来，可人工核对）；
  4. **前后对照**：项目 4 每个章节节点在规则前后拿到的候选池大小，看它到底改变了什么。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.domains import tags_of                                  # noqa: E402
from app.services import matching as M                                # noqa: E402

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def pages(n: int, tag_iter) -> list[dict]:
    """造 n 页；第 i 页的标签由 tag_iter(i) 给。"""
    return [{"id": i, "title": f"p{i}", "tags": list(tag_iter(i))} for i in range(n)]


# ---------------------------------------------------------------- 1. 覆盖率算法
def section_1() -> None:
    print("\n1) 覆盖率算法（generic_tags）")
    V = ("甲", "乙", "丙")

    # 50 页里 47 页贴「甲」→ 94%，过线
    p = pages(50, lambda i: ["甲"] if i < 47 else ["乙"])
    check("94% 过线被剔", M.generic_tags(p, V), {"甲"})

    # 25/50 = 50%，恰好压在线上——边界取 >=，所以剔（余下 25 页留空，别用另一个词陪衬，
    # 否则陪衬的那个也正好 50%，两个一起被剔，测不出边界）
    p = pages(50, lambda i: ["甲"] if i < 25 else [])
    check("恰好 50% 被剔（边界取 >=）", M.generic_tags(p, V), {"甲"})

    # 24/50 = 48%，差一点，留
    p = pages(50, lambda i: ["甲"] if i < 24 else [])
    check("48% 不到线，留着", M.generic_tags(p, V), set())

    # 小池子不判：29 < GENERIC_TAG_MIN_POOL(30)，哪怕 28/29 = 97%
    p = pages(29, lambda i: ["甲"] if i < 28 else ["乙"])
    check(f"{len(p)} 页（< {M.GENERIC_TAG_MIN_POOL}）一个都不判", M.generic_tags(p, V), set())

    # 词表外的标签不进统计，也不影响判决
    p = pages(50, lambda i: (["甲"] if i < 25 else []) + (["编外词"] if i < 40 else []))
    check("词表外的标签不参与", M.generic_tags(p, V), {"甲"})

    # 同一页把同一个词贴两遍，按**页**算不按**次**算：
    # 「甲」贴 24 页（每页两遍）＝ 48%，「乙」贴 26 页 ＝ 52%。若误按出现次数统计，
    # 甲会算成 48/74 = 65% 而被误剔。期望只剔乙。
    p = pages(50, lambda i: ["甲", "甲"] if i < 24 else ["乙"])
    check("同页重复标签只算一次（按页不按次）", M.generic_tags(p, V), {"乙"})

    # 字段名可换（工法条目用的是 applies_to）
    p = [{"id": i, "applies_to": ["甲"] if i < 40 else ["乙"]} for i in range(50)]
    check("key=applies_to 生效", M.generic_tags(p, V, key="applies_to"), {"甲"})
    check("默认 key=tags 时读不到 applies_to", M.generic_tags(p, V), set())

    # 空池 / 空词表
    check("空池", M.generic_tags([], V), set())
    check("空词表", M.generic_tags(pages(50, lambda i: ["甲"]), ()), set())


# ---------------------------------------------------------------- 2. filter_pool
def section_2() -> None:
    print("\n2) 第一级过滤（filter_pool）")
    V = ("实飞测试", "监视能力")
    # 100 页：60 页贴泛滥词「实飞测试」，其中 5 页同时贴「监视能力」；另 3 页只贴「监视能力」
    def tags_of_page(i):
        if i < 5:
            return ["实飞测试", "监视能力"]
        if i < 60:
            return ["实飞测试"]
        if i < 63:
            return ["监视能力"]
        return []
    p = pages(100, tags_of_page)

    check("先确认实飞测试确实被判泛滥", M.generic_tags(p, V), {"实飞测试"})

    check("路径没撞到专业词 → 原池", len(M.filter_pool(p, ["工程概况"], V)), 100)
    check("路径只撞到泛滥词 → 原池（不把章节饿死）",
          len(M.filter_pool(p, ["实飞测试专项"], V)), 100)
    check("路径撞到真词 → 收窄到真词那批（5 + 3）",
          len(M.filter_pool(p, ["实飞测试监测", "监视能力"], V)), 8)
    check("路径撞到两个真词时取并集",
          len(M.filter_pool(p, ["监视能力", "不存在的词"], V)), 8)

    # 真词一个都没命中 → 退回原池（标签打得稀疏是常态）
    q = pages(50, lambda i: ["实飞测试"] if i < 40 else [])
    check("真词零命中 → 退回原池",
          len(M.filter_pool(q, ["监视能力"], ("实飞测试", "监视能力"))), 50)

    # 规则生效的对照：同一节点，旧写法（不剔泛滥词）会拿到 63 页（5 + 55 + 3）
    old = [it for it in p if {"实飞测试", "监视能力"} & set(it["tags"])]
    check("旧写法（不剔泛滥词）拿 63 页，新写法拿 8 页", (len(old), 8), (63, 8))


# ---------------------------------------------------------------- 3 & 4. 真实语料
def section_3_4() -> bool:
    """连库部分。返回是否跑成功。"""
    print("\n3) 真实语料：lowalt 域当前被判为泛滥的词")
    try:
        from app.core.database import query
        from app.core.database import query_one  # noqa: F401  仅验证可连库
        pages_all = M.load_page_summaries("lowalt")
    except Exception as e:  # noqa: BLE001 没库就跳过，前面两节仍然作数
        print(f"  [--] 连不上库，跳过真实语料检查：{type(e).__name__}: {e}")
        return False

    if not pages_all:
        print("  [--] lowalt 域没有知识页，跳过")
        return False

    V = tags_of("lowalt")
    gen = M.generic_tags(pages_all, V)
    print(f"     池子 {len(pages_all)} 页，词表 {len(V)} 个词")
    cov = {}
    for t in V:
        cov[t] = sum(1 for p in pages_all if t in set(p["tags"]))
    for t, c in sorted(cov.items(), key=lambda kv: -kv[1]):
        mark = "  ← 泛滥" if t in gen else ""
        print(f"       {t:<12} {c:>5} 页  {c / len(pages_all) * 100:5.1f}%{mark}")

    check("「实飞测试」被判为泛滥", "实飞测试" in gen, True)
    check("「任务载荷」(2.5%) 不受影响", "任务载荷" in gen, False)

    print("\n4) 前后对照：项目 4 各章节节点的候选池（旧写法 vs 新写法）")
    rows = query("SELECT id, title, parent_id FROM chapter_node"
                 " WHERE project_id = 4 ORDER BY sort_order, id")
    if not rows:
        print("  [--] 项目 4 没有章节节点，跳过")
        return True

    by_id = {r["id"]: r for r in rows}

    def path_of(r) -> list[str]:
        chain, cur, guard = [], r, 0
        while cur is not None and guard < 20:
            chain.append(cur["title"] or "")
            cur = by_id.get(cur.get("parent_id"))
            guard += 1
        return list(reversed(chain))[:-1]  # 去掉自身，留祖先链

    unchanged = widened = narrowed = 0
    for r in rows:
        path = path_of(r)
        node = [*path, r["title"] or ""]
        new = len(M.filter_pool(pages_all, node, V))
        profs = M._path_professions(node, V)
        old_pool = [p for p in pages_all if profs & set(p["tags"])] if profs else pages_all
        old = len(old_pool) or len(pages_all)

        if new == old:
            unchanged += 1
        elif new > old:
            widened += 1
        else:
            narrowed += 1
        if new != old:
            arrow = "宽" if new > old else "窄"
            print(f"     {r['title'][:34]:<36} {old:>5} → {new:>5}  ({arrow})")

    print(f"     合计 {len(rows)} 章：不变 {unchanged}、变宽 {widened}、变窄 {narrowed}")
    check("规则确实改变了候选池（不是空转）", (widened + narrowed) > 0, True)
    check("没有章节被饿死（池子非空）",
          all(M.filter_pool(pages_all, [r["title"] or ""], V) for r in rows), True)
    return True


def main() -> int:
    section_1()
    section_2()
    # 第 3/4 节要连库；load_page_summaries 会顺带打印「哪几个词被判泛滥」的那行日志
    section_3_4()
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

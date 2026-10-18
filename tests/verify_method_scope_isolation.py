"""校验工法（`method_index`）这条线与知识页**共用同一套两道墙**（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_method_scope_isolation.py

为什么单独验它（2026-10-16）：工法文档在知识库列表里带着「归属」徽标、点一下能改，
但 `catalog()` 此前**只过滤 doc_category/status/domain，根本不看 scope**——同一份
`knowledge_doc.scope`，标书文档改了立刻生效，工法文档改了**静默无效**。
这就是"界面上给了个开关，后端不认"那一类：用户以为已经关掉了，正文里照样在用。

检查：
  1. 领域墙：别的领域的工法在 lowalt 项目里取不到；
  2. 归属墙（生成路径）：`off` 取不到、别项目的 `project` 取不到、本项目的 `project` 取得到；
  3. **安全默认**：不传 project_id **只取领域通用**（宁可少料，不可串味）；
  4. 列表例外：`library_view=True` 看得见 `off`（否则用户找不到改回来的入口）；
  5. **接线**：`match_methods()` 真的把 project_id 传下去了（只 stub 掉选料/取文，
     不碰模型）——这条是今天那个 bug 的回归检查。

自造数据、跑完自清（临时行的 file_name 带 `__tmp_verify__` 标记），不碰真实语料。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import execute, query                              # noqa: E402
from app.services import method_index as mi                               # noqa: E402

MARK = "__tmp_verify__"
ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def _seed() -> list[dict]:
    """造 4 份工法文档，覆盖两道墙的每种组合。返回 [{id, scope, project, domain}]。"""
    spec = [
        ("领域通用", "lowalt", "domain", None),          # 任何 lowalt 项目都该看到
        ("本项目专用", "lowalt", "project", 4),           # 只有项目 4 该看到
        ("别项目专用", "lowalt", "project", 999),         # 项目 4 不该看到
        ("关掉的", "lowalt", "off", None),               # 谁都不该看到
        ("别的领域", "railway", "domain", None),          # lowalt 项目不该看到
    ]
    out = []
    for tag, dom, scope, pid in spec:
        did = execute(
            "INSERT INTO knowledge_doc (file_name, file_path, doc_category, domain, scope,"
            " project_id, status, parsed_path) VALUES (%s, %s, 'method', %s, %s, %s, 'parsed', %s)",
            (f"{MARK}{tag}.docx", f"(fake){MARK}/{tag}.docx", dom, scope, pid, f"(fake){MARK}"),
        )
        execute("INSERT INTO method_index (doc_id, name, summary, applies_to) VALUES (%s, %s, %s, %s)",
                (did, f"{MARK}{tag}", "概况", "适用范围"))
        out.append({"id": did, "tag": tag})
    return out


def _cleanup(ids: list[int]) -> None:
    for did in ids:
        execute("DELETE FROM method_index WHERE doc_id = %s", (did,))
        execute("DELETE FROM knowledge_doc WHERE id = %s", (did,))


def _tags(cat: list[dict]) -> set[str]:
    return {c["name"] for c in cat}


def main() -> int:
    ids = _seed()
    try:
        all_tags = {f"{MARK}{t}" for t in ("领域通用", "本项目专用", "别项目专用", "关掉的", "别的领域")}

        # ---- 1/2/3. 生成路径：两道墙 + 安全默认 ----
        print("1) 生成路径 catalog('lowalt', project_id=4)：领域墙 + 归属墙")
        got = _tags(mi.catalog("lowalt", 4))
        check("取到「领域通用」", f"{MARK}领域通用" in got, True)
        check("取到「本项目专用」", f"{MARK}本项目专用" in got, True)
        check("**取不到** off 的", f"{MARK}关掉的" in got, False)
        check("**取不到**别项目的 project", f"{MARK}别项目专用" in got, False)
        check("**取不到**别领域的", f"{MARK}别的领域" in got, False)

        print("\n2) 安全默认 catalog('lowalt')：不传 project 只取领域通用")
        got = _tags(mi.catalog("lowalt"))
        check("取到「领域通用」", f"{MARK}领域通用" in got, True)
        check("project 类的（不管哪个项目）一律不取",
              {f"{MARK}本项目专用", f"{MARK}别项目专用"} & got, set())
        check("off 一律不取", f"{MARK}关掉的" in got, False)

        print("\n3) 列表例外 catalog('lowalt', library_view=True)：库里要看得见 off")
        got = _tags(mi.catalog("lowalt", library_view=True))
        check("看得见 off 的（否则用户找不到改回来的入口）", f"{MARK}关掉的" in got, True)
        check("看得见别项目的 project", f"{MARK}别项目专用" in got, True)
        check("但**仍然**看不到别领域的", f"{MARK}别的领域" in got, False)
        check("列表里只看得到 lowalt 的这 4 条（领域墙在列表页也生效）",
              got & all_tags, all_tags - {f"{MARK}别的领域"})

        # ---- 4. 接线：match_methods 真的把 project_id 传下去 ----
        print("\n4) 接线：match_methods(need, domain, project_id)")
        # 两次调用分别记账：一次带 project_id、一次漏传（模拟漏改的调用方）。
        seen: list[set[str]] = []
        real_select, real_load = mi.select, mi.load
        mi.select = lambda cat, need, **k: (seen.append(_tags(cat)), [])[1]
        mi.load = lambda cat, doc_ids: []
        try:
            mi.match_methods("编写测试验收章节", "lowalt", 4)
            mi.match_methods("编写测试验收章节", "lowalt")          # 漏传的调用方
        finally:
            mi.select, mi.load = real_select, real_load
        with_pid, no_pid = seen[0], seen[1]
        # ⚠ **这一条是唯一能抓到"漏传 project_id"的检查**：光看"off 进没进候选"抓不到
        # ——漏传时 catalog 退回安全默认（只取领域通用），off 和别项目的一样进不来，检查照样绿。
        # 只有"本项目专用的工法**在**候选里"能区分：漏传它就消失了。（2026-10-16 实测确认）
        check("传 project_id=4：「本项目专用」**在**候选里（漏传就取不到它）",
              f"{MARK}本项目专用" in with_pid, True)
        check("传 project_id=4：off 不进候选", f"{MARK}关掉的" in with_pid, False)
        check("传 project_id=4：别项目的 project 不进候选", f"{MARK}别项目专用" in with_pid, False)
        check("漏传 project_id：退化成只取领域通用（少料，不串味）",
              {f"{MARK}领域通用"} <= no_pid
              and not ({f"{MARK}本项目专用", f"{MARK}关掉的", f"{MARK}别项目专用"} & no_pid), True)
        check("两次取到的候选确实不同（接线真的接通了）", with_pid == no_pid, False)
    finally:
        _cleanup([r["id"] for r in ids])
        left = query("SELECT COUNT(*) c FROM knowledge_doc WHERE file_name LIKE %s", (f"{MARK}%",))[0]["c"]
        orphan = query("SELECT COUNT(*) c FROM method_index WHERE name LIKE %s", (f"{MARK}%",))[0]["c"]
        print(f"\n（已清理临时行：knowledge_doc 剩 {left} 行、method_index 剩 {orphan} 行）")

    print("\n结论：" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

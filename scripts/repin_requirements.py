"""把招标要求从顶层章下沉到真正对应的子节（存量数据回填，手工跑）。

背景：模型抽要求时爱填顶层章名——实测项目 4 的 8 条技术要求全写成「四、技术方案」，
而招标规定目录里明明有「第三节 监视能力全量验证技术方案」这种只差前后缀的子节。
父章一口气吃下全部，底下各子节写正文时一条要求都拿不到。

抽取侧已经修好（`tender_agent._clean_requirements` 里调 `chapter_title.descend`），
但那只对**将来**的抽取生效——`suggested_chapter` 是抽的那一刻写进库的，
所以存量项目要跑这个脚本刷一遍。

判据见 `chapter_title.descend`：只在原来挂的那一章的子树里找，包含优先、措辞变体次之
（阈值 0.65），找不到就原样不动——宁可不给，也不把泛称硬塞给某个子节。

默认 **dry-run**，只打印将要改动的行；加 `--apply` 才写库。

用法：
    D:\\bidcraft\\.venv\\Scripts\\python.exe scripts/repin_requirements.py [project_id] [--apply]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core import chapter_title as ct                      # noqa: E402
from app.core.database import execute, query, query_one        # noqa: E402


def load_tree(project_id: int) -> dict:
    row = query_one("SELECT toc_hint FROM project WHERE id = %s", (project_id,))
    hint = (row or {}).get("toc_hint")
    if isinstance(hint, str):
        hint = json.loads(hint)
    return hint if isinstance(hint, dict) and hint.get("chapters") else {"found": False, "chapters": []}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    project_id = int(args[0]) if args else 4

    tree = load_tree(project_id)
    titles = ct.toc_titles(tree)
    if not titles:
        print(f"项目 {project_id} 没有规定目录（toc_hint），无从下沉。")
        return 1
    print(f"项目 {project_id}：规定目录 {len(tree['chapters'])} 章 / {len(titles)} 个章名")

    rows = query("SELECT id, category, title, suggested_chapter FROM tender_requirement"
                 " WHERE project_id = %s ORDER BY id", (project_id,))
    print(f"招标要求 {len(rows)} 条\n")

    changes = []
    for r in rows:
        old = (r["suggested_chapter"] or "").strip()
        if not old:
            continue
        new = ct.descend(r["title"], old, tree)
        if new != old:
            changes.append((r["id"], r["title"], old, new))

    if not changes:
        print("没有需要下沉的（存量数据已经是子节粒度）。")
        return 0

    for rid, title, old, new in changes:
        print(f"  #{rid:>3} {title}")
        print(f"        {old}  →  {new}")

    if not apply:
        print(f"\n共 {len(changes)} 条待改（dry-run，未写库）。加 --apply 执行。")
        return 0

    for rid, _title, _old, new in changes:
        execute("UPDATE tender_requirement SET suggested_chapter = %s WHERE id = %s", (new, rid))
    print(f"\n已写库 {len(changes)} 条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

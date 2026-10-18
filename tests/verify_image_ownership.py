"""校验「同一张图全书只由一章配」（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_image_ownership.py

背景（2026-10-16，用户原话「确实不能出现两张一样的图」）：知识页按章**独立**匹配，容器章
与它的子章标题/路径天然相似，于是常匹配到同一批页——项目 4 里 `#285 工程化交付方案` 与
子章 `#286 第一节 交付批次规划` 的 `_page_ids` 一字不差，两章的【建议图片】里于是出现同一个
图名「跨区异地并行作业流水线横道图」，模型各画一张，成稿里就是两张一样的图。

归属规则（用户拍板）：**层级最深者得**（容器章让位给子章），同层按目录序。
实现：`app/services/image_ownership.py`——纯函数 `resolve_image_skips` 定规则，
`project_image_skips` 只负责把库里的目录 + `_page_ids` + 图名喂给它。

**为什么不按"生成时谁先抢到"实现**：批量/重写路径是 `ThreadPoolExecutor(10)` 并发
（`writer_service.start_batch` / `rewrite_chapters`），抢图结果不确定——同一份目录重跑两次
可能换个章拿到图。所以归属在开写前按目录结构一次算定。

检查：
  1. 纯函数：容器让位、同层按目录序、单章点名不裁决、父不在本批也不丢节点；
  2. 渲染：让出的图**不进**【建议图片】、另起一段说清归属并禁止重复，别的图不受影响；
  3. 接线：`_chapter_context` 真的把归属表装进 ctx（285 让位、286 不让位）；
  4. 真实数据（项目 4）：每个"被多章点名"的图名，恰好只有一章不让位；
  5. 成稿本身（项目 4 的存稿）：没有两张一样的图。
"""
from __future__ import annotations

import json
import re
import sys
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.writer_agent import _build_context                       # noqa: E402
from app.core.database import query                                      # noqa: E402
from app.services import writer_service as WS                            # noqa: E402
from app.services.image_ownership import (                               # noqa: E402
    image_name,
    project_image_skips,
    resolve_image_skips,
)

ok = True

CAP = "跨区异地并行作业流水线横道图"          # 就是项目 4 里撞车的那张
OTHER = "无人机实飞测试现场"


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


#: 夹具条目的 url。**必须带**：没有 url 的条目是"模型从正文描述编出来的空壳"（素材里没有
#: 这张图），已被 `_build_context` 挡在【建议图片】外——不带 url 的话本节所有"进建议"的
#: 断言全是假绿（2026-10-16 加空壳过滤时这几条当场翻红，就是这么发现的）。
#: 只判非空字符串，文件不必真在盘上。
_U = "/uploads/parsed/_verify_own/"


def _ctx(skip=None, images=None):
    """造一份最小知识页（不碰库、不调模型），只为了看【建议图片】那几段怎么渲染。"""
    return _build_context(
        {"title": "第二节 阶段性交付物清单"},
        [],
        [{"images": images if images is not None else
          [{"caption": CAP, "content": "杭州与大安并行作业的时间轴", "url": _U + "a.png"},
           {"caption": OTHER, "content": "户外场地实飞", "url": _U + "b.png"}]}],
        [],
        domain="lowalt",
        skip_images=skip,
    )


# ---------------------------------------------------------------- 1. 纯函数
def section_1() -> None:
    print("\n1) 归属规则（纯函数，不碰库）")
    nodes = [
        # 容器章和子章抢同一张图 —— 就是项目 4 的 285/286
        {"id": 1, "title": "工程化交付方案", "parent_id": None, "sort_order": 1,
         "captions": [CAP, "容器独有的图"]},
        {"id": 2, "title": "第一节 交付批次规划", "parent_id": 1, "sort_order": 0,
         "captions": [CAP]},
        # 同层两个叶子抢同一张图 —— 项目 4 的 287/292、289/290
        {"id": 4, "title": "先出现的叶子", "parent_id": 3, "sort_order": 0, "captions": [OTHER]},
        {"id": 5, "title": "后出现的叶子", "parent_id": 3, "sort_order": 1, "captions": [OTHER]},
        {"id": 3, "title": "另一个容器", "parent_id": None, "sort_order": 2, "captions": []},
        # 父节点不在本批（跨项目/脏数据）也不能把节点弄丢
        {"id": 6, "title": "孤儿章", "parent_id": 999, "sort_order": 9, "captions": ["孤儿图"]},
        # **专门用来把"层级最深"和"目录序最先"区分开**的一对：靠前的浅章 vs 靠后的深章
        # （先序永远把父排在子前，所以容器/子章那一组两种规则同解，钉不住这条规则——
        #   必须跨子树才分得开。扰动验过：去掉层级优先，下面这条就翻红。）
        {"id": 7, "title": "靠前的浅章", "parent_id": None, "sort_order": 0, "captions": ["跨子树图"]},
        {"id": 8, "title": "靠后的深章", "parent_id": 3, "sort_order": 0, "captions": ["跨子树图"]},
    ]
    s = resolve_image_skips(nodes)

    check("容器章让出图（归它更具体的子章）", s.get(1), {CAP: "第一节 交付批次规划"})
    check("子章不让位（它就是归属）", 2 in s, False)
    check("容器章独占的图不受影响（不做多余裁决）", "容器独有的图" in str(s.get(1, {})), False)
    check("同层撞车按目录序归先出现的那章", s.get(5), {OTHER: "先出现的叶子"})
    check("同层先出现的那章不让位", 4 in s, False)
    check("只有一章点名的图不裁决（本表里没有它）",
          "孤儿图" in str(s), False)
    check("父不在本批的节点没被丢掉（仍参与、且被算作根）",
          len(s), 3)
    # ⚠ 这条是唯一能把规则钉死的：目录序上 #7 在前、层级上 #8 更深，两者不给同一个答案。
    check("层级优先于目录序（前面出现过的浅章也要让位给更深的章）",
          (s.get(7), 8 in s), ({"跨子树图": "靠后的深章"}, False))


# ---------------------------------------------------------------- 2. 渲染
def section_2() -> None:
    print("\n2) 渲染：让出的图不进建议、另起一段说清归属")
    plain = _ctx()
    check("不传 skip 时两张图都在建议里",
          (f'- 图「{CAP}」' in plain, f'- 图「{OTHER}」' in plain), (True, True))
    check("（对照）不传 skip 时没有「不要再配」那一段",
          "【本章不要再配的图" in plain, False)

    one = _ctx(skip={CAP: "第一节 交付批次规划"})
    check("让出的图不再出现在【建议图片】里", f'- 图「{CAP}」' in one, False)
    check("另一张图照旧在建议里", f'- 图「{OTHER}」' in one, True)
    # 光删掉是不够的：本章正文自己常想画这张图，模型会照着自己画第二张一样的。
    check("另起一段说明归属章", "【本章不要再配的图" in one, True)
    check("那段里点名归属章", "已用于《第一节 交付批次规划》" in one, True)
    check("那段里明确禁止用占位顶上", "也不要用 [图片占位] 顶上" in one, True)

    # 唯一的图被让出时，【建议图片】整段不该留一个空壳（空列表比没有更让人困惑）
    empty = _ctx(skip={CAP: "X", OTHER: "Y"}, images=[{"caption": CAP, "url": _U + "a.png"}])
    check("图全被让出时不留空的【建议图片】段", "【本章建议使用的图片" in empty, False)
    check("图全被让出时仍留下归属说明", "【本章不要再配的图" in empty, True)


# ---------------------------------------------------------------- 3. 接线
def section_3() -> None:
    """`_chapter_context` 必须把归属表装进 ctx，否则 `_build_context` 收不到。

    把取材那一步换成空实现：这一节只验接线，不需要 LLM（`_parallel_take` 里有两次
    模型调用：工法选序与项目文件选章），也不该为了一条接线断言去花钱。
    """
    print("\n3) 接线：`_chapter_context` 把归属表交给 `_build_context`")
    real = WS._parallel_take
    WS._parallel_take = lambda jobs: {}
    # 接线测试固定归属表，不能依赖旧库里一张后来已被识别为空壳的图。
    wiring = patch("app.services.image_ownership.project_image_skips",
                   return_value={285: {CAP: "第一节 交付批次规划"}})
    wiring.start()
    try:
        c285 = WS._chapter_context(4, 285)          # 容器章：让出横道图
        c286 = WS._chapter_context(4, 286)          # 子章：就是归属
    except ValueError as e:
        print(f"  [--] 项目 4 的目录不满足前提，跳过（{e}）")
        return
    finally:
        WS._parallel_take = real
        wiring.stop()

    check("容器章 #285 的 ctx 里带上了让位表", c285.get("img_skips"), {CAP: "第一节 交付批次规划"})
    check("归属章 #286 的 ctx 里没有让位表", c286.get("img_skips"), {})
    # 传了 None 也该就地算出来（单章 SSE / 对话工具走的就是这条路）
    WS._parallel_take = lambda jobs: {}
    try:
        c = WS._chapter_context(4, 285, {CAP: "第一节 交付批次规划"})
    finally:
        WS._parallel_take = real
    check("批量路径显式传表时结果一致", c.get("img_skips"), c285.get("img_skips"))


# ---------------------------------------------------------------- 4. 真实数据
def section_4() -> None:
    """真实目录上的**性质断言**：每个被多章点名的图名，恰好只有一章不让位。

    不断言具体的章 id——`_page_ids` 会随目录/思路重跑而变，写死 id 的断言迟早变噪音。
    要守的性质是"归属唯一"，它对任何数据都成立。
    """
    print("\n4) 项目 4 真实数据：归属唯一")
    skips = project_image_skips(4)
    if not skips:
        print("  [--] 项目 4 没有需要裁决的图（目录或 _page_ids 为空？），跳过")
        return

    # 从让位表反推：每个让出的图名，恰好属于一个归属章；且没有任何章让出自己归属的图。
    by_cap: dict[str, set[str]] = {}
    for nid, m in skips.items():
        for cap, owner in m.items():
            by_cap.setdefault(cap, set()).add(owner)
    check("每个让出的图名只有一个归属章",
          sorted({c for c, o in by_cap.items() if len(o) > 1}), [])
    check("没有哪一章把自己归属的图又让出去",
          sorted({c for c, o in by_cap.items() if len(o) == 1 and
                  any(c in mm for i, mm in skips.items() if o == {i})}), [])

    # 非空校验：整份表是空的会静默变绿，所以至少要看见项目 4 上真实存在的那一组
    hit = [n for n, m in skips.items() if CAP in m]
    check("真实可用图片确实参与了裁决（表不是空转）", bool(by_cap), True)
    if hit:
        print(f"     #{hit[0]} 让出「{CAP}」→《{skips[hit[0]][CAP]}》")

    print(f"     共 {len(skips)} 章需让位、{len(by_cap)} 个图名被裁决")
    # 图名口径也得对：算归属与渲染用的是同一个函数（分家就会出现"表里叫 A、提示词写 B"）
    check("image_name：caption 优先", image_name({"caption": CAP, "title": "x"}), CAP)
    check("image_name：老数据回退 title", image_name({"title": "x"}), "x")
    check("image_name：非 dict 不炸", image_name("x"), "")


# ---------------------------------------------------------------- 5. 成稿本身
def section_5() -> None:
    """**结果**断言：项目 4 的存稿里没有两张一样的图。

    前四节都是过程断言——归属表算对、接线接对，不等于库里那份稿子就没重复。今天就撞上过：
    程序修好之后重新扫存稿，`#287` 和 `#292` 仍各带一张「无人机实飞测试现场」，
    是**旧代码写进去的**，直到重跑 #292 才清掉。生成侧再正确也改不了已经写下的正文。

    ⚠ 这是"不该出现"式的断言，**天生容易假绿**：库里查不到正文时，重复数也是 0。
       所以下面必须配正向断言（读到多少章、多少张图）——否则库一空就全绿。
    """
    print("\n5) 项目 4 的存稿本身：没有两张一样的图")

    def body_of(raw) -> str:
        if not raw:
            return ""
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        try:
            o = json.loads(raw)
        except (TypeError, ValueError):
            return raw
        # ⚠ 只看 body：重写后的 content 里还存着 prev_content，整串扫会把老稿算进来，
        #   重复数直接翻倍（这个坑踩过）。
        return (o.get("body") or "") if isinstance(o, dict) else ""

    rows = query(
        "SELECT nd.id, nd.title, c.content FROM chapter_node nd"
        " JOIN section_content c ON c.chapter_node_id = nd.id"
        " WHERE nd.project_id = %s",
        (4,),
    )
    mer: dict[str, list[str]] = {}
    ph: dict[str, list[str]] = {}
    n_ch = 0
    for r in rows:
        b = body_of(r["content"])
        if not b:
            continue
        n_ch += 1
        who = f"#{r['id']}"
        for m in re.findall(r"```mermaid\s*(.*?)```", b, re.S):
            mer.setdefault(re.sub(r"\s+", "", m), []).append(who)
        for m in re.findall(r"\[(?:图片占位|待补图)[：:]\s*([^\]]*)\]", b):
            # 占位写的是 `图名/用途说明`：不切出图名，同一张图配了不同用途就会被算成两张
            nm = re.split(r"[/／，,（(]", m, 1)[0].strip()
            if nm:
                ph.setdefault(nm, []).append(who)

    check("读到正文的章数不为 0（否则下面的「没重复」是假绿）", n_ch > 0, True)
    check("存稿里确实有图（同上）", len(mer) + len(ph) > 0, True)
    check("mermaid 图没有两张一样的", [v for k, v in mer.items() if len(v) > 1], [])
    check("图片占位没有两个同名图", [v for k, v in ph.items() if len(v) > 1], [])
    print(f"     实扫 {n_ch} 章：mermaid {len(mer)} 张、占位 {sum(len(v) for v in ph.values())} 处")


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

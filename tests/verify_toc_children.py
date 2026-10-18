"""校验「子节以招标规定为准」：规定了就原样照抄，没规定才让模型编（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_toc_children.py

背景：目录是两阶段生成的。阶段一 `generate_toc_titles` 拿到了 `tender_toc`，系统提示词里
写着「【最高优先级】……必须以它为骨架原样保留其章」；阶段二 `generate_toc_children` **从来
没拿到** `tender_toc`，只能照提示词「按惯例列 2~8 个」现编子节。

实测后果：项目 4 九个章的子节**无一与招标规定相符**——响应函招标只规定 1 节、编出 4 节；
技术方案招标 7 节、编出 8 节；技术标准对照表 6 节名字全被换掉。

检查四件事：
  1. `_tender_children` 的匹配：原样、带前缀、包含兜底、查无此章、该章无子节；
  2. **有规定时不调模型**——把 `llm.chat` 换成会抛异常的桩，仍须正常返回（这是本条修复的全部价值）；
  3. 没规定时才走模型，且走的是原路径（桩返回什么就得到什么）；
  4. 真实语料（连库）：项目 4 每一个章都能从 `toc_hint` 拿到规定的子节，且与 `toc_hint` 逐字相同。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import orchestrate_agent as OA                          # noqa: E402

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


TOC = {"found": True, "chapters": [
    {"title": "一、响应函", "children": [{"title": "第一节 响应函正文"}]},
    {"title": "四、技术方案", "children": [{"title": "第一节 总体技术路线与验证体系架构"},
                                          {"title": "第二节 新飞机采购全量指令验证方案"}]},
    {"title": "九、其他资料", "children": []},
]}


class _Boom:
    """会抛异常的 llm 桩：一旦被调用，测试立刻炸——用来证明"没调模型"。"""
    def chat(self, *a, **k):
        raise AssertionError("不该调模型！招标已规定这一章的子节")


class _Fake:
    """假模型：返回固定子节，用来证明"没规定时才走原路径"。"""
    def chat(self, *a, **k):
        return '{"children": [{"title": "第一节 模型编的"}]}'


# ---------------------------------------------------------------- 1. 匹配
def section_1() -> None:
    print("\n1) _tender_children 的匹配规则")
    t = OA._tender_children
    check("原样章名", t(TOC, "一、响应函"), [{"title": "第一节 响应函正文"}])
    check("章名少个前缀也能对上", t(TOC, "技术方案"),
          [{"title": "第一节 总体技术路线与验证体系架构"},
           {"title": "第二节 新飞机采购全量指令验证方案"}])
    check("带空格/编号差异", t(TOC, " 四、 技术方案 "),
          [{"title": "第一节 总体技术路线与验证体系架构"},
           {"title": "第二节 新飞机采购全量指令验证方案"}])
    check("招标没规定的章 → 空（走模型）", t(TOC, "十、某个新章"), [])
    check("该章在但没规定子节 → 空", t(TOC, "九、其他资料"), [])
    check("空目录 → 空", t(None, "一、响应函"), [])
    check("空章名 → 空", t(TOC, ""), [])
    check("子节名两头的空白被清掉",
          t({"chapters": [{"title": "甲", "children": [{"title": "  第一节 乙  "}]}]}, "甲"),
          [{"title": "第一节 乙"}])


# ---------------------------------------------------------------- 2 & 3. 调不调模型
def section_2_3() -> None:
    print("\n2) 有规定时一次模型都不调")
    real = OA.llm
    OA.llm = _Boom()
    try:
        got = OA.generate_toc_children("四、技术方案", "项目信息", [], "招标要求", tender_toc=TOC)
        check("返回招标规定的子节", got, [{"title": "第一节 总体技术路线与验证体系架构"},
                                          {"title": "第二节 新飞机采购全量指令验证方案"}])
    except AssertionError as e:
        check("有规定时不调模型", f"调了模型：{e}", "没调模型")
    finally:
        OA.llm = real

    print("\n3) 没规定时才走模型（且是原路径）")
    OA.llm = _Fake()
    try:
        got = OA.generate_toc_children("十、某个新章", "项目信息", [], "招标要求", tender_toc=TOC)
        check("模型产物原样返回", got, [{"title": "第一节 模型编的"}])
        # 不传 tender_toc（老调用方的写法）也不能炸
        got = OA.generate_toc_children("十、某个新章", "项目信息", [], "招标要求")
        check("不传 tender_toc 仍走模型", got, [{"title": "第一节 模型编的"}])
    finally:
        OA.llm = real


# ---------------------------------------------------------------- 4. 真实语料
def section_4() -> None:
    print("\n4) 真实语料：项目 4 每一章都能拿到招标规定的子节")
    try:
        import json

        from app.core.database import query_one
        row = query_one("SELECT toc_hint FROM project WHERE id = 4")
    except Exception as e:  # noqa: BLE001 没库就跳过，前三节仍然作数
        print(f"  [--] 连不上库，跳过：{type(e).__name__}: {e}")
        return
    hint = (row or {}).get("toc_hint")
    if isinstance(hint, str):
        hint = json.loads(hint)
    if not hint or not hint.get("chapters"):
        print("  [--] 项目 4 没有 toc_hint，跳过")
        return

    real = OA.llm
    OA.llm = _Boom()          # 任何一个章走到模型都算失败
    try:
        for ch in hint["chapters"]:
            want = [{"title": k["title"]} for k in ch.get("children") or []]
            got = OA.generate_toc_children(ch["title"], "", [], "", tender_toc=hint)
            check(f"「{ch['title'][:20]}」原样照抄（{len(want)} 节）", got, want)
    finally:
        OA.llm = real


def main() -> int:
    section_1()
    section_2_3()
    section_4()
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

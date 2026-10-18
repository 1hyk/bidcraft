"""校验**每次上传/每章生成都会跑到低空项目上**的那几处提示词，已按领域隔离（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_domain_prompt_isolation.py

背景（2026-10-15，第 2 组）：低空项目建好后，仍有一批提示词把铁路词汇喂给模型——
它们与「标段档案」无关，因此没有被上一组修掉（`verify_lot_profile_isolation.py`）：

  · `fact_agent.DOC_TYPE_HINTS`            —— 每次上传图纸/报告/策划都拼进抽取提示
                                              （「设计荷载、设计时速、轨道类型」「大临布置原则」）
  · `file_index.SUMMARY_FACTS_SYSTEM`      —— 每章建索引都发（「架梁开始/完成、铺轨开始」
                                              「桥梁/隧道/路基总规模、轨道类型」）
  · `tender_agent.EXTRACT_SYSTEM`          —— 每次上传招标文件都发（整段「二、标段划分」，
                                              例子是 CGJXZQ-3/HSZQ-4，范围内涵写「里程」）
  · `matching.JUDGE_SYSTEM`                —— 每章匹配知识页都发（「桥梁下部结构施工」）
  · `method_index` / `method_agent` / `knowledge_agent` / `requirement_agent`
                                            —— 提炼与抽题时发

修法与第 1 组同一判据：**换一个领域，这行字还能原样用吗？** 不能就只有两条路——
要么挂到 `Domain`（表），要么改成中性措辞（与领域无关的通用说明）。
`tender_agent` 的「标段划分」段选的是第一条路：段落的**存废**由 `Domain.lot_fields` 决定，
低空项目根本不该有这一段——这与 `file_index` 的 C) 标段归属段是同一个形状。

检查：
  1. `DOC_TYPE_HINTS`：三条侧重项一个铁路词都没有；
  2. `file_index`：`SUMMARY_SYSTEM` 已删（死代码），模板本身干净，
     `summary_prompt(False)` 不拼标段段、`summary_prompt(True)` 拼；
  3. `tender_agent`：`NO_LOTS_SECTION` 版干净且不含标段代码例子，
     `_system_prompt(expect_lots=False)` 走的是它，`=True` 才走 `LOTS_SECTION`；
     并且**本域没标段时强制清空 lots**（不因模型多抽就落库）；
  4. 其余常驻提示词：`matching` / `method_index` / `method_agent` /
     `knowledge_agent` / `requirement_agent` 的提示词常量里没有铁路词；
  5. 真库上跑一遍：项目 4（低空）走到的系统提示词里没有标段段；
  6. **体裁词（第 5 组）**：`施工组织设计/施组/技术标` 那套词已挂到 `Domain.genre`——
     `{G_*}` 占位符按域填、填完不许有残留、低空填出来必须是「技术方案」；
     并且 AST 扫一遍全仓，不许再有裸的 `G_*` 名字（f-string 会把它当变量，
     上一轮就是这么炸的：`NameError: name 'G_SHORT' is not defined`）；
  7. 换一套体裁词（临时域）→ 同一份常量改说的是那套词（证明表真的在起作用）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

ok = True

#: 这些词一个都不许出现在**发给模型**的提示词里（注释/docstring 里讲历史是可以的）
RAIL_WORDS = ("路基", "桥梁", "隧道", "箱梁", "铺轨", "联络线", "梁场", "拌和站",
              "盾构", "管片", "弃土", "弃渣", "里程", "特大桥", "站场", "枢纽",
              "邻近既有线", "无砟轨道", "轨道类型", "钻孔灌注桩", "墩身", "桩基",
              "大临", "土木工程", "CGJXZQ", "HSZQ")


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def rail_hits(text: str) -> list[str]:
    return [w for w in RAIL_WORDS if w in (text or "")]


# ---------------------------------------------------------------- 1. 上传抽取
def section_1() -> None:
    print("\n1) 上传时拼进抽取提示的侧重项（fact_agent.DOC_TYPE_HINTS）")
    from app.agents import fact_agent

    for kind, hint in fact_agent.DOC_TYPE_HINTS.items():
        check(f"{kind} 侧重项不含铁路词", rail_hits(hint), [])
    check("三条侧重项都还在（不是被删空）", sorted(fact_agent.DOC_TYPE_HINTS),
          ["drawing", "planning", "survey_report"])


# ---------------------------------------------------------------- 2. 建索引
def section_2() -> None:
    print("\n2) 每章建索引都发的 system（file_index.SUMMARY_FACTS_SYSTEM）")
    from app.services import file_index as FI

    check("死代码 SUMMARY_SYSTEM 已删", hasattr(FI, "SUMMARY_SYSTEM"), False)
    check("模板本身（未替换前）不含铁路词", rail_hits(FI.SUMMARY_FACTS_SYSTEM), [])

    off = FI.summary_prompt(False)
    on = FI.summary_prompt(True)
    check("本域无标段：不含铁路词", rail_hits(off), [])
    check("本域无标段：不拼 C) 标段归属段", "标段归属" in off, False)
    # 有标段时才拼，且拼进去的是 LOTS_BLOCK 原文
    check("有标段：拼上了 C) 段", FI.LOTS_BLOCK.strip()[:20] in on, True)
    check("有标段：JSON 骨架带 applies_lots", '"applies_lots"' in on, True)
    check("无标段：JSON 骨架不带 applies_lots", '"applies_lots"' in off, False)
    # 占位符必须都被替换掉——漏一个就是提示词里留着 {xxx} 给模型看
    check("无标段：占位符全部替换", [p for p in ("{categories}", "{lots_block}", "{shape}")
                                     if p in off], [])
    check("有标段：占位符全部替换", [p for p in ("{categories}", "{lots_block}", "{shape}")
                                     if p in on], [])


# ---------------------------------------------------------------- 3. 招标文件整体抽取
def section_3() -> None:
    print("\n3) 每次上传招标文件都发的整体抽取（tender_agent.EXTRACT_SYSTEM）")
    from app.agents import tender_agent as TA

    check("模板本身（未替换前）不含铁路词", rail_hits(TA.EXTRACT_SYSTEM), [])
    check("无标段版干净", rail_hits(TA.NO_LOTS_SECTION), [])
    # 有标段版允许出现「标段」这个词（那是这个域真实的概念），但**不许**有铁路标段代码
    check("有标段版不含铁路标段代码",
          [w for w in ("CGJXZQ", "HSZQ") if w in TA.LOTS_SECTION], [])
    check("有标段版范围口径不再是「里程」", "里程" in TA.LOTS_SECTION, False)

    off = TA._system_prompt([], "", expect_lots=False)
    on = TA._system_prompt([], "", expect_lots=True)
    check("无标段：走 NO_LOTS_SECTION", TA.NO_LOTS_SECTION.strip()[:12] in off, True)
    check("无标段：不含铁路词", rail_hits(off), [])
    check("无标段：输出形状里 lots 为空数组",
          '"has_lots": false' in off and '"lots": []' in off, True)
    check("有标段：走 LOTS_SECTION", TA.LOTS_SECTION.strip()[:12] in on, True)
    check("有标段：输出形状里带 lot_code", '"lot_code"' in on, True)
    # 占位符都替换掉了
    check("无标段：占位符全部替换",
          [p for p in ("{LOTS_SECTION}", "{LOTS_SHAPE}", "{EXISTING_TOC}",
                       "{FALLBACK_CHAPTERS}") if p in off], [])
    check("有标段：占位符全部替换",
          [p for p in ("{LOTS_SECTION}", "{LOTS_SHAPE}", "{EXISTING_TOC}",
                       "{FALLBACK_CHAPTERS}") if p in on], [])
    # 已有目录块也要能正常拼进去（别改坏了原有的替换）
    on2 = TA._system_prompt(["第一章 总则"], "", expect_lots=True)
    check("已有目录仍会拼进提示词", "第一章 总则" in on2, True)

    # **强制清空**：本域声明没有标段时，模型多抽了也不落库
    import inspect

    src = inspect.getsource(TA.extract_tender_all)
    check("无标段时强制清空 lots（不看模型脸色）",
          "if expect_lots else []" in src, True)


# ---------------------------------------------------------------- 4. 其余常驻提示词
def section_4() -> None:
    print("\n4) 其余每次章节生成/提炼都要发的提示词")
    from app.agents import knowledge_agent as KA, method_agent as MA, requirement_agent as RA
    from app.services import matching as MT, method_index as MI

    consts = {
        "matching.JUDGE_SYSTEM": MT.JUDGE_SYSTEM,
        "method_index.OVERVIEW_SYSTEM": MI.OVERVIEW_SYSTEM,
        "method_index.SELECT_SYSTEM": MI.SELECT_SYSTEM,
        "method_agent.METHOD_SYSTEM": MA.METHOD_SYSTEM,
        "method_agent.METHOD_MERGE_PROMPT": MA.METHOD_MERGE_PROMPT,
        "knowledge_agent.SYSTEM_PROMPT": KA.SYSTEM_PROMPT,
        "knowledge_agent.MERGE_PROMPT": KA.MERGE_PROMPT,
        "requirement_agent.TOC_SYSTEM": RA.TOC_SYSTEM,
    }
    for name, text in consts.items():
        check(f"{name} 不含铁路词", rail_hits(text), [])

    # 招标要求那四条专项（对话内按需重抽时走）
    for item in RA.TASKS if hasattr(RA, "TASKS") else []:
        check(f"requirement_agent.{item.get('key')} 不含铁路词",
              rail_hits(str(item.get("prompt"))), [])


# ---------------------------------------------------------------- 5. 真库
def section_5() -> None:
    print("\n5) 真库上跑一遍：项目 4（低空）走到的提示词没有标段段")
    from app.agents import tender_agent as TA
    from app.core.domains import domain_of_project, lot_fields_of

    dom = domain_of_project(4)
    check("项目 4 是低空域", dom, "lowalt")
    expect_lots = bool(lot_fields_of(dom))
    check("按域算出来的 expect_lots", expect_lots, False)
    sys_prompt = TA._system_prompt([], "", expect_lots)
    check("项目 4 的系统提示词：不拼标段段", "标段划分（lots）" in sys_prompt and
          "没有「标段」这一层次" in sys_prompt, True)
    check("项目 4 的系统提示词：无铁路词", rail_hits(sys_prompt), [])


# ---------------------------------------------------------------- 6. 体裁词（第 5 组）
#: 这些词是「施工组织设计」那套体裁名词，历史上写死在提示词里。谁都不许再写死：
#: 一个低空项目必须说「技术方案」，铁路那套词只应该出现在**声明了那套词**的域里。
RAIL_GENRE = ("施工组织设计", "施组", "技术标")

#: 「技术标」是「技术标准/技术标注/技术标识/技术标记」的前缀，直接子串匹配会误报——
#: 那四个词是正经技术术语，跟体裁无关。扫提示词时用这个正则，别用裸 in。
RAIL_GENRE_RE = re.compile("|".join(
    [re.escape(w) for w in ("施工组织设计", "施组")] + [r"技术标(?!准|注|识|记)"]))


def genre_hits(text: str) -> list[str]:
    return sorted(set(RAIL_GENRE_RE.findall(text or "")))

#: 提示词跑得最勤的那几个模块——它们的模块级字符串常量都参与「发给模型」。
GENRE_MODULES = (
    "app.agents.writer_agent", "app.agents.knowledge_agent",
    "app.agents.method_agent", "app.agents.orchestrate_agent",
    "app.agents.chat_agent", "app.agents.requirement_agent",
    "app.agents.tender_agent", "app.services.fact_service",
)


def _str_consts(mod) -> dict[str, str]:
    """模块级里所有含 {G_ 占位符的字符串（含 TASKS 那种嵌套结构里的）。"""
    found: dict[str, str] = {}

    def walk(obj, path: str) -> None:
        if isinstance(obj, str):
            if "{G_" in obj:
                found[path] = obj
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}[{k!r}]")
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")

    for name in dir(mod):
        if name.startswith("__"):
            continue
        walk(getattr(mod, name), f"{mod.__name__}.{name}")
    return found


def section_6() -> None:
    print("\n6) 体裁词（施工组织设计/施组/技术标）已挂到 Domain.genre（第 5 组）")
    import importlib

    from app.core import domains as D

    # 6a. 表本身
    for key, dom in D.DOMAINS.items():
        g = dom.genre
        check(f"{key} 声明了体裁词", all((g.full, g.short, g.bid)), True)
    check("低空的成稿叫「技术方案」", D.genre_of("lowalt").full, "技术方案")
    check("list_domains 带 genre 块",
          all("genre" in d for d in D.list_domains()), True)

    # 6b. DocGenre.intro：三个词一样时不画蛇添足
    same = D.DocGenre(full="技术方案", short="技术方案", bid="技术方案")
    check("三词相同：intro 无括号", same.intro, "技术方案")
    diff = D.DocGenre(full="施工组织设计", short="施组", bid="技术标")
    check("三词不同：intro 带括号", diff.intro, "施工组织设计（施组/技术标）")

    # 6c. fill_genre
    check("低空填充四个占位符",
          D.fill_genre("{G_FULL}|{G_SHORT}|{G_BID}|{G_INTRO}", "lowalt"),
          "技术方案|技术方案|技术方案|技术方案")
    check("未知域回落到默认域而不是崩", D.fill_genre("{G_FULL}", "不存在的域"),
          D.genre_of(None).full)

    # 6d. 常量：按低空填好之后既无残留占位符、也无铁路体裁词
    for modname in GENRE_MODULES:
        mod = importlib.import_module(modname)
        for name, raw in _str_consts(mod).items():
            filled = D.fill_genre(raw, "lowalt")
            check(f"{name} 填完无残留占位符",
                  [p for p in ("{G_FULL}", "{G_SHORT}", "{G_BID}", "{G_INTRO}")
                   if p in filled], [])
            check(f"{name} 低空填完无铁路体裁词", genre_hits(filled), [])

    # 6e. 调用点确实填了（静态）：用了占位符的模块里必须有 fill_genre(
    import inspect

    for modname in GENRE_MODULES:
        mod = importlib.import_module(modname)
        if _str_consts(mod):
            check(f"{modname} 用了 fill_genre",
                  "fill_genre" in inspect.getsource(mod), True)

    # 6f. 钉住这次修的那类 bug：f-string 里写 {G_XXX} 会在**拼串时**当变量解析（NameError）。
    #     占位符必须以 {{G_XXX}} 逃逸，或在非 f-string 里。AST 扫一遍：除 domains.py 外
    #     不许出现裸的 G_* 名字。
    import ast
    from pathlib import Path as _P

    leaks = []
    for p in (ROOT / "backend" / "app").rglob("*.py"):
        if p.name == "domains.py":
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name) and node.id.startswith("G_"):
                leaks.append(f"{p.name}:{node.lineno}:{node.id}")
    check("没有裸的 G_* 名字（f-string 会当变量解析）", leaks, [])

    # 6g. 真库：项目 4（低空）编出来的每章 system 提示词
    from app.agents import writer_agent as WA
    from app.agents import tender_agent as TA

    dom = D.domain_of_project(4)
    check("项目 4 是低空域", dom, "lowalt")
    wr = D.fill_genre(WA.WRITER_SYSTEM, dom)
    check("项目 4 的写手 system：无残留占位符",
          [p for p in ("{G_FULL}", "{G_SHORT}", "{G_BID}", "{G_INTRO}") if p in wr], [])
    check("项目 4 的写手 system：无铁路体裁词", genre_hits(wr), [])
    check("项目 4 的写手 system：说的是技术方案", "技术方案" in wr, True)
    ex = D.fill_genre(TA.EXTRACT_SYSTEM, dom)
    check("项目 4 的招标抽取 system：无残留占位符",
          [p for p in ("{G_FULL}", "{G_SHORT}", "{G_BID}", "{G_INTRO}") if p in ex], [])
    check("项目 4 的招标抽取 system：无铁路体裁词",
          genre_hits(ex), [])
    # 本册目录的认词也只有本域那份 + 通用那份
    check("低空的目录认词=技术方案|投标文件格式",
          D.genre_of("lowalt").keywords + ("投标文件格式",),
          ("技术方案", "投标文件格式"))
    check("铁路体裁词不在低空的目录认词里",
          [w for w in RAIL_GENRE if w in "|".join(D.genre_of("lowalt").keywords)], [])


# ---------------------------------------------------------------- 7. 临时域对照
def section_7() -> None:
    print("\n7) 换一套体裁词（临时域）→ 同一份常量改说的是那套词")
    from app.core import domains as D
    from app.agents import writer_agent as WA

    key = "__verify_genre__"
    D.DOMAINS[key] = D.Domain(
        key=key, name="回归验证用（临时体裁域）", tags=(),
        genre=D.DocGenre(full="施工组织设计", short="施组", bid="技术标",
                         keywords=("施工组织设计", "施组")),
    )
    try:
        wr = D.fill_genre(WA.WRITER_SYSTEM, key)
        check("换成铁路体裁词后：出现「施组」", "施组" in wr, True)
        check("换成铁路体裁词后：不再说「技术方案」", "技术方案" in wr, False)
        check("换成铁路体裁词后：无残留占位符",
              [p for p in ("{G_FULL}", "{G_SHORT}", "{G_BID}", "{G_INTRO}") if p in wr], [])
    finally:
        D.DOMAINS.pop(key, None)


# ---------------------------------------------------------------- 8. 行业做法名词（第 6 组）
#: 比体裁词更细一层。体裁词问的是「这份文件叫什么」（施工组织设计/技术方案，已挂 `Domain.genre`），
#: 这批问的是「里面的做法叫什么」——「施工」那一套。同一个道理：写死了就是拿一个行业的名词
#: 给另一个行业定调。两个域的成稿都叫「技术方案」，正文里根本没有"施工"这回事。
#:
#: 2026-10-16 补这一节的原因：第 5 组只查了体裁词（`RAIL_GENRE`），而 `RAIL_WORDS` 里
#: 有路基/桥梁/隧道、**没有「施工」**——于是 `【工艺工法（施工方法素材…）】` 这种
#: 每写一章都发出去的块头，从两道检查中间漏了过去。词表按**出现过的实测**列，不凭想象加。
CIVIL_WORDS = ("施工", "泥浆比重", "导管埋深", "主体结构", "基础施工", "工程地质")


def civil_hits(text: str) -> list[str]:
    return sorted({w for w in CIVIL_WORDS if w in (text or "")})


#: 手工列出的**在跑**的提示词常量（模块级常量，全量扫会撞上死代码，见下面的豁免）。
def section_8() -> None:
    print("\n8) 行业做法名词（「施工」那套）不写死在发给模型的提示词里（第 6 组）")
    from app.services import matching as MT, method_index as MI
    from app.agents import writer_agent as WA

    live = {
        "writer_agent.WRITER_SYSTEM": WA.WRITER_SYSTEM,
        "matching.JUDGE_SYSTEM": MT.JUDGE_SYSTEM,
        "method_index.OVERVIEW_SYSTEM": MI.OVERVIEW_SYSTEM,
        "method_index.SELECT_SYSTEM": MI.SELECT_SYSTEM,
    }
    for name, text in live.items():
        check(f"{name} 无写死的行业做法名词", civil_hits(text), [])

    # 8b. 工法注入块**不是常量、是拼出来的**，所以直接调 _build_context 抓真实产物
    #     ——只查常量的话，这一处永远查不到（它就是漏掉的那一处）。
    fake = [{"name": "XX测试方法", "text": "步骤一：……"}]
    core = dict(facts=[], knowledge=[], siblings=[], experiences=[], methods=fake,
                file_refs=[], lot_info="", extra_requirements="", reqs="")
    outline = {"title": "测试验收准备方案", "thinking": ""}
    ctx = WA._build_context(outline, **core, domain="lowalt")
    check("低空：工法块按域说「技术方案」", "技术方案的方法素材" in ctx, True)
    check("低空：整段上下文无「施工」", civil_hits(ctx), [])
    check("低空：上下文里不留占位符",
          [p for p in ("{G_FULL}", "{G_SHORT}", "{G_BID}", "{G_INTRO}") if p in ctx], [])

    # 8c. 换一套体裁词（临时域）→ 同一个块跟着换（证明它真的接了域，不是碰巧没写"施工"）
    from app.core import domains as D
    key = "__verify_civil__"
    D.DOMAINS[key] = D.Domain(
        key=key, name="回归验证用（临时体裁域）", tags=(),
        genre=D.DocGenre(full="施工组织设计", short="施组", bid="技术标",
                         keywords=("施工组织设计",)),
    )
    try:
        ctx2 = WA._build_context(outline, **core, domain=key)
        check("换成铁路体裁词：工法块跟着变「施组的方法素材」",
              "施组的方法素材" in ctx2, True)
    finally:
        D.DOMAINS.pop(key, None)

    # 8d. 死代码豁免**必须能自我失效**：本模块不扫 method_agent（它通篇施工口径，
    #     已在模块里标了 ⚠），但一旦有人把它接回去，这条豁免就得立刻不成立。
    #     不是断言"没人引用"这件事本身，而是断言"豁免的前提还在"。
    refs = sorted({
        p.name for p in (ROOT / "backend" / "app").rglob("*.py")
        if p.name != "method_agent.py" and "method_agent" in p.read_text(encoding="utf-8")
    })
    check("method_agent 仍零调用点（接回去就得重写它的提示词，见模块内 ⚠）", refs, [])


def main() -> int:
    section_1()
    section_2()
    section_3()
    section_4()
    section_5()
    section_6()
    section_7()
    section_8()
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

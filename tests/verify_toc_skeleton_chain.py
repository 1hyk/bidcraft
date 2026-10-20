"""校验「章级骨架」这条链：按域取、招标优先、没有就不给（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_toc_skeleton_chain.py

背景（2026-10-15）：`orchestrate_agent.SKELETON_CHAPTERS` 是一张写死的 **11 个铁路
土建章名**表，模块级常量、读的时候不看 domain，而 `generate_toc_titles` 无条件吃它。
后果是**任何项目**（包括新加的低空域）一生成目录就先落一份铁路骨架；同时知识库里
对得上本项目评审办法的 1156 页语料**在章级压根够不着**，因为 `chapter_skeleton`
只被喂到子节那一层。低空域的空转是必然的：章名是铁路的，子节只好由模型现编。

修法（用户拍板「① 先修章级这条链」）：
    ① 招标文件规定的目录（评标格式红线）
    ② 本领域声明的候选骨架（`Domain.chapters`）
    ③ 都没有 → 传 None，让模型按项目描述自编
**任何一档都不许回退到别的领域**——静默跨域兜底正是这个 bug 的成因。

检查：
  1. 领域注册表：railway 已删；章骨架按域取，没声明就是空串（不是别的域的）；
  2. 提示词：不给骨架时整段不出现、且不残留任何铁路章名；给了就原样到达；
  3. 优先级：招标规定了目录时不再叠加领域骨架（两条线不打架）；
  4. **真实语料**：lowalt 声明的五章，每一章在低空知识库里都能数出非空子节骨架
     ——这是「章与节能接成一份连贯的两级目录」的前提，也是这一档存在的意义。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


# ---------------------------------------------------------------- 1. 领域注册表
def section_1() -> None:
    print("\n1) 章级骨架按域取，域没声明就是空（不回退别域）")
    from app.core.domains import DOMAINS, chapters_of, list_domains

    check("railway 域已删", "railway" in DOMAINS, False)

    low = chapters_of("lowalt")
    check("lowalt 声明了 5 章", len(low), 5)
    print(f"       lowalt 章骨架：{' / '.join(low)}")
    check("softtest 没声明（空元组）", chapters_of("softtest"), ())

    # 这张表存在的意义：域与域之间不得互相借用章名
    check("两域章骨架无交集", set(low) & set(chapters_of("softtest")), set())
    check("list_domains 带出 chapters",
          all("chapters" in d for d in list_domains()), True)

    # 「工程类型」也是领域词表——原先是前端写死的 ['铁路','公路','房建','市政','水利']，
    # 加了低空领域之后那几个选项里根本没有低空（新建低空项目时一个类型都点不了）。
    types = {d["key"]: d["project_types"] for d in list_domains()}
    check("list_domains 带出全部注册领域的 project_types", set(types), set(DOMAINS))
    check("lowalt 的业务服务类型", types["lowalt"],
          ["飞行测试验证", "软件测试", "平台软件开发", "系统集成与部署", "运维与技术保障", "综合服务"])
    check("工程类型里不含别的领域的词",
          [t for v in types.values() for t in v
           if t in ("铁路", "公路", "房建", "市政", "水利")], [])


# ---------------------------------------------------------------- 1b. 不静默兜底
def _capture_stdout(fn) -> str:
    """跑 fn，把它打到 stdout 的东西收回来（`get_domain` 的警告走的是 print）。"""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        fn()
    return buf.getvalue()


def section_1b() -> None:
    """「没给」和「不认识」必须分开——这是 2026-10-15 那个跨域 bug 的同款形状。

    原先 `get_domain` 就一句 `DOMAINS.get(key) or DOMAINS[default]`：**不认识的领域名
    悄悄变成默认域**，下游一路拿默认域的知识库打分，没有任何一处报错。删掉
    `Domain("railway")` 之后，任何 domain='railway' 的历史行都会走到这条路。
    """
    print("\n1b) 不认识的领域 key 要出声（不静默兜底）")
    from app.core import domains as D

    # 没给：调用方就是没传 → 默认域，**合法**，不该吵
    quiet = _capture_stdout(lambda: [D.get_domain(None), D.get_domain(""), D.get_domain("   ")])
    check("没给领域时不出声", quiet, "")
    check("没给领域时给默认域", D.get_domain(None).key, "lowalt")

    # 给了但不认识：值仍然是默认域（宁可退化不抛错），但**必须留痕**
    D._warned_unknown.clear()
    out = _capture_stdout(lambda: D.get_domain("railway"))
    check("不认识的 key 有警告", "railway" in out and "回退默认域" in out, True)
    check("警告里给出合法 key", "lowalt" in out and "softtest" in out, True)
    check("不认识的 key 仍然退化不抛错", D.get_domain("railway").key, "lowalt")

    # 同一个 key 只报一次：匹配链路上它被反复调用，每次都打等于没打
    D._warned_unknown.clear()
    twice = _capture_stdout(lambda: [D.get_domain("railway"), D.get_domain("railway"),
                                     D.get_domain("railway")])
    check("同 key 只报一次", twice.count("回退默认域"), 1)

    # 配置写错（默认域自己不在表里）——这个不该退化，必须点出来是哪一处
    real = D.settings.default_domain
    try:
        D.settings.default_domain = "不存在的域"
        try:
            D._fallback_domain("railway")
            raised = ""
        except KeyError as e:
            raised = str(e)
    finally:
        D.settings.default_domain = real
    check("默认域本身配错时直接报错", "default_domain" in raised, True)


# ---------------------------------------------------------------- 2. 提示词
def _capture_titles_prompt(tender_requirements: str = "评分办法：…", **kw) -> dict:
    """桩掉 llm.chat，跑一次 generate_toc_titles，返回它发出去的两段提示词。

    问的是**到达模型的那段话**，不是函数内部变量——上一轮的问题正是"函数看着没问题、
    提示词里却多了一段没人要的骨架"。system / user 分开返回：**规则**在 system 里
    （写死章名会藏在那种地方），**骨架**在 user 里。
    """
    import app.agents.orchestrate_agent as OA

    class _FakeLLM:
        def __init__(self):
            self.system = self.user = ""

        def chat(self, msgs, **k):
            self.system, self.user = msgs[0]["content"], msgs[1]["content"]
            return '{"chapters": [{"title": "第一章 甲"}]}'

    fake = _FakeLLM()
    real = OA.llm
    from app.core.config import settings
    original_scope = settings.generation_root_title
    try:
        # 本节专验“完整目录模式”的领域骨架契约；仅编技术方案模式另有范围守卫。
        settings.generation_root_title = ''
        OA.llm = fake
        OA.generate_toc_titles("项目信息：某低空项目", ["低空"], tender_requirements, **kw)
    finally:
        OA.llm = real
        settings.generation_root_title = original_scope
    return {"system": fake.system, "user": fake.user}


def section_2() -> None:
    print("\n2) 提示词：不给骨架时整段不出现，给了就原样到达")
    from app.core.domains import chapters_of

    base = _capture_titles_prompt()
    # 骨架块只在 user 段；system 段本来就写着「没给参考骨架就…」这句规则，不能拿它判有无。
    check("不给骨架时 user 段不含「参考骨架」", "参考骨架" in base["user"], False)
    check("system 段仍写着「照项目信息自己判断」", "照项目信息自己判断" in base["system"], True)
    # 写死的铁路章名一个都不许漏进来——system 段尤其要查，那正是旧表藏身之处
    for rail in ("铺轨", "站房", "路基", "桥梁", "隧道", "临时与过渡工程", "控制与重难点工程"):
        check(f"system 段不含铁路章名「{rail}」", rail in base["system"], False)
        check(f"user 段不含铁路章名「{rail}」", rail in base["user"], False)

    low = list(chapters_of("lowalt"))
    # 没招标要求时：骨架是**红线措辞**（"以它为准落地"）——这是 2026-10-15 那条修复，
    # 当时模型照单收下五章之后又把自己那套通用施组模板补了回来，10 章里 5 章没人要。
    given = _capture_titles_prompt(chapters=low, tender_requirements="")
    check("没招标要求时 user 段出现「参考骨架」", "参考骨架" in given["user"], True)
    check("没招标要求时骨架是红线措辞", "以它为准落地" in given["user"], True)
    for ch in low:
        check(f"章名原样到达「{ch}」", ch in given["user"], True)

    # 有招标要求时：骨架必须**降级成补漏参考**。`Domain.chapters` 那五支是**从某一份
    # 招标文件的评审办法里读出来的**，不是低空领域的定律；拿它去压另一份招标文件
    # （"以它为准落地、不涉及的可以删"），新招标要求写的内容就会被挤掉——用户看到的
    # 就是"我的技术方案框架被强制收缩成五章，换一份招标文件也不变"。
    led = _capture_titles_prompt(chapters=low)  # 默认第 3 个参数就是"评分办法：…"
    check("有招标要求时不许再写「以它为准落地」", "以它为准落地" in led["user"], False)
    check("有招标要求时骨架标成「仅供补漏」", "仅供补漏" in led["user"], True)
    for ch in low:
        check(f"降级后章名仍然到达「{ch}」", ch in led["user"], True)
    check("system 段写明两者并存时以招标文件为准",
          "以招标文件为准" in led["system"], True)
    check("system 段要求招标点名内容一项都不许少",
          "一项都不许少" in led["system"], True)

    # 常量必须真的删掉，而不是只改了调用点——留着就会被下一个人再捡起来
    import app.agents.orchestrate_agent as OA
    import app.agents.requirement_agent as RA

    check("SKELETON_CHAPTERS 已删", hasattr(OA, "SKELETON_CHAPTERS"), False)
    check("SUGGESTED_CHAPTERS 常量已删", hasattr(RA, "SUGGESTED_CHAPTERS"), False)
    check("改成了按域取函数", RA.suggested_chapters("lowalt"), "/".join(low))


# ---------------------------------------------------------------- 3. 优先级
def section_3() -> None:
    print("\n3) 招标规定目录 > 领域骨架：两条线不打架")
    from app.agents.compile_graph import _chapter_skeleton
    from app.core.domains import chapters_of

    check("没招标 → 给本域骨架", _chapter_skeleton("lowalt", None), list(chapters_of("lowalt")))
    check("招标 found=false → 给本域骨架", _chapter_skeleton("lowalt", {"found": False, "chapters": []}),
          list(chapters_of("lowalt")))
    # 招标规定了章：必须**什么都不给**，而不是给领域骨架。给了模型就会在红线与候选之间摇摆。
    tender = {"found": True, "note": "", "chapters": [{"title": "四、技术方案", "children": []}]}
    check("招标规定了目录 → 不再叠加领域骨架", _chapter_skeleton("lowalt", tender), None)
    check("没有骨架的域 + 没招标 → 也不给", _chapter_skeleton("softtest", None), [])

    # 同一份目录 + 领域骨架同时传进 generate_toc_titles 时，招标那段必须原样在
    from app.agents.orchestrate_agent import _chapters_block

    check("_chapters_block(None) 是空串", _chapters_block(None), "")
    check("_chapters_block([]) 也是空串", _chapters_block([]), "")
    # 同一个骨架，两种说法——差别就是 tender_led 这一个开关
    plain, led = _chapters_block(["甲", "乙"]), _chapters_block(["甲", "乙"], tender_led=True)
    check("两种说法确实不同", plain != led, True)
    check("补漏那版不再命令「以它为准落地」", "以它为准落地" in led, False)
    check("两版都把章名带上了", ("甲" in plain and "甲" in led), True)


# ---------------------------------------------------------------- 4. 真实语料
def section_4() -> None:
    print("\n4) 真实语料：lowalt 声明的每一章都要能在知识库里数出子节骨架")
    from app.core.domains import chapters_of
    from app.services.matching import chapter_skeleton, load_page_summaries

    pages = load_page_summaries("lowalt")
    print(f"     低空知识页 {len(pages)} 条")
    check("低空知识库非空", len(pages) > 0, True)

    empty = []
    for ch in chapters_of("lowalt"):
        skel = chapter_skeleton(ch, pages, domain="lowalt")
        if not skel:
            empty.append(ch)
        top = "、".join(f"{s['name']}({s['pages']}页)" for s in skel[:3])
        print(f"       {ch}: {len(skel)} 个子节  {top}")
    check("五章都有非空子节骨架（章节能接成两级目录）", empty, [])

    # 对照组：这张表**不该**再出现的地方——铁路域已删，但更直接的是这句话本身：
    # 招标没规定目录时，兜底必须来自本域语料，而不是某张通用表。
    rail = chapter_skeleton("施工方案", pages, domain="lowalt")
    print(f"       （对照）拿铁路章名「施工方案」查低空语料：{len(rail)} 个子节")


# ---------------------------------------------------------------- 5. 实跑闭环
def section_5(project_id: int) -> None:
    """真调模型跑一次低空项目的目录生成（**只读**：不落库、不动章节树）。

    用法：`... verify_toc_skeleton_chain.py --live [project_id]`

    **刻意不调用 `node_generate_toc`**：它会 `clear_tree` + `save_tree`，把项目现有的
    正文与思路全清掉（那是「重新生成目录」的既定语义）。这里要验的是"章骨架这条链
    通不通"，把三件事拆开单独跑就够：组装输入 → 章级 → 子节级。
    """
    print(f"\n5) 实跑低空闭环（项目 {project_id}，真调模型）")
    from app.agents.compile_graph import _chapter_skeleton
    from app.agents.orchestrate_agent import generate_toc_children, generate_toc_titles
    from app.core import chapter_title
    from app.core.domains import chapters_of, domain_of_project
    from app.services import compile_service
    from app.services.matching import chapter_skeleton, load_page_summaries

    dom = domain_of_project(project_id)
    inputs = compile_service.assemble_inputs(project_id)
    tender = inputs["tender_toc"] or {}
    print(f"     领域={dom}  招标规定施组目录={'有' if tender.get('chapters') else '无'}")

    skel = _chapter_skeleton(dom, tender)
    print(f"     传进提示词的章骨架：{skel}")
    if dom == "lowalt" and not tender.get("chapters"):
        check("低空项目、招标没规定目录 → 拿到本域五章", skel, list(chapters_of("lowalt")))

    titles = [t["title"] for t in generate_toc_titles(
        inputs["project_info"], inputs["professions"],
        inputs["tender_requirements"], inputs["tender_toc"], chapters=skel)]
    print(f"     模型给出 {len(titles)} 章：")
    for t in titles:
        print(f"       · {t}")

    # 模型被允许"可增删调整"，所以不做逐字相等——用项目自己的章名匹配口径（related）判在不在
    miss = [c for c in (skel or [])
            if not any(chapter_title.related(t, c) or chapter_title.related(c, t) for t in titles)]
    check("骨架里每一章都在结果里（允许改措辞）", miss, [])
    # 骨架之外的章：**只报不断言**。模型补章是它的自由度，而"补出来的章在语料里数不出子节"
    # 才是真正要盯的（正文只能写空话）。这里给个可见度，别让它悄悄长回来。
    extra = [t for t in titles
             if not any(chapter_title.related(t, c) or chapter_title.related(c, t) for c in (skel or []))]
    if extra:
        pages_all = load_page_summaries(dom)
        for t in extra:
            k = len(chapter_skeleton(t, pages_all, domain=dom))
            print(f"     [注意] 骨架外的章「{t}」：知识库 {k} 个子节" + ("（无语料支撑）" if not k else ""))
    for rail in ("铺轨", "站房", "路基", "桥梁", "隧道"):
        check(f"结果里没有铁路工程「{rail}」", any(rail in t for t in titles), False)

    # 子节层：拿结果里的一章真跑一次，看知识库骨架有没有跟到目录里
    if titles:
        ch = titles[0]
        pages = load_page_summaries(dom)
        s = chapter_skeleton(ch, pages, domain=dom)
        print(f"     本章「{ch}」知识库骨架 {len(s)} 个子节")
        kids = [k["title"] for k in generate_toc_children(
            ch, inputs["project_info"], inputs["professions"], inputs["tender_requirements"],
            tender_toc=inputs["tender_toc"], skeleton=s)]
        print(f"     模型给出 {len(kids)} 个子节：")
        for k in kids:
            print(f"       - {k}")
        check("子节非空（两级目录接得上）", bool(kids), True)


def main() -> int:
    section_1()
    section_1b()
    section_2()
    section_3()
    section_4()
    if "--live" in sys.argv:
        pid = next((int(a) for a in sys.argv[1:] if a.isdigit()), 4)
        section_5(pid)
    else:
        print("\n5) 实跑低空闭环 —— 跳过（加 --live [project_id] 才跑：会真调模型）")
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""校验「招标要求 → 施工章节」的定位（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_requirement_mapping.py [project_id]

要修的是一个**静默失效**的功能：`tender_requirement.suggested_chapter` 一直存着
「施工方案/管理措施」这类泛称，而消费侧 `requirements_for_chapter` 判的是
「章名整串出现在 suggested_chapter 里」——方向反了，永远不成立。实测 56 章 × 20 条
= 1120 种组合 0 命中，于是每章都掉进类别兜底，**每章注入的是同一批要求**。

检查四件事：
  1. 章节名归一化（去「四、」「第一章」「1.1」这类前缀）；
  2. 匹配方向（`related` 双向包含）——含修复前后的对照；
  3. 抽取侧的章名对齐（`_clean_requirements`：对不上就清空，而不是留个挂不上的泛称）；
  4. 端到端（要连库，**只读**）：跑真实的 `requirements_for_chapter`，报「定位命中 /
     类别兜底 / 不给」三档分布，并断言**章名看不出类别的章不被倾倒评分项**
     （「一、响应函」曾拿到「工程化交付方案 10 分」）。
     加 `--simulate` 才会改数据（DB 还没重抽、建议章节仍是旧泛称时用），跑完恢复。

配套事实（项目 4 重抽后的真实测量，2026-10-15）：
  抽取侧 25 条要求、13 条非空建议章节，**逐字都落在规定目录的 5 个名字内、零越界**；
  消费侧定位命中 2 章、兜底 2 章、其余 52 章一条不给（修兜底之前是 54 章被倾倒同一批 5 条）。
  即**抽取侧已达成，消费侧的覆盖靠语义别名（规定子项 ↔ 树节点）才能再上一档**。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core import chapter_title as ct                             # noqa: E402

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def check_near(label: str, got: float, want: float, tol: float) -> None:
    global ok
    good = abs(got - want) <= tol
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got:.3f}"
          + ("" if good else f" != 期望 {want}±{tol}"))


def main() -> int:
    # ---- 1. 归一化 ----
    print("1) 章节名归一化")
    check("去中文顿号序号", ct.normalize("四、技术方案"), "技术方案")
    check("去「第X章」", ct.normalize("第一章 工程概况"), "工程概况")
    check("去「第X节」", ct.normalize("第七节 局点实飞演示场景支撑方案"), "局点实飞演示场景支撑方案")
    check("去括注式序号", ct.normalize("（一）总体技术路线"), "总体技术路线")
    check("去多级阿拉伯编号", ct.normalize("3.2.1 施工工艺"), "施工工艺")
    check("叠着的前缀一起剥", ct.normalize("第一章 第一节 概述"), "概述")
    check("不误伤正常标题", ct.normalize("ISO9001 质量管理体系"), "ISO9001质量管理体系")
    check("空白归一", ct.normalize("  技术 方案 "), "技术方案")
    # 引号必须剥掉：留着的话「九天智感实景验证」永远包含不进这个章名里
    check("剥掉标题里的引号",
          ct.normalize("第四节 “九天智感”实景验证实施方案"), "九天智感实景验证实施方案")

    # ---- 2. 匹配方向 ----
    print("\n2) 匹配方向（related）")
    # 这一对就是原先永远匹配不上的组合：章名 vs suggested_chapter
    check("带序号的章名 == 不带序号的建议章节",
          ct.related("四、技术方案", "技术方案"), True)
    check("招标要求的概括写法 ⊂ 真实子节名",
          ct.related("第七节 局点实飞演示场景支撑方案", "局点实飞演示场景支撑"), True)
    check("招标要求更概括时也能对上",
          ct.related("第一节 企业营业执照及基本信息", "营业执照"), True)
    check("完全不相干的章不误判", ct.related("四、技术方案", "价格评分"), False)
    check("空的建议章节不匹配", ct.related("四、技术方案", ""), False)
    check("太短的片段不参与包含匹配（防「技术」⊂「技术方案」）",
          ct.related("技术服务", "技术"), False)
    print("     修复前后对照（章名「四、技术方案」/ 建议章节「技术方案」）：")
    t, sc = "四、技术方案", "技术方案"
    print(f"       旧规则 `t in sc`  → {t in sc}  （章名整串出现在泛称里，永远不成立）")
    print(f"       新规则 related()  → {ct.related(t, sc)}")

    # ---- 2b. 措辞变体相似度（二元组重合） ----
    print("\n2b) 措辞变体相似度（similarity）")
    # 该配对的：只差一两个词，双向包含都不成立
    s = ct.similarity("第四节 售后服务体系与保障能力介绍", "项目售后服务与保障能力")
    check_near("「售后服务体系」多一个词也能认出同指", s, 0.700, 0.02)
    check("过阈值", s >= ct.SIM_THRESHOLD, True)
    check("「九天智感」实景验证实施方案 ↔ 项目实施方案 过阈值",
          ct.similarity("第四节 “九天智感”实景验证实施方案", "项目实施方案") >= ct.SIM_THRESHOLD, True)
    # 不该配对的
    for a, b, hi in (("第六节 验收交付物清单符合性对照", "测试验收准备方案", 0.20),
                     ("第一节 总体技术路线与验证体系架构", "工程化交付方案", 0.05),
                     ("第六节 廉洁从业承诺书", "项目售后服务与保障能力", 0.05)):
        got = ct.similarity(a, b)
        check(f"不相干的 {a[:12]}… ↔ {b[:12]}… 在阈值下（{got:.3f} < {hi}）", got < hi, True)
    # 短标题护栏：没有它，「方案」两字就能撑出 0.333 的一簇噪音
    check("短标题不参与（「技术方案」只有 3 个二元组）",
          ct.similarity("第七节 局点实飞演示场景支撑方案", "四、技术方案"), 0.0)
    check("同长度正常标题不受护栏影响",
          ct.similarity("售后服务体系与保障能力介绍", "项目售后服务与保障能力") > 0, True)

    # ---- 2c. 下沉：挂在父章上的要求落到真正对应的子节 ----
    print("\n2c) 下沉（descend）")
    # 树形照着项目 4 的真实目录裁剪：挂错的那 8 条就长这样
    tree = {"found": True, "chapters": [
        {"title": "四、技术方案", "children": [
            {"title": "第一节 总体技术路线与验证体系架构"},
            {"title": "第三节 监视能力全量验证技术方案"},
            {"title": "第四节 “九天智感”实景验证实施方案"},
            {"title": "第五节 高密度大容量运行能力验证方案"},
        ]},
        {"title": "五、公司简介", "children": [
            {"title": "第四节 售后服务体系与保障能力介绍"},
        ]},
    ]}
    check("要求标题是子节名的一部分 → 下沉",
          ct.descend("监视能力全量验证", "四、技术方案", tree),
          "第三节 监视能力全量验证技术方案")
    check("中间插了字也认得出（措辞变体 0.889）",
          ct.descend("高密度大容量能力验证", "四、技术方案", tree),
          "第五节 高密度大容量运行能力验证方案")
    check("引号不挡（“九天智感”）",
          ct.descend("九天智感实景验证", "四、技术方案", tree),
          "第四节 “九天智感”实景验证实施方案")
    check("跨章不动——只在自己那棵子树里找",
          ct.descend("售后服务体系与保障能力介绍", "四、技术方案", tree), "四、技术方案")
    # 这条是阈值那条线：泛称「项目实施方案」↔「“九天智感”…方案」正好 0.600，
    # 不按住的话会被硬塞进第四节，把一条本该概括全章的评分项钉死在某个子节上。
    check("泛称不被硬塞给某个子节（0.600 < 0.65）",
          ct.descend("项目实施方案", "四、技术方案", tree), "四、技术方案")
    check("已经是子节了就不再往下沉",
          ct.descend("监视能力全量验证", "第三节 监视能力全量验证技术方案", tree),
          "第三节 监视能力全量验证技术方案")
    check("叶子章不沉", ct.descend("某某要求", "七、营业执照等公司资质材料", tree),
          "七、营业执照等公司资质材料")
    check("children 不把子节当父章（「技术方案」⊂ 子节名）",
          ct.children(tree, "第三节 监视能力全量验证技术方案"), [])

    # ---- 3. 抽取侧的章名对齐 ----
    print("\n3) 抽取侧对齐（_clean_requirements）")
    from app.agents.tender_agent import _clean_requirements, merge_toc   # noqa: E402

    toc = {"found": True, "chapters": [
        {"title": "四、技术方案", "children": [{"title": "第一节 总体技术路线与验证体系架构"},
                                               {"title": "第七节 局点实飞演示场景支撑方案"}]},
        {"title": "七、营业执照等公司资质材料", "children": []},
    ]}
    check("目录里的章名都被列出来",
          ct.toc_titles(toc), ["四、技术方案", "第一节 总体技术路线与验证体系架构",
                               "第七节 局点实飞演示场景支撑方案", "七、营业执照等公司资质材料"])
    check("对齐到原样写法（模型少写序号也能救回）",
          ct.align("技术方案", ct.toc_titles(toc)), "四、技术方案")
    check("对不上任何真实章节时返回空", ct.align("价格评分", ct.toc_titles(toc)), "")

    def _req(sc: str, title: str) -> dict:
        return {"category": "技术要求", "title": title, "content": "内容",
                "suggested_chapter": sc, "source_location": "x"}

    out = _clean_requirements([_req("技术方案", "甲"), _req("施工方案", "乙")], toc)
    check("模型写了目录里的章名（少序号）→ 对齐保留",
          out[0]["suggested_chapter"], "四、技术方案")
    check("模型写了挂不上的泛称 → 清空（走类别兜底，而不是留个假定位）",
          out[1]["suggested_chapter"], "")
    # 目录 found=false 时不该动模型给的值（此时泛称是唯一可选）
    out2 = _clean_requirements([_req("施工方案", "丙")], {"found": False, "chapters": []})
    check("没有规定目录时不干预", out2[0]["suggested_chapter"], "施工方案")

    # 已有目录（project.toc_hint）并进候选
    # 场景：本次模型把目录抽窄了（只剩 四、技术方案），若不并已有目录，
    # 「工程化交付方案」会被清空——而它正是真实章节树里的章名。
    narrow = {"found": True, "chapters": [{"title": "四、技术方案", "children": []}]}
    hint = {"found": True, "chapters": [{"title": "工程化交付方案", "children": []}]}
    out3 = _clean_requirements([_req("工程化交付方案", "丁")], merge_toc(hint, narrow))
    check("合并已有目录后接住本次抽不到的章名",
          out3[0]["suggested_chapter"], "工程化交付方案")
    out4 = _clean_requirements([_req("工程化交付方案", "戊")], narrow)
    check("不合并时该条被清空（对照）", out4[0]["suggested_chapter"], "")

    # 下沉接进 _clean_requirements（不只纯函数对）——这正是项目 4 那 8 条的形态
    merged = {"found": True, "chapters": [{"title": "四、技术方案", "children": [
        {"title": "第三节 监视能力全量验证技术方案"}]}]}
    out5 = _clean_requirements([_req("四、技术方案", "监视能力全量验证")], merged)
    check("模型填父章 → 落库前就下沉到子节",
          out5[0]["suggested_chapter"], "第三节 监视能力全量验证技术方案")

    # ---- 3a. 已有目录写进提示词（否则模型这轮没抽出目录就给空） ----
    print("\n3a) 已有目录写进提示词 / 兜底章名由调用方按域传（_system_prompt）")
    from app.agents.tender_agent import _system_prompt            # noqa: E402

    p_no = _system_prompt([])      # 不给兜底词表（项目未知，取不到领域）
    p_yes = _system_prompt(["四、技术方案", "第四节 售后服务体系与保障能力介绍"])
    # 注意：指令正文里就会提到这份清单的名字（「上面给了【…】的」），所以不能用它判有无；
    # 用只有清单块才有的那句话。
    _marker = "实际章节树就是照它生成的"
    check("没有已有目录时不出现清单块", _marker in p_no, False)
    check("有已有目录时清单写进去、章名原样在",
          (_marker in p_yes and "第四节 售后服务体系与保障能力介绍" in p_yes), True)
    check("两个占位符都被替换干净",
          "{EXISTING_TOC}" not in p_yes and "{FALLBACK_CHAPTERS}" not in p_yes, True)
    # 提示词里有 {"toc": ...} 这类大括号，用 %/format 拼会炸
    check("JSON 示例的大括号没被当占位符", '{"toc"' in p_yes, True)
    # 兜底词表的**默认值必须是不给**：此前这里自带一份铁路章名表，于是越不像铁路的项目
    # 越必然拿到它（低空服务采购正是：招标只给了整本响应文件格式，抽不出施组目录）。
    check("没传兜底词表时不含任何写死的章名", "编制依据与原则" in p_no, False)
    check("没传兜底词表时仍要求按性质自造泛称", "自造一个泛称章名" in p_no, True)
    p_low = _system_prompt([], "技术指标响应情况/工程化交付方案")
    check("传了兜底词表（按域取）就写进去", "工程化交付方案" in p_low, True)
    from app.agents.requirement_agent import suggested_chapters    # noqa: E402
    # 2026-10-20 起目录必须有当前招标依据，lowalt 的默认章骨架已清空。
    check("lowalt 不再自带默认章骨架（由招标依据决定目录）",
          suggested_chapters("lowalt"), "")
    check("未声明章骨架的域返回空串（不回退别域）", suggested_chapters("softtest"), "")

    # ---- 3c. 冒烟：不打 LLM 跑完整的 extract_tender_all ----
    # 这条是为了抓「提示词拼装里引用了一个没赋值的变量」这类错误——它在真实调用里表现为
    # 一顿 NameError，被 fact_service 的 try/except 吞成一句 lot_msg，表面上就像
    # 「模型返回了空」。实打实栽过一次，所以要有一条免 API 的端到端。
    print("\n3c) 冒烟：extract_tender_all（桩掉 llm.chat）")
    import json as _json                                     # noqa: E402

    import app.agents.tender_agent as ta                      # noqa: E402

    class _FakeLLM:
        def __init__(self, payload):
            self.payload, self.prompts = payload, []

        def chat(self, msgs, **kw):
            self.prompts.append(msgs[0]["content"])
            return _json.dumps(self.payload, ensure_ascii=False)

    fake = _FakeLLM({
        "toc": {"found": False, "note": "", "chapters": []},   # 本次没抽出目录
        "has_lots": False, "lots": [],
        "requirements": [{"category": "技术要求", "title": "甲", "content": "内容",
                          "weight": None, "suggested_chapter": "第四节 售后服务体系与保障能力介绍",
                          "source_location": "x"}],
    })
    # 已有目录**打桩而不是读库**：项目 4 的 `toc_hint` 已随 2026-10-15 的修复清空
    # （重抽后 found=false，那份"投标文件格式"不再是施组目录），拿它当夹具会让这条
    # 断言在被测代码没变的情况下变红。这里要验的是**拼装管路**，夹具自己给。
    _FAKE_HINT = {"found": True, "note": "", "chapters": [{"title": "四、技术方案", "children": [
        {"title": "第四节 售后服务体系与保障能力介绍"}]}]}
    real_llm, real_hint = ta.llm, ta._read_toc_hint
    try:
        ta.llm = fake
        ta._read_toc_hint = lambda pid: _FAKE_HINT
        res = ta.extract_tender_all("（原文）", "f.docx", 4)
    finally:
        ta.llm, ta._read_toc_hint = real_llm, real_hint
    check("跑通了（没有未赋值变量之类）", isinstance(res, dict), True)
    prompt = fake.prompts[0]
    check("已有目录写进了提示词", "【本项目已有的规定目录】" in prompt, True)
    check("提示词里带上了已有目录的章名",
          "第四节 售后服务体系与保障能力介绍" in prompt, True)
    # 本次没抽出目录（found=false），全靠"已有目录"接住
    check("本次没抽出目录时，靠已有目录仍对齐成功",
          res["requirements"][0]["suggested_chapter"], "第四节 售后服务体系与保障能力介绍")

    # ---- 3b. 已有目录不被覆盖（merge_toc） ----
    print("\n3b) 已有目录不被覆盖（merge_toc）")
    old = {"found": True, "note": "旧", "chapters": [
        {"title": "一、响应函", "children": [{"title": "第一节 响应函正文"}]},
        {"title": "四、技术方案", "children": []}]}
    new = {"found": True, "note": "新", "chapters": [
        {"title": "四、技术方案", "children": [{"title": "工程化交付方案"}]},
        {"title": "十、新增章", "children": []}]}
    m = merge_toc(old, new)
    check("已有章一个不丢、新章追加", [c["title"] for c in m["chapters"]],
          ["一、响应函", "四、技术方案", "十、新增章"])
    check("同一章的（related）子节并入而非另起一章",
          [k["title"] for k in m["chapters"][1]["children"]], ["工程化交付方案"])
    check("新目录没抽到目录时，旧的照留",
          len(merge_toc(old, {"found": False, "chapters": []})["chapters"]), 2)
    check("旧目录为空时用新的", [c["title"] for c in merge_toc(None, new)["chapters"]],
          ["四、技术方案", "十、新增章"])
    check("两边都没有 → found=false", merge_toc(None, {"found": False, "chapters": []})["found"], False)
    check("重抽同一份目录不会重复追加",
          len(merge_toc(m, new)["chapters"]), 3)

    # ---- 4. 端到端（连库，只读；--simulate 才写） ----
    nums = [a for a in sys.argv[1:] if a.isdigit()]
    project_id = int(nums[0]) if nums else 4
    print(f"\n4) 端到端（项目 {project_id}）")
    _end_to_end(project_id)

    print("\n结论：" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


def _end_to_end(project_id: int) -> None:
    """跑真实的按章取要求，报「定位命中 / 类别兜底 / 不给」三档的分布。

    **只读**——不改任何数据。加 `--simulate` 才会把每条要求按标题包含匹配重挂一遍
    （用于 DB 还没重抽、suggested_chapter 仍是旧泛称的状态），跑完恢复原值。
    """
    from app.core.database import execute, query
    from app.services.requirement_service import requirements_for_chapter
    from app.services import page_plan_service as ps

    sim = "--simulate" in sys.argv
    rows = query("SELECT id, title, category, suggested_chapter FROM tender_requirement"
                 " WHERE project_id = %s ORDER BY id", (project_id,))
    if not rows:
        print(f"  [--] 跳过：项目 {project_id} 没有招标要求")
        return
    titles = [n["title"] for n in ps._ordered_nodes(project_id)]
    if not titles:
        print(f"  [--] 跳过：项目 {project_id} 没有目录树")
        return
    # 数据是否已重抽过：非空 suggested_chapter 里有能对齐到真实章名的
    aligned_n = sum(1 for r in rows
                    if (r["suggested_chapter"] or "").strip()
                    and ct.align(r["suggested_chapter"], titles))
    print(f"     非空建议章节 {sum(1 for r in rows if (r['suggested_chapter'] or '').strip())}"
          f" 条，其中能落到真实章名上的 {aligned_n} 条")

    original = {r["id"]: r["suggested_chapter"] for r in rows}
    try:
        if sim:
            if aligned_n:
                print("     （数据已对齐，跳过模拟）")
            else:
                hit_n = 0
                for r in rows:
                    hit = ct.align(r["title"], titles)
                    execute("UPDATE tender_requirement SET suggested_chapter = %s WHERE id = %s",
                            (hit or None, r["id"]))
                    hit_n += 1 if hit else 0
                print(f"     （模拟挂章：{hit_n}/{len(rows)} 条挂上）")

        rows = query("SELECT id, title, category, suggested_chapter FROM tender_requirement"
                     " WHERE project_id = %s ORDER BY id", (project_id,))
        buckets: dict[tuple, list[str]] = {}
        for t in titles:
            hs = requirements_for_chapter(project_id, t)
            key = tuple(h["title"] for h in hs)
            buckets.setdefault(key, []).append(t)
        empty = len(buckets.get((), []))
        print(f"     共 {len(titles)} 章 → {len(buckets)} 种结果（{empty} 章一条不给）：")
        for got, chs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            print(f"       {len(chs):>2} 章 → {len(got)} 条: {[g[:14] for g in got] or '（无）'}")
            for c in chs[:3]:
                print(f"            {c}")

        # 兜底回归：章名看不出类别的章不该被倾倒任何要求
        for t in ("一、响应函", "第六节 廉洁从业承诺书", "第一节 企业营业执照及基本信息"):
            if t in titles:
                got = requirements_for_chapter(project_id, t)
                bad = [h["title"] for h in got if h["category"] == "评分办法"]
                check(f"「{t}」不被倾倒评分项", bad, [])
        check("各章结果不再完全雷同", len(buckets) > 1, True)
    finally:
        if sim and not aligned_n:
            for rid, sc in original.items():
                execute("UPDATE tender_requirement SET suggested_chapter = %s WHERE id = %s",
                        (sc, rid))
            print("     （已恢复原 suggested_chapter）")


if __name__ == "__main__":
    sys.exit(main())

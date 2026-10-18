"""校验「篇幅控制」（目标正文页数 → 各章目标字数）的算法与接线（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_page_plan.py [project_id]

检查六件事：
  1. 字数口径（count_words）：CJK 逐字 + 中文标点 + 西文词，且先剥掉 md/HTML 标记；
  2. **几何 vs 实测**：解析算出的密度比值，要和实测页数比值对得上——
     这是整个系数可信的根基，也是最不容易自欺的一条（对比的双方互相独立）；
  3. 系数估算：代入两个真实排版，估算的「字/页」要与实测反推值差 <5%。
     **两边都取冻结值，这条不读库**——它问的是「系数模型对不对」，不是「现在库里有多少字」；
  4. 权重与分摊：重章多分、人工额度不被归一化冲掉、预算吃光不注入负数；
  5. from_dict 双轨（库里的坏值不炸生成 / PUT 的坏值当场报错）；
  6. 接线（要连库）：没设目标时 target_words_map 为空（生成侧零开销），
     设了目标后每章额度之和 ≈ 目标总字数，且用完即恢复原状。

第 2 条的实测数据来自第 1 轮：项目 4 **同一份正文**，默认档导出 151 页、
改「三号 + 2 倍行距 + 窄边距」后 249 页（见 docs/前端页面设计说明书.md:150）。

那份正文是 **86830 字（本系统口径，即 `count_words`）**，与 151/249 是一组数——
见下面 `MEASURED_WORDS` 的注释，三个数**必须同时采自同一份正文**。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core import page_plan as pp                                  # noqa: E402
from app.core.docx_style import from_dict as style_from_dict          # noqa: E402

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def check_near(label: str, got: float, want: float, tol: float) -> None:
    """相对偏差在 tol 以内算过。"""
    global ok
    good = want != 0 and abs(got - want) / abs(want) <= tol
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got:.1f} vs {want:.1f}"
          f"（偏差 {(got - want) / want * 100:+.1f}%，容差 ±{tol:.0%}）")


# ---- 实测基准（第 1 轮）：**一份正文 + 它的两种排版页数**，三者是一组 ----
#
# 为什么字数要跟着页数一起冻在这里，而不是从库里现读（2026-10-16 修）：
# 第 3 节验的是「系数模型准不准」，它的两边是**估算**（只吃排版，纯几何函数）与
# **实测反推**（那次实测的字数 ÷ 那次实测的页数）。上一版只冻了页数、字数现读，
# 于是正文一重新生成（项目 4 现在 47495 字，基准是 86830 字），
# 这条就变成「今天的字数 ÷ 那天的页数」，报出 +85% 的**假偏差**。
# 后果比一条红断言严重得多：**红灯常亮 = 没有红灯**。以后真有人把
# `page_density` 或 `DEFAULT_FILL_FACTOR` 改坏，屏幕上还是那两行 `[!!]`，
# 跟老毛病一模一样，谁也不会多看一眼——这个最不该失守的数就成了没有护栏的。
#
# 口径也要对齐：这个字数是**本系统口径**（`count_words`），不是 Word 的 90283。
# 两者差 2.7%，而系数换算的两端（目标总字数、每章【篇幅要求】）走的就是
# `count_words`，拿 Word 的数当分母就是另一处同款错配。
MEASURED_WORDS = 86830
MEASURED_PAGES = {"默认档（宋体小四 1.5 倍，普通边距）": 151,
                  "三号 2 倍行距，窄边距": 249}


def main() -> int:
    # ---- 1. 字数口径 ----
    print("1) 字数口径（count_words）")
    check("四个汉字", pp.count_words("技术方案"), 4)
    check("中文标点也计", pp.count_words("安全，质量。"), 6)          # 4 字 + ，。
    check("西文按词计（不是按字母）", pp.count_words("ISO 9001 quality"), 3)
    check("数字串算一个词", pp.count_words("2026-10-15"), 1)          # 连字符按一个词符
    check("HTML 标签不计", pp.count_words("<p>正文</p>"), 2)
    check("代码块不计", pp.count_words("```\nSELECT 1 FROM t\n```"), 0)
    check("mermaid 源图不计", pp.count_words('<div data-code="graph TD;A-->B"></div>'), 0)
    check("图片语法不计", pp.count_words("![示意图](a.png)正文"), 2)
    check("段落数不虚增", pp.count_words("第一段\n\n第二段"), 6)
    check("空值安全", (pp.count_words(""), pp.count_words(None)), (0, 0))
    check("content_words 认 JSON 字符串", pp.content_words('{"body": "技术方案"}'), 4)
    check("content_words 认 dict", pp.content_words({"body": "技术方案"}), 4)
    check("content_words 坏 JSON 记 0 不抛错", pp.content_words("{oops"), 0)

    # ---- 2. 几何 vs 实测（核心：两边独立，对不上说明系数模型是错的） ----
    print("\n2) 几何密度比值 vs 实测页数比值")
    default = style_from_dict({})
    narrow3 = style_from_dict({
        "body_size_pt": 16, "line_spacing": 2.0,
        "margin_top_cm": 1.27, "margin_bottom_cm": 1.27,
        "margin_left_cm": 1.27, "margin_right_cm": 1.27,
    })
    d_default = pp.page_density(default)
    d_narrow3 = pp.page_density(narrow3)
    print(f"     理论满版密度：默认档 {d_default:.0f} 字/页，窄边距三号档 {d_narrow3:.0f} 字/页")
    pages = list(MEASURED_PAGES.values())
    check_near("密度之比 == 页数之比", d_default / d_narrow3, pages[1] / pages[0], 0.05)

    # ---- 3. 系数估算 vs 实测反推 ----
    # 两边都是**冻结值**：估算来自纯几何函数（只吃排版），反推来自那次实测的字数与页数。
    # 刻意不读库：正文重生成是常态，而「系数模型准不准」与库里现在有多少字无关。
    # 读库只会让这条检查随正文变动假红——见 MEASURED_WORDS 的注释。
    print("\n3) 系数估算（乘默认填充率）vs 实测反推的字/页")
    print(f"     默认填充率 DEFAULT_FILL_FACTOR = {pp.DEFAULT_FILL_FACTOR}")
    print(f"     实测基准：{MEASURED_WORDS} 字（本系统口径）÷ 各档页数")
    for (name, n_pages), style in zip(MEASURED_PAGES.items(), (default, narrow3)):
        dens = pp.page_density(style)
        measured = MEASURED_WORDS / n_pages
        print(f"       {name}：实测反推 {measured:.0f} 字/页 → 反推填充率 {measured / dens:.3f}")
        check_near(f"{name} 的估算字/页", pp.estimate_chars_per_page(style), measured, 0.05)
    # 现读库**只报一句、不判分**：库里字数变了不代表系数错了（上表仍在验模型），
    # 但意味着「想换基准得重新导出实测」，这件事得让人看得见。
    _report_corpus_drift()

    # ---- 4. 权重与分摊 ----
    print("\n4) 章节权重与分摊")
    w_l1 = pp.chapter_weight("第一章 编制依据", 1, False)
    w_l1_container = pp.chapter_weight("第二章 施工方案", 1, True)
    check("有子章节的壳被折扣", round(w_l1_container / pp.chapter_weight("第二章 施工方案", 1, False),
                                    2), pp.CONTAINER_FACTOR)
    check("「编制依据」这类引言章被调低",
          pp.chapter_weight("第一章 编制依据", 1, False) < pp.chapter_weight("第一章 质量管理体系", 1, False),
          True)
    check("层级越深权重越低", pp.LEVEL_WEIGHT[0] > pp.LEVEL_WEIGHT[1] > pp.LEVEL_WEIGHT[2] > pp.LEVEL_WEIGHT[3],
          True)
    check("超深层级取下限而不是崩", pp.chapter_weight("x", 9, False), pp.LEVEL_WEIGHT[-1])

    # 父章继承：技术核心只写在父章标题上，子节必须能继承
    # （低空标实测踩过的坑：56 个标题里组1、组2 一个都没命中，「四、技术方案」的
    #   7 个子节全是「…技术路线」「…验证方案」，一个都命不中）
    tf_tech = pp.type_factor("四、技术方案", None)
    check("「技术方案」命中技术组", tf_tech, 1.6)
    # 补的词让大部分技术子节**自己**就命中了，不靠继承：
    check("「…技术路线…」自己也命中了", pp.type_factor("总体技术路线与验证体系架构", None), 1.6)
    check("「…验证方案」自己也命中了", pp.type_factor("新飞机采购全量指令验证方案", None), 1.6)
    # 继承管的是**自己命不中**的那部分（「第七节 局点实飞演示场景支撑方案」这类）：
    check("自己命不中时继承父章倍率（×0.9）",
          round(pp.type_factor("第一节 组织架构与职责分工", tf_tech), 4),
          round(1.6 * pp.INHERIT_FACTOR, 4))
    check("自己命中的优先于继承",
          pp.type_factor("第一节 编制依据与响应原则", tf_tech), 0.6)
    check("祖先链无命中时保持中性 1.0（没主张 ≠ 该降权）",
          pp.type_factor("纳税及社会保障资金缴纳证明", None), 1.0)
    # 链值必须区分「没命中」(None) 与「倍率就是 1.0」：中性父章若把 1.0 当命中传下去，
    # 子节会被凭空打 0.9 折
    check("整条链没命中时链值是 None 而不是 1.0",
          pp.type_chain("纳税及社会保障资金缴纳证明", None), None)
    check("中性父章的子节链值也是 None（不被凭空打折）",
          pp.type_chain("纳税及社会保障资金缴纳证明",
                        pp.type_chain("七、营业执照等公司资质材料", None)), None)
    check("多级逐层衰减",
          round(pp.type_factor("三级小节", tf_tech * pp.INHERIT_FACTOR), 4),
          round(1.6 * pp.INHERIT_FACTOR ** 2, 4))
    check("引言类父章的降权也照样继承",
          round(pp.type_factor("公司基本情况与资质荣誉",
                               pp.type_factor("五、公司简介", None)), 4),
          round(0.6 * pp.INHERIT_FACTOR, 4))
    check("层级与继承共同作用",
          round(pp.chapter_weight("第一节 组织架构与职责分工", 2, False, tf_tech), 2),
          round(2.0 * 1.6 * pp.INHERIT_FACTOR, 2))

    nodes = [
        {"id": 1, "title": "第一章 工程概况", "level": 1, "has_children": False, "override": None},
        {"id": 2, "title": "第二章 施工方案", "level": 1, "has_children": False, "override": None},
        {"id": 3, "title": "第三章 质量保证措施", "level": 1, "has_children": False, "override": None},
    ]
    alloc = pp.allocate(nodes, 30000)
    check("每章都有额度", sorted(alloc) == [1, 2, 3], True)
    check("「施工方案」分得比「工程概况」多", alloc[2] > alloc[1], True)
    check("「质量保证措施」也多于「工程概况」", alloc[3] > alloc[1], True)
    check_near("总额不超预算", sum(alloc.values()), 30000, 0.001)

    # 人工额度：先扣减，不被归一化冲掉
    nodes[0]["override"] = 5000
    alloc = pp.allocate(nodes, 30000)
    check("人工额度原样保留", alloc[1], 5000)
    check_near("其余按权重摊剩下的 25000", alloc[2] + alloc[3], 25000, 0.001)
    check("总额 ≈ 30000", abs(sum(alloc.values()) - 30000) <= 2, True)

    # 人工额度吃光预算 → 其余章不注入（而不是给 0 或负数）
    nodes[0]["override"] = 40000
    alloc = pp.allocate(nodes, 30000)
    check("预算被吃光时其余章不注入", sorted(alloc), [1])

    check("没设目标页数 → 空字典", pp.allocate(nodes, None), {})
    check("空目录 → 空字典", pp.allocate([], 30000), {})

    # ---- 5. from_dict 双轨 ----
    print("\n5) from_dict 双轨（读库宽松 / PUT 严格）")
    check("空 → 默认", pp.from_dict(None), pp.DEFAULT)
    check("坏 JSON 非 strict 回默认", pp.from_dict("{oops"), pp.DEFAULT)
    check("JSON 字符串能解析", pp.from_dict('{"target_pages": 120}').target_pages, 120)
    check("非 strict 越界记 None", pp.from_dict({"target_pages": 99999}).target_pages, None)
    check("非 strict 非数字记 None", pp.from_dict({"target_pages": "很多"}).target_pages, None)
    check("空串 = 不设目标", pp.from_dict({"target_pages": ""}).target_pages, None)
    check("字符串数字能转", pp.from_dict({"target_pages": "120"}).target_pages, 120)
    for raw, hint in (({"target_pages": 0}, "1–2000"), ({"target_pages": "很多"}, "数字"),
                      ({"rewrite_tolerance": 0.5}, "阈值")):
        try:
            pp.from_dict(raw, strict=True)
            check(f"strict {raw} 应报错", "没报错", "ValueError")
        except ValueError as e:
            check(f"strict {raw} 报「{hint}」", hint in str(e), True)
    check("等于默认就存 NULL（不落库）",
          pp.from_dict({"target_pages": None}).to_dict(), pp.DEFAULT.to_dict())

    # ---- 6. 接线（连库） ----
    project_id = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    print(f"\n6) 接线检查（项目 {project_id}）")
    _wiring(project_id)

    print("\n结论：" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


def _corpus_words() -> int | None:
    """项目 4 正文按本口径的实测字数；连不上库返回 None。"""
    try:
        from app.core.database import query
        rows = query(
            "SELECT sc.content FROM section_content sc"
            " JOIN chapter_node cn ON cn.id = sc.chapter_node_id WHERE cn.project_id = 4"
        )
        return sum(pp.content_words(r["content"]) for r in rows)
    except Exception:
        return None


def _report_corpus_drift() -> None:
    """报一句：库里现有字数与冻结基准差多少。**只提示，不判分**。

    它存在的理由：基准是「某一份正文」测出来的，正文换了基准就该重测。
    但这属于**该不该更新基准**的问题，不是**系数对不对**的问题——
    混成一个断言，就是上一版把守门人变成常亮红灯的那一步。
    """
    corpus = _corpus_words()
    if corpus is None:
        print("     （连不上库，跳过字数漂移提示）")
        return
    drift = (corpus - MEASURED_WORDS) / MEASURED_WORDS
    msg = ("基准仍与库中正文一致，可直接沿用"
           if abs(drift) <= 0.02 else
           "正文已重新生成过——上表照旧在验系数模型，但**要换基准须重新导出实测页数**"
           "（本机用 WPS/Word COM，见 tests/verify_docx_word.py）")
    print(f"     提示：项目 4 正文现在 {corpus} 字，与冻结基准 86830 字差 {drift * 100:+.0f}%——{msg}")


def _wiring(project_id: int) -> None:
    """没设目标 → 生成侧零开销；设了目标 → 额度总和 ≈ 目标总字数；跑完恢复原状。"""
    from app.core.database import query_one
    from app.services import page_plan_service

    before = query_one("SELECT page_plan FROM project WHERE id = %s", (project_id,))
    if not before:
        print(f"  [--] 跳过：项目 {project_id} 不存在")
        return
    try:
        pp.save_project_plan(project_id, pp.DEFAULT)
        check("未设目标时目标字数表为空（生成时零注入）",
              page_plan_service.target_words_map(project_id), {})

        pp.save_project_plan(project_id, pp.PagePlan(target_pages=120))
        view = page_plan_service.build_page_plan_view(project_id)
        coeff = view["coefficient"]
        print(f"     系数 {coeff['value']} 字/页（来源 {coeff['source']}，密度 {coeff['density']}）"
              f" → 目标总字数 {view['target_words_total']}")
        check("系数来源如实标注为估算", coeff["source"], "estimated")
        check("目标总字数 = 页数 × 系数", view["target_words_total"], 120 * coeff["value"])

        targets = page_plan_service.target_words_map(project_id)
        check("每章都拿到了额度", len(targets) == view["stats"]["nodes"], True)
        check_near("额度之和 ≈ 目标总字数", sum(targets.values()), view["target_words_total"], 0.02)
        check("面板节点带权重与偏差字段",
              all({"weight", "target_words", "words", "deviation"} <= set(n) for n in view["nodes"]), True)

        # 真实目录上的效果：技术核心章的子节必须显著重于资质复印件章的子节。
        # （这是修继承之前最直观的错——两者曾经都是 2.00。）
        by_title = {n["title"]: n for n in view["nodes"]}
        tech = by_title.get("第一节 总体技术路线与验证体系架构")
        qual = by_title.get("第五节 纳税及社会保障资金缴纳证明")
        prof = by_title.get("第一节 公司基本情况与资质荣誉")
        if not (tech and qual and prof):
            print(f"  [--] 跳过真实目录断言：项目 {project_id} 的章节名与预期不符")
        else:
            check("技术核心子节权重 3.20", tech["weight"], 3.2)
            # 这条同时是「中性父章不得给子节凭空打 0.9 折」的回归检查
            # （type_chain 返回 None 而不是 1.0，否则这里会变成 1.80）
            check("中性父章的子节仍是 2.00（不是 1.80）", qual["weight"], 2.0)
            check("公司简介子节继承降权 1.08", prof["weight"], 1.08)
            check("技术核心子节确实重于资质子节", tech["weight"] > qual["weight"], True)
            # 「第七节 局点实飞演示场景支撑方案」自己命不中任何关键词，只能靠继承拿到父章的 1.6
            inh = by_title.get("第七节 局点实飞演示场景支撑方案")
            if inh:
                check("靠继承拿到 2.88 的子节", inh["weight"], 2.88)
                check("继承来的倍率被标记", (inh["inherited"], qual["inherited"]), (True, False))
                check("自己命中的不算继承", tech["inherited"], False)

            # 按一级章汇总：技术方案家族应明显高于营业执照家族（曾经只差 15%）
            fam, cur = {}, None
            for n in view["nodes"]:
                if n["level"] == 1:
                    cur = n["title"]
                fam.setdefault(cur, 0)
                fam[cur] += n["target_words"] or 0
            t_key = next((k for k in fam if "技术方案" in k), None)
            q_key = next((k for k in fam if "营业执照" in k), None)
            if t_key and q_key:
                print(f"     {t_key} {fam[t_key]} 字 vs {q_key} {fam[q_key]} 字"
                      f"（{fam[t_key] / fam[q_key]:.2f} 倍）")
                check("技术方案家族至少是营业执照家族的 1.5 倍",
                      fam[t_key] / fam[q_key] >= 1.5, True)

        # 人工额度：只认本项目节点，非法 id 忽略
        first = view["nodes"][0]["id"]
        n = page_plan_service.save_page_overrides(project_id, {str(first): 3000, "99999999": 5000})
        check("只写入本项目真实节点", n, 1)
        check("人工额度落库", page_plan_service.target_words_map(project_id)[first], 3000)
        # 清空 → 回落自动分摊
        page_plan_service.save_page_overrides(project_id, {str(first): None})
        check("清空人工额度后回落自动分摊",
              query_one("SELECT target_words FROM chapter_node WHERE id = %s", (first,))["target_words"],
              None)
    finally:
        row = before or {}
        pp.save_project_plan(project_id, pp.from_dict(row.get("page_plan")))
        print("     （已恢复原篇幅设置）")


if __name__ == "__main__":
    sys.exit(main())

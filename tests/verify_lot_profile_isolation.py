"""校验「标段档案」这条链按领域隔离（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_lot_profile_isolation.py

背景（2026-10-15）：`lot_profile_service` 里写死了**整张铁路工程 schema**——
`mileage_ranges`（DK 里程）、`works`（路基/桥梁/隧道/箱梁预制架设/联络线/铺轨/邻近既有线），
以及提示词里的「特大桥、隧道、车站、枢纽」「梁场、拌和站、盾构管片场、铺轨基地」。
模块级常量、读的时候不看 domain，与 `SKELETON_CHAPTERS`（章级骨架）是同一种毛病：
**一个被误认为领域无关的领域常量**。任何项目一建标段档案，就在提示词里领一份铁路字段表，
而抽出来的词还会经 `whitelist_terms` 变成"正文只许写这些"的硬边界。

修法：整张表挂到 `Domain.lot_fields`（key/label/hint/kind 四件套）。
**本域没声明字段（空元组）＝本域没有标段这一层次**——不抽取、不注入、不用它裁正文。
低空与软件测试都声明空：低空那份采购文件全文「标段」出现 0 次（实测），
采购范围是一个项目、一个交付地点、30 日历天交货，确实没有标段划分。

检查：
  1. 注册表：两个域都没声明标段字段；`get_domain` 不跨域兜底；
  2. 空表 ⇒ 整条链不参与：抽取提示词一次都不发、profile_text/lot_brief/索引都为空；
  3. 提示词：`_fill_system` / `_parse_system` 里一个铁路词都没有；
  4. 机制本身可用：拿一个**临时**领域（用完即删）声明一组字段，验证
     key→label→渲染、kind 决定白名单、JSON 骨架形状、已知信息回述都按声明走；
  5. 源码里不许再出现那张老字段表（防止有人再捡回来）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

ok = True

#: 这张表里的词，一个都不许出现在给模型看的标段档案提示词里
RAIL_WORDS = ("路基", "桥梁", "隧道", "箱梁", "铺轨", "联络线", "梁场", "拌和站",
              "盾构", "管片", "弃土", "弃渣", "里程", "特大桥", "站场", "枢纽",
              "DK", "邻近既有线", "无砟轨道")


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


# ---------------------------------------------------------------- 1. 注册表
def section_1() -> None:
    print("\n1) 领域注册表：标段档案字段按域声明，没声明就是空")
    from app.core.domains import DOMAINS, list_domains, lot_fields_of

    check("lowalt 没声明标段字段", lot_fields_of("lowalt"), ())
    check("softtest 没声明标段字段", lot_fields_of("softtest"), ())
    check("没声明 ≠ 报错（拿得到空元组）", isinstance(lot_fields_of("lowalt"), tuple), True)
    check("list_domains 带出 lot_fields",
          all("lot_fields" in d for d in list_domains()), True)
    check("list_domains 里两个域都是空表",
          [d["lot_fields"] for d in list_domains()], [[], []])
    # 别的域声明了不该影响本域
    check("railway 域确实已删（没有别的域可借）", "railway" in DOMAINS, False)


# ---------------------------------------------------------------- 2. 空表 ⇒ 不参与
def section_2() -> None:
    print("\n2) 本域没声明字段 ⇒ 整条链不参与（而不是「抽一个空档案」）")
    from app.services import lot_profile_service as LP

    # 最要紧的一条：**一次 LLM 调用都不该发出去**
    called = []

    def _boom(*a, **k):
        called.append(1)
        raise AssertionError("本域没声明标段字段，却发出了抽取调用")

    real = LP.llm.chat
    try:
        LP.llm.chat = _boom
        LP._fill_by_llm({"lot_code": "X-1", "lot_name": "甲"}, [{"file": "f", "text": "原文" * 50}],
                        "lowalt")
        LP.parse_profile_text("里程范围：DK1+000～DK2+000", "lowalt")
    finally:
        LP.llm.chat = real
    check("抽取/解析都不发 LLM 调用", called, [])

    # 各渲染口都返回空串（调用方据此整段不注入，而不是注入一个空壳标题）
    check("profile_text 返回空", LP.profile_text({"major_structures": ["甲桥"]}, "lowalt"), "")
    check("parse_profile_text 返回空", LP.parse_profile_text("随便写点什么", "lowalt"), {})
    check("whitelist_terms 返回空",
          LP.whitelist_terms({"major_structures": ["甲桥"], "temp_facilities": ["乙梁场"]}, "lowalt"), [])
    check("_archive_keys 为空", LP._archive_keys("lowalt"), ())
    check("_milestone_key 为空", LP._milestone_key("lowalt"), None)
    # 已知信息回述：没声明字段就不该回述任何东西（原先这里写死里程/构造物两行）
    check("_known_block 不吐任何字段",
          LP._known_block({"mileage_ranges": ["DK1+000"], "major_structures": ["甲桥"]}, "lowalt"), "")

    # 真库上跑一遍：项目 4 是低空、没有标段 → 全空
    check("lot_brief(项目4) 为空", LP.lot_brief(4), "")
    from app.services.lot_scope_service import build_lot_index

    check("build_lot_index(项目4) 为空表", build_lot_index(4), [])


# ---------------------------------------------------------------- 3. 提示词
def section_3() -> None:
    print("\n3) 提示词：一个铁路词都没有")
    from app.services import lot_profile_service as LP

    # 本域（空字段表）能发出的提示词只有这两个
    prompts = {"FILL": LP._fill_system(()), "PARSE": LP._parse_system(())}
    for name, text in prompts.items():
        check(f"{name} 不含铁路词",
              [w for w in RAIL_WORDS if w in text], [])
    # 整段模板里也不许有（这是被 .replace 拼进去之前的底稿）
    check("FILL_SYSTEM 模板本身干净",
          [w for w in RAIL_WORDS if w in LP.FILL_SYSTEM], [])
    check("PARSE_SYSTEM 模板本身干净",
          [w for w in RAIL_WORDS if w in LP.PARSE_SYSTEM], [])
    check("旧字段表已删（works 的键一个不剩）",
          [w for w in ("box_girder_spans", "link_line_km", "track_km",
                       "adjacent_existing_line", "roadbed") if w in LP.__doc__], [])


# ---------------------------------------------------------------- 4. 机制可用
def _with_temp_domain(fn) -> None:
    """临时往注册表里塞一个声明了标段字段的领域，跑完删掉。

    **故意不让 lowalt 声明假字段来测试**——那等于往真实数据里塞领域先验。
    机制本身要能跑，用一次性领域验。
    """
    from app.core import domains as D

    key = "__verify_scope__"
    D.DOMAINS[key] = D.Domain(
        key=key, name="回归验证用（临时）", tags=(),
        # genre 是必填的（见 DocGenre）：新领域必须自己声明成稿叫什么，
        # 这里是一次性临时域，随便给一个不影响的词。
        genre=D.DocGenre(full="验证用文件", short="验证用文件", bid="验证用文件"),
        lot_fields=(
            D.LotField("zones", "作业区域", "该标段的作业区域名称（用原文名称）"),
            D.LotField("spans", "区间", "该标段的起止区间，保留原文写法", kind="ranges"),
            D.LotField("milestones", "关键节点", "该标段的关键节点",
                       kind="milestones"),
        ),
    )
    try:
        fn(key, D)
    finally:
        D.DOMAINS.pop(key, None)


def section_4() -> None:
    print("\n4) 机制本身可用：声明了字段就按声明走")

    def body(key, D):
        from app.services import lot_profile_service as LP

        fields = D.lot_fields_of(key)
        check("_archive_keys 取声明顺序", LP._archive_keys(key), ("zones", "spans", "milestones"))
        check("_milestone_key 按 kind 认出里程碑", LP._milestone_key(key), "milestones")

        # JSON 骨架：值形态由 kind 决定，不靠 hint
        shape = LP._json_shape(fields)
        check("骨架里有 zones 的数组占位", '"zones": ["…"]' in shape, True)
        check("骨架里里程碑是对象数组", '"milestones": [{"name": "", "days": 0' in shape, True)

        # 提示词只讲声明的字段，且带上各自的 hint
        fill = LP._fill_system(fields)
        check("提示词含本域字段说明", "该标段的作业区域名称" in fill and "该标段的起止区间" in fill, True)
        check("提示词没有别的域的字段", "里程范围" in fill or "major_structures" in fill, False)

        # 渲染：key → label，区间类不进白名单
        p = {"zones": ["一号试验场"], "spans": ["A～B"], "milestones": [{"name": "初验", "days": 30}]}
        txt = LP.profile_text(p, key)
        check("按 label 渲染", txt.splitlines()[0], "作业区域：一号试验场")
        check("里程碑只渲染名字", "关键节点：初验" in txt, True)
        check("末尾带上「未列出即不含」的断言", txt.endswith(LP._EMPTY_TAIL), True)
        check("区间类不进白名单", LP.whitelist_terms(p, key), ["一号试验场"])
        check("_known_block 只回述声明字段",
              LP._known_block({"zones": ["甲"], "spans": ["A～B"]}, key),
              "该标段作业区域：甲\n该标段区间：A～B\n")

    _with_temp_domain(body)

    # 用完必须删干净：否则它会被 list_domains 带出去，前端多出一个假领域
    from app.core.domains import DOMAINS, list_domains

    check("临时领域已移除", "__verify_scope__" in DOMAINS, False)
    check("前端拿到的领域仍只有两个", [d["key"] for d in list_domains()], ["lowalt", "softtest"])


# ---------------------------------------------------------------- 5. 源码守卫
def section_5() -> None:
    print("\n5) 源码里不许再出现那张老字段表")
    from app.services import lot_profile_service as LP

    src = inspect.getsource(LP)
    # 注释里提「里程」是可以的（讲清历史），但**代码里**不该再有点名读某个字段
    for bad in ('get("mileage_ranges")', 'get("major_structures")', 'get("temp_facilities")',
                'p.get("works")', '"roadbed"', '"track_km"'):
        check(f"源码里没有写死的 {bad}", bad in src, False)
    check("模块 docstring 已改成按域声明", "由领域声明" in (LP.__doc__ or ""), True)


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

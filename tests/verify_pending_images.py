"""校验「配图判据只有一个来源」与「导出侧那套分类词表跟着判据走」（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_pending_images.py

背景（2026-10-16）：用户问「没有可以参考的图，能不能根据图片占位名称和正文生成图」。
量完 202 条有图名的 image 条目：**结构/示意类 133 条**（故障分级处理流程图、服务模式图、
应对策略图这类，本该由写手按规则 9 画成 mermaid）、**实证类 69 条**（第三方CNAS检测证明、
合同扫描件、实飞现场照片、界面截图这类，只能找真件，AI 画一张就是伪造投标材料）。
这两个数是**导出侧那份词表**数出来的，也是第 5 节每次跑都会重算一遍的数。
早期拿一套更窄的词表量过（88 : 114），只是用来确认"结构类占多数"这个结论，
**别拿它当数**——词表一变比例就变，「结构类占多数」这条才是要守的性质。

翻车点不在"缺一条规则"，在**规则有三份、还互相不一致**：

| 位置 | 旧枚举 |
|---|---|
| 规则 9 开头 | 流程、层级、**组织**、时序、**因果**、时间进度、**对比关系** |
| 规则 5 | 流程、层级、关系、时间进度、逻辑判定 |
| 【建议图片】段 | 流程/层级/关系/时序/进度/逻辑判定 |

`组织架构图` 在规则 9 里该画、在规则 5 里落到"其余 → 放占位"，于是从缝里漏成 [图片占位]；
三份还都漏掉了语料里真实存在的 `服务模式图` / `应对策略图` / `标准化维度图` / `思维导图`。
改法：判据收进 `writer_agent._IMG_SOURCE_RULE` 一处（**按"内容从哪来"判，枚举只当例子**），
规则 5 / 规则 9(4) / 【建议图片】段都插它。

检查（每条反面都配一条正面）：
  1. 提示词契约：常量在 `WRITER_SYSTEM` 里**恰好一份**、旧枚举没死灰复燃、规则 9(4) 指向它；
  2. 【建议图片】段也插同一份（**夹具必须带 url**，否则那一段整块不出现，断言会假绿）；
  3. 分类词表同源：判据里列的例词，导出侧 `_STRUCTURAL_RE/_EVIDENCE_RE` 必须全都认得；
  4. `_pending_kind` 在真实占位名上的判定：项目 4 现存 8 处全部是实证类；
  5. 真实语料：结构类 vs 实证类的条数（配正向断言，否则库一空就假绿）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.writer_agent import (                                    # noqa: E402
    WRITER_SYSTEM,
    _IMG_SOURCE_RULE,
    _build_context,
)
from app.core.database import query                                       # noqa: E402
from app.services.docx_export import (                                    # noqa: E402
    _EVIDENCE_RE,
    _STRUCTURAL_RE,
    _pending_kind,
)
from app.services.image_ownership import image_name                       # noqa: E402

ok = True

#: 判据里列的例词，**逐字抄自 `_IMG_SOURCE_RULE`**。第 3 节先断常量里确实有这些词
#: （常量改了、这份词表没跟上，守卫会先在那里红），再断导出侧词表认得它们——
#: 这就是"给模型看的散文"与"给程序用的正则"同源这条接线的钉子。
STRUCT_WORDS = ["流程", "层级", "组织", "时序", "因果", "进度", "逻辑判定", "对比关系",
                "结构", "分类", "模式", "方案", "方法", "规划"]
EVIDENCE_WORDS = ["现场照片", "实物图", "界面截图", "仪器仪表图", "扫描件", "证照",
                  "检测报告", "客户提供的材料", "实测数据"]

#: 项目 4 存稿里的 8 处占位（`#282` 2 / `#283` 3 / `#287` 1 / `#294` 2）。**全是实证类**——
#: 写手侧那半在本机今天一张都修不到，这 8 处的终态就是"红字 + 灰字 + 清单里一条"。
REAL_PENDING = [
    "API网关管理界面配置截图", "标准接口文档摘录（Swagger/Postman）",
    "飞行器姿态抗扰动数据曲线图", "实质性佐证材料示例集",
    "第三方CNAS环境适应性检测证明", "无人机实飞测试现场",
    "缺陷管理系统操作界面", "缺陷收敛曲线示意图",
]

#: 语料里真实存在的结构类图名，每一个都该判成"需补图"（写手侧修好后它们根本不该成占位）。
STRUCTURAL_NAMES = [
    "故障分级处理流程图", "定制化开发项目服务模式图", "风险识别与应对策略图",
    "测试用例标准化维度图", "培训对象分类及目标图", "三维课程架构学时占比",
    "研发测试验证流程图", "异常分级处置流程图", "缺陷闭环整改流程示意图",
    # 陷阱：名字里带「材料」，但它是结构类——结构词必须优先
    "材料进场流程图",
]


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


# ---------------------------------------------------------------- 1. 提示词契约
def section_1() -> None:
    print("\n1) 提示词契约：判据只有一份，旧枚举不许回来")
    check("（正面）判据常量插进了系统提示词",
          WRITER_SYSTEM.count(_IMG_SOURCE_RULE), 1)
    check("（正面）判据点名了「内容从哪来」这个判法",
          "判据是这张图的内容从哪来" in _IMG_SOURCE_RULE, True)
    check("（正面）判不准时给了明确的退路（按占位）",
          "判不准就按占位" in _IMG_SOURCE_RULE, True)

    # 老枚举死灰复燃断言（"不该出现"，靠上面两条正面断言保证不是假绿）
    check("（反面）规则 5 的老措辞没了",
          "其余（现场照片、实物图、界面截图" in WRITER_SYSTEM, False)
    check("（反面）【建议图片】段的老枚举没了",
          "流程/层级/关系/时序/进度/逻辑判定类" in WRITER_SYSTEM, False)
    # 规则 9(4) 不再自带枚举，而是指向规则 5 的判据——**两份枚举分家就是这个 bug 的病根**
    check("（正面）规则 9(4) 指向规则 5 的判据",
          "规则 5 那条判据里" in WRITER_SYSTEM, True)


# ---------------------------------------------------------------- 2. 【建议图片】段
def section_2() -> None:
    """⚠ 夹具**必须带 url**：没有 url 的条目是"空壳"，会被挡在【建议图片】外，
    那一段整块不出现，下面这条正面断言就成了假绿（`verify_image_naming.py` 踩过这个坑）。"""
    print("\n2) 【建议图片】段插的是同一份判据")
    img = {"caption": "系统总体架构图", "content": "整体结构",
           "url": "/uploads/parsed/_verify_pending/a.png"}
    ctx = _build_context({"title": "第一节 系统设计"}, [], [{"images": [img]}], [],
                         domain="lowalt")
    check("（正面）建议段在（夹具带 url，不是空壳）", "【本章建议使用的图片" in ctx, True)
    check("（正面）建议段插的是同一份判据", ctx.count(_IMG_SOURCE_RULE), 1)
    check("（反面）建议段里不再自带枚举",
          "流程/层级/关系/时序/进度/逻辑判定类" in ctx, False)

    # 没有图时那一段不出现——这正是上面那条正面断言需要对照的反面
    empty = _build_context({"title": "第一节 系统设计"}, [], [], [], domain="lowalt")
    check("（对照）没有图时整段不出现", "【本章建议使用的图片" in empty, False)
    check("（对照）没有图时判据也不出现", empty.count(_IMG_SOURCE_RULE), 0)


# ---------------------------------------------------------------- 3. 词表同源
def section_3() -> None:
    """提示词那份是**给模型看的中文散文**，导出侧那份是**可执行的正则**——
    两套形态、一套判据。散文改了词表不跟着改，就会出现"提示词说不该留占位、
    导出侧却把它归成另一类"，所以这条接线必须钉住。"""
    print("\n3) 分类词表跟着判据走")
    check("（正面）结构类例词都写在判据里",
          [w for w in STRUCT_WORDS if w not in _IMG_SOURCE_RULE], [])
    check("（正面）实证类例词都写在判据里",
          [w for w in EVIDENCE_WORDS if w not in _IMG_SOURCE_RULE], [])
    check("（正面）结构词表认得判据里的每个结构类例词",
          [w for w in STRUCT_WORDS if not _STRUCTURAL_RE.search(w)], [])
    check("（正面）实证词表认得判据里的每个实证类例词",
          [w for w in EVIDENCE_WORDS if not _EVIDENCE_RE.search(w)], [])


# ---------------------------------------------------------------- 4. 真实占位名上的判定
def section_4() -> None:
    print("\n4) `_pending_kind` 在真实占位名上")
    check("（正面）项目 4 现存 8 处全是「需补真实材料」",
          sorted({_pending_kind(n) for n in REAL_PENDING}), ["需补真实材料"])
    check("（正面）结构类图名判成「需补图」",
          [n for n in STRUCTURAL_NAMES if _pending_kind(n) != "需补图"], [])
    # 反面：两类不能混——上面两条若其实都在答同一个值，就什么都没测到
    check("（反面）两类确实分得开",
          _pending_kind(REAL_PENDING[0]) != _pending_kind(STRUCTURAL_NAMES[0]), True)


# ---------------------------------------------------------------- 5. 真实语料
def section_5() -> None:
    """全库口径的**信息行**：结构类 / 实证类各多少条。

    ⚠ 这是"数出来"的断言，天生容易假绿（库空、或一条 image 都没有时也是 0），
       所以必须配正向断言：确实扫到了多少条。
    """
    print("\n5) 真实语料：结构类 vs 实证类")
    rows = query("SELECT content FROM knowledge_page")
    named = structural = evidence = 0
    for r in rows:
        c = r["content"]
        c = json.loads(c) if isinstance(c, str) else (c or {})
        for im in (c.get("images") or []):
            nm = image_name(im)
            if not nm:
                continue
            named += 1
            if _pending_kind(nm) == "需补真实材料":
                evidence += 1
            else:
                structural += 1

    check("确实扫到了有图名的条目（否则下面的比例是假绿）", named > 100, True)
    # 只断"结构类占多数"这条性质，不断死具体条数——词表一动条数就变，写死只会变噪音
    check("结构类占多数（本轮实测 133 : 69）", structural > evidence, True)
    check("两类都不为零（一面倒说明分类器坏了）", (structural > 0, evidence > 0), (True, True))
    print(f"     共 {len(rows)} 份知识页、{named} 条有图名："
          f"结构类 {structural} 条（该由写手画成 mermaid）、实证类 {evidence} 条（只能找真件）")


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

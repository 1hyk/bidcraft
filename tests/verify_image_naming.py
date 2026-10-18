"""校验「文件名不算图名」与「图片引用按文件名认领」（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_image_naming.py

背景（2026-10-16）：知识页 2947 的第二条 image 图名是 `图 (docx_0009.png)`、没有 url，
追下去发现两件事，都不是孤例：

  ① **文件名冒充图名**：全库 207 条 image 条目里 **103 条**图名是 `docx_0005.png` 这类文件名。
     源头有两处：`docx_parser.export_para_images` 导出内联图写的是 `![图](docx_0009.png)`
     （alt 恒为「图」）；而 `knowledge_agent` 的提示词只解释了 content/usage、
     **根本没交代 caption 该从哪来**，模型没有图题可抄就照抄了文件名。
     图名一路流进正文提示词的【建议图片】，模型照着写出 `[图片占位：docx_0005.png]`。
  ② **url 按序硬挂会串位**：`_attach_image_urls` 原先是 `urls[i]` 按下标配。
     但"模型吐出几条"和"原文有几个引用"从来没有保证一致（2947：原文 1 个引用、模型 3 条），
     一多一少就挂错——图名写着 A、预览出来是 B，而且不报错。

修法：`image_name` 归一（名字不成立就返回空串，消费端见空名跳过 / 显示"未命名"）；
`_attach_image_urls` 改成按文件名认领、同一引用只认领一次；提示词把 caption 说清楚。
**归一放在 `image_name` 而不是抽取时，是为了连已经落库的那 103 条一起管住**——它们不会再被
清洗（要清洗得重解析整册），但从此进不了【建议图片】。

检查：
  1. `is_real_name`/`image_name`：文件名、`图 (docx_0009.png)`、占位 alt、空串都不算图名；
  2. 渲染：脏图名进不了【建议图片】，真图名照旧（一正一反，缺了正面就是假绿）；
  3. `_attach_image_urls`：不串位、条目自己点名就认领它、同一引用不认领两次、引用去重；
  4. 提示词契约：说清了 caption、明令不许拿文件名当图名；
  5. 真实数据：全库再无文件名图名（配正向断言，否则库一空就假绿）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.knowledge_agent import SYSTEM_PROMPT                       # noqa: E402
from app.agents.writer_agent import _build_context                         # noqa: E402
from app.core.database import query                                        # noqa: E402
from app.services import knowledge_service as KS                           # noqa: E402
from app.services.image_ownership import image_name, is_real_name           # noqa: E402

ok = True

GOOD = "系统总体架构图"


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


# ---------------------------------------------------------------- 1. 图名归一
def section_1() -> None:
    print("\n1) 图名归一：文件名 / 占位 alt / 空串都不算名字")
    check("纯文件名不是图名", image_name({"caption": "docx_0005.png"}), "")
    check("「图 (docx_0009.png)」这种回显也不是", image_name({"caption": "图 (docx_0009.png)"}), "")
    check("解析器写死的 alt「图」也不是", image_name({"caption": "图"}), "")
    check("真图名照旧", image_name({"caption": GOOD}), GOOD)
    check("caption 空了回退 title（老数据）", image_name({"title": GOOD}), GOOD)
    check("caption 是文件名时不回退 title（别绕过去）", image_name({"caption": "docx_0001.png", "title": "x"}), "")
    check("非 dict 不炸", image_name("docx_0009.png"), "")
    check("判据函数本身：大小写后缀都拦", is_real_name("A.PNG"), False)
    # 反面：别把正常图名误伤——名字里带"图"字很常见，只有**整个名字**是占位词才算
    check("（对照）含「图」字的正常图名不受影响", image_name({"caption": "测试流程图"}), "测试流程图")


# ---------------------------------------------------------------- 2. 渲染
def section_2() -> None:
    """一正一反：脏图名必须消失（反），真图名必须在（正）。只有反面就是假绿。"""
    print("\n2) 渲染：【建议图片】里不出现文件名，真图名照旧在")
    # 夹具带 url：没有 url 的条目是"空壳"（素材里没这张图），会被挡在【建议图片】外，
    # 那么下面那条正面断言（真图名在不在）就没在测"名字脏不脏"这件事了。
    _u = "/uploads/parsed/_verify_naming/"
    ctx = _build_context(
        {"title": "第二节 阶段性交付物清单"},
        [],
        [{"images": [{"caption": "docx_0005.png", "content": "某张图", "url": _u + "a.png"},
                     {"caption": GOOD, "content": "系统整体结构", "url": _u + "b.png"}]}],
        [],
        domain="lowalt",
    )
    check("文件名的图没进【建议图片】", "docx_0005.png" in ctx, False)
    check("（正面）真图名进去了", f"- 图「{GOOD}」" in ctx, True)


# ---------------------------------------------------------------- 3. 挂 url
def section_3() -> None:
    """`_attach_image_urls`：按文件名认领，不按下标硬配。

    `_image_index` 换成假实现——这一节只验认领逻辑，不该依赖本机解析产物还在不在。
    """
    print("\n3) 挂 url：按文件名认领，不串位、不重复认领")
    real = KS._image_index
    KS._image_index = lambda out_dir: {"docx_0009.png": "/uploads/parsed/1/images/docx_0009.png",
                                       "docx_0005.png": "/uploads/parsed/1/images/docx_0005.png"}
    try:
        # 就是 2947 的现场：原文 1 个引用，模型吐 3 条（第 2 条是回显）
        imgs = [{"caption": "无人机实飞测试现场", "content": "户外实飞"},
                {"caption": "图 (docx_0009.png)", "content": "原文中引用的图片，此处推测"},
                {"caption": "数据中心服务器机房", "content": "机房环境"}]
        KS._attach_image_urls(imgs, "正文\n![图](docx_0009.png)\n", None)
        check("第 1 条拿到 url（真图）", imgs[0].get("url"), "/uploads/parsed/1/images/docx_0009.png")
        check("回显那条不重复认领同一个文件", all("url" not in i for i in imgs[1:]), True)

        # 顺序被打乱时，条目自己点名了文件就该认对它，而不是按位次发
        two = [{"caption": "B图", "content": "见 docx_0005.png"},
               {"caption": "A图", "content": "见 docx_0009.png"}]
        KS._attach_image_urls(two, "![](docx_0009.png)\n![](docx_0005.png)\n", None)
        check("条目点名了文件就认它（第 2 条要 0009）",
              two[1].get("url"), "/uploads/parsed/1/images/docx_0009.png")
        check("（对照）第 1 条认 0005",
              two[0].get("url"), "/uploads/parsed/1/images/docx_0005.png")

        # 同一个文件被引用两次 = 同一张图，不该占掉两个条目
        dup = [{"caption": "甲", "content": ""}, {"caption": "乙", "content": ""}]
        KS._attach_image_urls(dup, "![](docx_0009.png)\n![](docx_0009.png)\n", None)
        check("同一文件引用两次只算一张图", [i.get("url") for i in dup], ["/uploads/parsed/1/images/docx_0009.png", None])
    finally:
        KS._image_index = real


# ---------------------------------------------------------------- 4. 提示词契约
def section_4() -> None:
    """静态契约：`caption` 必须被交代清楚，且明令不许写文件名。

    这条是**唯一能治本**的一环——模型吐什么全看提示词，归一只是兜底。
    """
    print("\n4) 提示词契约：说了 caption 从哪来、不许拿文件名当图名")
    check("规则里解释了 caption 是什么", "- caption：**图名**" in SYSTEM_PROMPT, True)
    check("交代了图题在哪找", "图题一般在图的前一段或后一段文字里" in SYSTEM_PROMPT, True)
    check("没图题时要求自己起名（不是留空）", "宁可自己起名，也不要留空" in SYSTEM_PROMPT, True)
    check("明令不许把文件名当图名", "绝对不要把文件名当图名" in SYSTEM_PROMPT, True)
    # 2026-10-16 改写：提示词现在说的是「圆括号里的是文件名、方括号里的才是图题」
    # （alt 带图题后，不能再指认 `![图](…)` 这个整体了）。
    # 只断连续子串——断言跨换行会假红。
    check("点出了文件名在圆括号里、图题在方括号里",
          "圆括号里的" in SYSTEM_PROMPT and "方括号里的就是这个文件的图题" in SYSTEM_PROMPT, True)
    check("JSON 结构里 caption 字段还在", '"caption": "图名"' in SYSTEM_PROMPT, True)


# ---------------------------------------------------------------- 5. 真实数据
def section_5() -> None:
    """全库口径：再也没有"图名是文件名"的条目。

    ⚠ 这是"不该出现"式断言，天生容易假绿（库空、或一条 image 都没有时也通过），
       所以下面必须配正向断言：确实扫到了多少条。
    """
    print("\n5) 真实数据：全库的 image 条目，返回出来的图名没有一个是文件名")
    rows = query("SELECT id, content FROM knowledge_page")
    total = named = suppressed = 0
    bad: list[tuple[int, str]] = []
    fileish = re.compile(r"\.(png|jpe?g|gif|bmp|webp|tiff)\b", re.I)
    for r in rows:
        c = r["content"]
        c = json.loads(c) if isinstance(c, str) else (c or {})
        for im in (c.get("images") or []):
            if not isinstance(im, dict):
                continue
            total += 1
            nm = image_name(im)                       # ← 消费端拿到的就是它
            if nm:
                named += 1
                if fileish.search(nm):                # 漏网的：归一没拦住
                    bad.append((r["id"], nm))
            elif str(im.get("caption") or im.get("title") or "").strip():
                suppressed += 1                       # 库里有名字，但名字不成立 → 被归一挡下
    check("确实扫到了 image 条目（否则下面的「没有文件名图名」是假绿）", total > 100, True)
    check("确实有名字成立的图（同上）", named > 0, True)
    check("返回出来的图名没有一个是文件名", bad[:5], [])
    # 被挡下的条数是**信息**不是断言：库里的老 caption 仍是历史遗留（要清得重解析整册），
    # 归一的意义就在于不必清库——它们从读的就进不了【建议图片】。
    print(f"     共 {len(rows)} 份知识页、{total} 条 image 条目：{named} 条有可用图名，"
          f"{suppressed} 条名字不成立被归一挡下")


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

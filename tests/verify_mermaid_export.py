"""校验「Mermaid 自动配图」：正文里的 ```mermaid 能不能变成 Word 里的一张真图（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_mermaid_export.py [--llm]
      `--llm` 额外跑第 6 节（**会真调一次模型**，几十秒），验编写提示词有没有让模型写出
      能解析的 mermaid；不带就跳过，其余几节不调模型、几秒跑完。

背景：编辑器一直支持 Mermaid 渲染（`frontend/src/components/MermaidView.vue`），但导出侧
只把 `<div class="mermaid-block">` 标红成 `[待补图：Mermaid 关系图（…）]`，而且**那条分支几乎
走不到**——AI 生成的正文存的是 Markdown，```mermaid 经 python-markdown 的 fenced_code 变成
`<pre><code class="language-mermaid">`，落进通用 `pre` 分支被渲染成一片 Consolas 文本。
本次两头都补上：识别两种格式 + headless Chrome 渲成 PNG 嵌进 docx（见
`app/services/mermaid_render.py` 的选型说明）。

这个链路**依赖本机的浏览器**，所以在没装浏览器的机器上它会明确报「不可用」而不是假装通过；
渲染失败**必须**退化成红字占位，不能让一张图把整篇导出带崩——下面第 4 节专门守这条。

检查：
  1. 围栏识别：mermaid 围栏认得出、别的语言围栏不误认、实体转义已还原；
  2. 渲染：正常图出 PNG 且带缓存；语法错/空源码一律 None；
  3. 纵横比定宽：竖长图按 19cm 高封顶缩宽；
  4. **端到端**：两种正文格式（md 围栏 / html 的 div.mermaid-block）各导一份 docx，
     断言 docx 包里**真有图片**（word/media/），且正文里**没有**那行红字占位；
  5. 预热与遍历用同一把 key（键错开不会出错图，只会白渲两遍）+ **一批只起一次 Chrome**
     （原来这里按秒数比快慢，机器一忙就假红；改成数 `_screenshot_and_split` 的调用次数）；
  6. （`--llm`）模型写出来的 mermaid 真的能解析；
  7. 规则 9 的**文字契约**（不调模型）：`gantt`↔「横道图」、点名类配图不得留成占位、
     两处枚举里都没有「示意图」这个模糊档——这条守的是"模型画不画"，第 1~5 节守不到。
"""
from __future__ import annotations

import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import lxml.html                                                          # noqa: E402
from docx import Document                                                 # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH                             # noqa: E402

from app.core.docx_style import DocxStyle                                 # noqa: E402
from app.services import docx_export as DX                                # noqa: E402
from app.services import mermaid_render as MR                             # noqa: E402

ok = True

GOOD = """flowchart TD
  A[需求受理] --> B[方案设计]
  B --> C[测试执行]
  C --> D[验收交付]"""


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def _pre(html: str):
    """把一段 HTML 解析成受控子集的根，返回其中所有 <pre>。"""
    root = lxml.html.fromstring(f"<div>{html}</div>")
    return root, [e for e in root.iter() if isinstance(e.tag, str) and e.tag.lower() == "pre"]


# ---------------------------------------------------------------- 1. 围栏识别
def section_1() -> None:
    print("\n1) 围栏识别（md 正文经 python-markdown 归一后）")
    md = ("## 实施流程\n\n本项目按下图推进：\n\n```mermaid\n"
          + GOOD + "\n```\n\n以上为总体流程。\n\n```python\nprint(1)\n```\n")
    html = DX._normalize_body({"format": "md", "body": md})
    check("归一出的是语言围栏", 'class="language-mermaid"' in html, True)

    _root, pres = _pre(html)
    check("两个围栏都在", len(pres), 2)
    got = DX._mermaid_code(pres[0])
    check("mermaid 围栏认得出", got is not None, True)
    # lxml 会把 fenced_code 转义出来的 --&gt; 还原，源码必须是可直接喂给 mermaid 的原文
    check("实体已还原（有 --> 没有 &gt;）", "-->" in (got or "") and "&gt;" not in (got or ""), True)
    check("python 围栏不误认", DX._mermaid_code(pres[1]), None)
    check("裸 <pre> 不误认", DX._mermaid_code(lxml.html.fromstring("<pre>x</pre>")), None)

    # 编辑器保存过的正文走 div.mermaid-block（另一条真会发生的路）
    div = lxml.html.fromstring('<div class="mermaid-block" data-code="flowchart TD&#10;A-->B"></div>')
    check("div.mermaid-block 的 data-code 取得到", "A-->B" in (div.get("data-code") or ""), True)


# ---------------------------------------------------------------- 2/3. 渲染
def section_2() -> None:
    print("\n2) 渲染与缓存")
    if not MR.available():
        print(f"  [--] 本机没有可用浏览器（{MR.chrome()}）或没有 mermaid.min.js，"
              f"跳过渲染用例；导出侧会全部退化成红字占位")
        return

    p1 = MR.render_cached(GOOD)
    check("正常图渲染成功", bool(p1 and p1.is_file()), True)
    if not p1:
        return
    from PIL import Image
    with Image.open(p1) as im:
        w, h = im.size
    check("图有合理尺寸（宽>60 高>40）", w > 60 and h > 40, True)
    p2 = MR.render_cached(GOOD)
    check("同源码命中同一个缓存文件", p2, p1)

    print("  坏输入必须降级成 None（否则会把 mermaid 自己的报错图嵌进标书）：")
    for label, bad in (("纯文本乱码", "this is not mermaid at all {{{"),
                       ("围栏未闭合", "flowchart TD\n  A[未闭合"),
                       ("空源码", "")):
        check(f"  {label} → None", MR.render_cached(bad), None)

    print("\n3) 纵横比定宽（竖长图不许顶穿一页）")
    # 竖排链：flowchart TD 每个节点占一行，天然又高又窄——正是会被 19cm 封顶的那类
    tall = "flowchart TD\n" + "\n".join(f"  N{i}[节点{i}] --> N{i + 1}[节点{i + 1}]" for i in range(12))
    pt = MR.render_cached(tall)
    if pt:
        with Image.open(pt) as im:
            tw, th = im.size
        cm = DX._mermaid_width_cm(pt)
        height_cm = th / tw * cm
        # 容差 0.1cm：宽度是 round(…, 2) 到两位小数的，纵横比一大这点取整就放大成零点几毫米
        check(f"竖长图({tw}x{th}) 缩后高 ≤ 19cm（实为 {height_cm:.2f}cm）",
              height_cm <= DX.MERMAID_MAX_H_CM + 0.1, True)
        check("竖长图确实被缩窄了", cm < DX.MERMAID_MAX_W_CM, True)
    # 横排链：宽而矮，不该被缩
    wide = "flowchart LR\n  A[需求受理] --> B[方案设计] --> C[测试执行] --> D[验收交付] --> E[运维保障]"
    pw = MR.render_cached(wide)
    if pw:
        with Image.open(pw) as im:
            ww, wh = im.size
        check(f"宽图({ww}x{wh}) 不缩，满宽 14cm", DX._mermaid_width_cm(pw), DX.MERMAID_MAX_W_CM)
    check("读不出的路径不炸", DX._mermaid_width_cm(Path("D:/不存在.png")), DX.MERMAID_MAX_W_CM)


# ---------------------------------------------------------------- 4. 端到端
def section_4() -> None:
    print("\n4) 端到端：两种正文格式各导一份 docx")
    style = DocxStyle()
    cases = [
        # AI 生成的正文：format=md，```mermaid 围栏
        ("md 围栏", {"format": "md",
                     "body": f"## 实施流程\n\n本项目按下图推进：\n\n```mermaid\n{GOOD}\n```\n" +
                             "\n\n" + "正文说明。" * 20}),
        # 编辑器保存过的正文：format=html，Tiptap 的 div.mermaid-block
        ("html 节点", {"format": "html",
                       "body": f'<h2>实施流程</h2><p>本项目按下图推进：</p>'
                               f'<div class="mermaid-block" data-code="{GOOD}"></div>'
                               f'<p>正文说明。</p>'}),
    ]
    for label, payload in cases:
        html = DX._normalize_body(payload)
        root = lxml.html.fromstring(f"<div>{html}</div>")
        doc = Document()
        DX._walk_block(doc, style, root, base_level=1)
        out = ROOT / "uploads" / "parsed" / "_verify_mermaid" / f"{label.replace(' ', '_')}.docx"
        out.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out))

        with zipfile.ZipFile(out) as z:
            media = [n for n in z.namelist() if n.startswith("word/media/")]
        text = "\n".join(p.text for p in doc.paragraphs)
        print(f"     {label}: 图片 {len(media)} 张，段落 {len(doc.paragraphs)} 个 → {out.name}")
        if MR.available():
            check(f"{label} 正文里嵌进了图片", len(media) >= 1, True)
            check(f"{label} 没有红字占位", "[待补图" in text, False)
        else:
            # 没浏览器时必须**明确降级**，而不是静默丢图
            check(f"{label} 没有浏览器时退化成红字占位", "[待补图" in text, True)

    # 普通代码块不许被当成图：它该照旧落成 Consolas 文本，且不产生图片
    html = DX._normalize_body({"format": "md", "body": "```python\nprint(1)\n```\n"})
    root = lxml.html.fromstring(f"<div>{html}</div>")
    doc = Document()
    DX._walk_block(doc, style, root, base_level=1)
    check("普通代码块不产图", sum(1 for r in doc.paragraphs[0].runs), 1)
    check("普通代码块仍是代码文（Consolas）",
          doc.paragraphs[0].runs[0].font.name, "Consolas")


# ---------------------------------------------------------------- 5. 预热
def section_5() -> None:
    """预热（_warm_mermaid 的正则）与遍历（lxml）必须算出**同一份源码字符串**。

    键错开的后果不是错图，是**同一张图白渲两遍**：预热按没还原的 `&amp;` 拼 key、
    遍历按还原后的 `&` 拼 key，缓存互不命中，导出时间翻倍还不易察觉。
    判据：预热跑完再遍历一遍，缓存目录里**不许多出文件**（多出来就是又渲了一次）。
    """
    print("\n5) 预热与遍历用同一把 key")
    if not MR.available():
        print("  [--] 没有可用浏览器，跳过")
        return
    import html as _html

    cache = ROOT / "uploads" / "mermaid"
    # 每轮换一个 nonce：**上面那条耗时断言必须是冷渲**，全命中缓存的话两边都是 0.0s，
    # 等于什么都没验。下面"遍历没重渲"那条与 nonce 无关，照样成立。
    nonce = f"{time.time_ns() % 100000}"
    # 六张不重样的图，两种正文格式各一半；刻意带上 --> 与 &（都要过实体转义那一关）
    srcs = [f"flowchart LR\n  A[甲&乙] --> B[{nonce}环节{i}]\n  B --> C[交付]" for i in range(3)]
    srcs += [f"flowchart TD\n  X[{nonce}阶段{i}] --> Y[验收]" for i in range(3)]
    contents = {}
    for i, s in enumerate(srcs):
        if i % 2:
            contents[i] = {"format": "html",
                           "body": f'<p>说明{i}</p><div class="mermaid-block" '
                                   f'data-code="{_html.escape(s, quote=True)}"></div>'}
        else:
            contents[i] = {"format": "md", "body": f"## 图{i}\n\n```mermaid\n{s}\n```\n"}

    # **数起了几次 Chrome，而不是比秒数**。这里原本断言的是"6 张整批耗时不超过单张的 3 倍"，
    # 2026-10-16 飘过一次假红：机器一忙，同一件事的耗时差一倍，秒数从来就不是这条的性质。
    # `_BATCH` 的意义也**不是"更快"**，是**一批共用一扇窗口**——这个数完全确定：
    # 冷图数 / _BATCH 向上取整。`_screenshot_and_split` 一次调用就是一扇窗口
    # （内部那次 subprocess.run 是后端仅有的外部进程之一），拿它当计数器最贴切；
    # 兜底路径 `_render_one` 走的也是同一个函数，所以"批量失败退回逐张"同样会被数出来。
    launches: list[int] = []
    real_shot = MR._screenshot_and_split

    def _counting(chunk):
        launches.append(len(chunk))
        return real_shot(chunk)

    MR._screenshot_and_split = _counting
    try:
        DX._warm_mermaid(contents)
    finally:
        MR._screenshot_and_split = real_shot
    per = -(-len(srcs) // MR._BATCH)                      # 冷图数 / 批大小，向上取整
    print(f"     预热 {len(srcs)} 张冷图：起了 {len(launches)} 次 Chrome"
          f"（每批 {launches} 张，_BATCH={MR._BATCH}）")
    check(f"{len(srcs)} 张一个批次恰好起 {per} 次 Chrome（没退回逐张）",
          len(launches), per)
    # 快照必须在**所有**预热渲染之后取：下面要比的是"遍历有没有又渲一次"，
    # 任何在此之后新增的缓存文件都只可能来自遍历那一趟。
    snap = {str(p): p.stat().st_mtime_ns for p in cache.rglob("*.png")}

    style = DocxStyle()
    doc = Document()
    for payload in contents.values():
        root = lxml.html.fromstring(f"<div>{DX._normalize_body(payload)}</div>")
        DX._walk_block(doc, style, root, base_level=1)
    now = {str(p): p.stat().st_mtime_ns for p in cache.rglob("*.png")}
    check("6 张图全部嵌进了文档", len(doc.inline_shapes), 6)
    # 只比"新增了哪些"：预热之前就在缓存里的旧文件与本次无关，别把整个缓存目录打印出来
    check("遍历没有新渲任何图（预热键与遍历键一致）", sorted(set(now) - set(snap)), [])

    # 同一批里重复源码只该渲一张（去重），否则导出一份文档会白起好几次浏览器
    dup = ["flowchart LR\n  D1[同一张] --> D2[图]", "flowchart LR\n  D1[同一张] --> D2[图]"]
    check("重复源码去重成 1 张", len(MR.render_batch(dup)), 1)


# ---------------------------------------------------------------- 7. 规则 9 的文字契约
def section_7() -> None:
    """规则 9 是"模型到底画不画图"的唯一开关——渲染链路再绿也守不住它（见第 6 节）。

    这几条全是真翻过车才补的（2026-10-16，#283/#285/#286）：
    ① 「横道图」被画成了 flowchart——`gantt` 只在图型表里躺着，没跟正文里的"横道图/进度计划"
       对上号，模型没有理由选它；
    ② 「算法逻辑图」被留成了 [图片占位]——**两处**枚举里都躺着「示意图」这个模糊档
       （`WRITER_SYSTEM` 规则 5、以及 `_build_context` 每章都发的那段【建议图片】处理方式），
       算法逻辑图把自己归进"示意图"就合法了。
    所以这里既查"该写的写没写"，也查"那个放错档的词清没清"。断言全是**正向子串**
    （有/没有），删掉或改回任一句就红——这正是后来者最想干的事（把提示词当润色删）。

    **2026-10-16 第二轮**：规则 5 / 规则 9(4) / 【建议图片】原先**各写一份枚举**，三份互相
    不一致（规则 9 有「组织/因果/对比关系」，另两处没有），「组织架构图」从缝里漏成占位；
    三份还都漏掉了语料里真实存在的「服务模式图/应对策略图/维度图/思维导图」。现在收成
    `writer_agent._IMG_SOURCE_RULE` 一份常量（**按"内容从哪来"判，枚举只当例子**），三处都插它。
    本节的第 4 条断言原来钉的是被删掉的那句枚举（`"逻辑判定类的按规则 9"`），随之下架；
    它要守的**意图没变**——进度/逻辑判定这类图必须落在"画成 mermaid"那一档、不许留成占位
    ——改从常量与两处注入点断言（下面 `_IMG_SOURCE_RULE` 那三条）。提示词的散文契约在
    `verify_pending_images.py`；这里守的是"这两个注入点插的确实是同一份判据"。
    """
    from app.agents import writer_agent as WA
    from app.core.domains import fill_genre

    p = fill_genre(WA.WRITER_SYSTEM, "lowalt")
    for label, needle in [
        ("gantt 与「横道图」对上了号（进度类不再被画成流程图）", "横道图"),
        ("明说别拿 flowchart 顶替 gantt", "别拿 flowchart 代替"),
        ("点名要配的图不得留成占位", "就必须画出来，不得留成 [图片占位"),
    ]:
        check(label, needle in p, True)

    # 原「规则 5 的 Mermaid 那一档含进度/逻辑判定」：判据改了形状，但**那一档的去处没变**。
    # 正面断在常量自身上（改了判据、把进度/逻辑判定挪出"能推出来"那一档，或把去处从
    # mermaid 改成留占位，这条立刻红）；再断规则 5 里插的确实是它。
    rule = WA._IMG_SOURCE_RULE
    check("判据把进度/逻辑判定划进「能由正文推出来」那一档",
          [w for w in ("进度", "逻辑判定") if w not in rule], [])
    check("那一档的去处是画成 mermaid（不是留占位）",
          "按规则 9 画成 ```mermaid" in rule, True)
    check("规则 5 里插的就是这份判据（不是又写了一份枚举）", rule in p, True)

    # 每章实际发出去的那段许可（规则 5 是常驻 system，这里是按章的注入，两份都要对）
    # ⚠ 夹具必须带 url：没有 url 的条目是"模型从正文描述编出来的空壳"（素材里没这张图），
    #   已被 `_build_context` 挡在【建议图片】外；不带 url 的话整段都不发，下面两条全是假绿
    #   （2026-10-16 加空壳过滤时就漏改过这里，守卫当场红了两条）。
    ctx = WA._build_context(
        {"title": "第三节 关键技术指标论证"},
        [], [{"images": [{"caption": "多机协同航线规划算法逻辑图",
                          "url": "/uploads/parsed/1/images/docx_0001.png"}]}], [],
        domain="lowalt",
    )
    check("【建议图片】插的也是同一份判据", rule in ctx, True)
    check("【建议图片】把进度/逻辑判定也划进 Mermaid 那一档（同一份判据带来的）",
          "按规则 9 画成 ```mermaid" in ctx, True)
    # 负向断言：要证明的是"模糊档那个词没了"，不是"某句话还在"。
    # 留着「示意图」，模型就永远有一条合法退路——图名里带"示意图"的会自动掉进占位。
    check("两处枚举里都不再有「示意图」这个模糊档",
          ("示意图" in p, "示意图" in ctx), (False, False))


# ---------------------------------------------------------------- 6. 提示词（真调模型）
def section_6() -> None:
    """`WRITER_SYSTEM` 规则 9 到底管不管用：模型会不会写出**能解析**的 mermaid。

    **这个用例会真调一次模型**（几十秒），所以默认不跑，要跑加 `--llm`。
    它是唯一能守住"自动配图"那半边的东西：渲染链路再好，模型不写 ```mermaid（还是写
    [图片占位]），或者写出来的语法过不了 mermaid.parse，导出件里就只会是红字占位。
    所以断言两层：**得有块**，且**块得能渲染**。
    """
    print("\n6) 提示词 → 模型 → mermaid（真调模型）")
    from app.agents.writer_agent import write_chapter

    # 一个天然该有流程图的章节；组织架构与职责也一并问，看它会不会画第二张
    outline = {
        "title": "第一节 项目实施流程与组织保障",
        "thinking": "交代本项目从启动到验收的实施流程，以及承担该流程的组织架构与职责分工。",
        "key_points": ["实施流程（启动→现场勘测→方案设计→测试执行→问题闭环→验收）",
                       "项目组织架构与各岗位职责", "关键节点的责任人与交付物"],
    }
    out = write_chapter(outline, domain="lowalt")
    print(f"     正文 {len(out)} 字")
    blocks = re.findall(r"```mermaid\n(.*?)```", out, re.S)
    check("模型输出了 mermaid 块", len(blocks) >= 1, True)
    check("没有退回去写图片占位", "[图片占位" in out, False)
    if not blocks:
        # 光说"没输出 mermaid"没法判断是模型没听话、还是提示词没到位——
        # 把模型原文落盘，这是唯一能追下去的线索（放临时目录，别污染 uploads/）。
        import tempfile

        dump = Path(tempfile.gettempdir()) / "bidcraft_mermaid_probe.md"
        dump.write_text(out, encoding="utf-8")
        print(f"     !! 提示词规则 9 没生效，检查 writer_agent.WRITER_SYSTEM；"
              f"模型原文已存 {dump}")
        return
    ok_blocks = 0
    for i, src in enumerate(blocks):
        p = MR.render_cached(src)
        print(f"     第{i + 1}块 {len(src)} 字符 → {'出图 ' + str(p.name) if p else '解析失败'}")
        ok_blocks += bool(p)
    check(f"{len(blocks)} 块全部能渲染（规则 9 里那些语法约束是不是真的被遵守了）",
          ok_blocks, len(blocks))


def main() -> int:
    section_1()
    section_2()
    section_4()
    section_5()
    section_7()
    if "--llm" in sys.argv:
        section_6()
    else:
        print("\n6) 提示词 → 模型（跳过；加 --llm 才跑，会真调一次模型）")
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

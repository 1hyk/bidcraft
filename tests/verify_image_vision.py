"""校验「图名从哪来」这条链：文档图题（确定性）+ 视觉（兜底）（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_image_vision.py

背景（2026-10-16）：知识页里的图名一直不可信，追下去是**三层叠加**的错：

  ① **解析器没给图题**。`docx_parser` 导图时 alt 恒为「图」，图题那句原文被当成普通段落
     混在正文里，模型只能靠上下文猜。
     ——实测《实飞测试验证外协项目投标文件(正本).docx》：图题段落是 `[首行缩进|CENTER]`、
     正文一律 `[Normal|JUSTIFY]`。**居中是唯一信号**（该文档 0 个段落用 Word 题注样式，
     "读题注样式"这条常规路子在本书上不存在）。识别覆盖率约 10%，但精度高。
  ② **模型会把图题推错位**。同文档 2.2.1 节三张图，模型给的名字整体后移一格：
     把上一段正文的话题「端到端测试验证流程图」安给了一张**无人机照片**，真图题
     「实飞测试现场（一）/（二）」被推给后两张。图名写着流程图、画面是无人机，且不报错。
     ——所以文档图题必须**覆盖**模型的名字，不能只填空。
  ③ **系统从来没看过图**。`llm.chat_with_image` 零调用，`images[].content` 全部是照着
     图名编的（无图名的 101 条里 47% 带"可能包含/通常对应/推测为"）。
     ——2026-10-16 实测 `qwen3.8-flash` 本身就能读图（自绘对照图能抄出图上的随机串），
     于是接进提炼链路：`services/image_vision.py`。

检查：
  1. 图题识别：现场合成 docx，居中短文本算图题、正文/标题/长文本/末尾都不算；
  2. 图题覆盖：文档图题压过模型写的名字（修错位），alt 是「图」时不当图题；
  3. 视觉补名：填空不夺名、覆写 content、去重、无 url 跳过（全部打桩，不花真钱）；
  4. 提示词契约：告诉模型 alt 里就是图题；`llm.chat_with_image` 确已收编删除；
  5. 真实数据：真文档上跑一遍，2.2.1 节那三张图的图名必须钉对（错位的回归哨）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.knowledge_agent import SYSTEM_PROMPT                       # noqa: E402
from app.core.database import query                                        # noqa: E402
from app.services import image_vision as IV                                # noqa: E402
from app.services import knowledge_service as KS                           # noqa: E402

ok = True


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


# ---------------------------------------------------------------- 1. 图题识别
def _tiny_png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (24, 24), "red").save(path)
    return path


def _synth_docx(tmp: Path) -> Path:
    """合成一份最小 docx：4 张图，后面各跟一种"像不像图题"的段落。

    合成而不是拿真文档测，是为了**能构造反面用例**——真文档里反面靠碰运气。
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    png = _tiny_png(tmp / "px.png")
    doc = Document()
    doc.add_heading("第一章 合成测试章", level=1)
    doc.add_paragraph("正文铺垫。" * 30)

    doc.add_picture(str(png))                                    # ① 居中短文本 → 图题
    doc.add_paragraph("无人机实飞测试现场（一）").alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_picture(str(png))                                    # ② 正文段落 → 不是
    doc.add_paragraph("这是普通正文段落，两端对齐，讲的是别的事情。" * 3).alignment = \
        WD_ALIGN_PARAGRAPH.JUSTIFY

    doc.add_picture(str(png))                                    # ③ 居中但超长 → 不是
    doc.add_paragraph("这条虽然居中但是太长了，四十一字以上就不该当图题用了明白吗。" * 2).alignment = \
        WD_ALIGN_PARAGRAPH.CENTER

    doc.add_picture(str(png))                                    # ④ 居中且是标题样式 → 不是
    doc.add_heading("评审要求目录", level=1)

    doc.add_picture(str(png))                                    # ⑤ 后面什么都没有 → 不是

    # ⑥⑦⑧ 表格单元格里的图。2026-10-16 修复前 `_table_to_markdown` 只读 `c.text`，
    # 表里的图**静默丢掉**（实测真文档 doc1 有 14 张实拍现场照/界面截图全在 1×1 表里）。
    # 表格放在最后，免得打乱 ①~⑤ 的编号——编号本身也是别处的断言。
    t1 = doc.add_table(rows=1, cols=1)                           # ⑥ 图 + 居中短图题 → 是
    t1.rows[0].cells[0].paragraphs[0].add_run().add_picture(str(png))
    t1.rows[0].cells[0].add_paragraph("缺陷管理系统操作界面(一)").alignment = \
        WD_ALIGN_PARAGRAPH.CENTER

    t2 = doc.add_table(rows=1, cols=1)                           # ⑦ 图 + 两端对齐正文 → 不是
    t2.rows[0].cells[0].paragraphs[0].add_run().add_picture(str(png))
    t2.rows[0].cells[0].add_paragraph("表格里两端对齐的正文段讲的是别的事情，不该当图题用。" * 3).alignment = \
        WD_ALIGN_PARAGRAPH.JUSTIFY

    t3 = doc.add_table(rows=1, cols=1)                           # ⑧ 图后面什么都没有 → 不是
    t3.rows[0].cells[0].paragraphs[0].add_run().add_picture(str(png))

    path = tmp / "synthetic.docx"
    doc.save(str(path))
    return path


def section_1() -> None:
    print("\n1) 图题识别：居中短文本才算，正文/标题/超长/末尾都不算")
    from app.services.docx_parser import chapters_from_docx

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cap: dict[str, str] = {}
        chapters_from_docx(_synth_docx(tmp), min_text_chars=0,
                           out_dir=tmp / "out", captions_out=cap)
        check("①居中短文本被认成图题", cap.get("docx_0001.png"), "无人机实飞测试现场（一）")
        check("②两端对齐的正文段不算图题", "docx_0002.png" in cap, False)
        check("③居中但 40 字以上不算图题", "docx_0003.png" in cap, False)
        check("④居中但 Heading 样式不算图题", "docx_0004.png" in cap, False)
        check("⑤图片后面没内容不算图题", "docx_0005.png" in cap, False)
        # ⑥⑦⑧ 表格里的图（修复前这三张一个都导不出来）
        check("⑥（正面）表格里的图导出来了", "docx_0006.png" in cap, True)
        check("⑥（正面）表格里居中短文本认成图题", cap.get("docx_0006.png"), "缺陷管理系统操作界面(一)")
        check("⑦（反面）表格里两端对齐的正文段不算图题", "docx_0007.png" in cap, False)
        check("⑧（反面）表格里图后没内容不算图题", "docx_0008.png" in cap, False)
        check("（正面）认出的图题就是 2 条（正文 1 + 表格 1），没多认", len(cap), 2)
        text = "\n".join(c.text for c in chapters_from_docx(
            _synth_docx(tmp), min_text_chars=0, out_dir=tmp / "out2"))
        check("有图题的引用 alt 写的是图题", "![无人机实飞测试现场（一）](docx_0001.png)" in text, True)
        check("没图题的引用 alt 仍是「图」", "![图](docx_0002.png)" in text, True)
        check("（正面）表格图的引用也进了正文", "![缺陷管理系统操作界面(一)](docx_0006.png)" in text, True)
        check("（反面）表格里没图题的那张，引用 alt 仍是「图」",
              "![图](docx_0007.png)" in text, True)


# ---------------------------------------------------------------- 2. 图题覆盖
def section_2() -> None:
    """文档图题必须**压过**模型写的图名——这是修"图名错位"的那一刀。"""
    print("\n2) 图题覆盖：文档原文的图题压过模型编的名字")
    real = KS._image_index
    KS._image_index = lambda out_dir: {f"docx_{i:04d}.png": f"/uploads/parsed/1/images/docx_{i:04d}.png"
                                       for i in range(1, 6)}
    try:
        # 2947/2916 的现场：模型把上一段正文的话题当成图名，真图题在引用 alt 里
        imgs = [{"caption": "图2：端到端测试验证流程图", "content": "x"}]
        KS._attach_image_urls(imgs, "正文\n![无人机实飞测试现场（一）](docx_0002.png)\n", None)
        check("模型编的名字被文档图题覆盖", imgs[0].get("caption"), "无人机实飞测试现场（一）")
        check("url 照旧挂上", imgs[0].get("url"), "/uploads/parsed/1/images/docx_0002.png")

        # 反面：alt 是解析器占位「图」时，绝不能把它当图名抄进去
        plain = [{"caption": "系统总体架构图", "content": "y"}]
        KS._attach_image_urls(plain, "正文\n![图](docx_0003.png)\n", None)
        check("alt 是「图」时不覆盖真实图名", plain[0].get("caption"), "系统总体架构图")

        # 反面：alt 是文件名时同样不算图名
        fn = [{"caption": "", "content": "z"}]
        KS._attach_image_urls(fn, "正文\n![docx_0004.png](docx_0004.png)\n", None)
        check("alt 是文件名时不算图名", fn[0].get("caption"), "")

        # 正面：条目本来没名字时，文档图题要能填进去
        empty = [{"content": "w"}]
        KS._attach_image_urls(empty, "正文\n![产品设计研发流程](docx_0005.png)\n", None)
        check("（正面）没名字的条目被文档图题填上", empty[0].get("caption"), "产品设计研发流程")
    finally:
        KS._image_index = real


# ---------------------------------------------------------------- 3. 视觉补名
def section_3() -> None:
    """全部打桩：这一节不该花真钱、也不该依赖本机图片还在不在。"""
    print("\n3) 视觉补名：填空不夺名、覆写 content、按内容去重、无 url 跳过")
    real_read, real_desc = IV.read_image, IV.describe
    IV._cache.clear()
    calls: list[str] = []

    IV.read_image = lambda url: (url.encode(), "image/png")   # 像素即 url，便于去重测试

    def fake_describe(data, mime, *, model=None):
        calls.append(data.decode())
        return {"name": "验收流程图", "content": f"看图得来：{data.decode()}", "kind": "diagram"}

    IV.describe = fake_describe
    try:
        imgs = [
            {"content": "旧的猜测", "url": "/a.png"},          # 无名字 → 补名 + 覆写 content
            {"caption": "文档自己的图题", "content": "旧的", "url": "/b.png"},  # 有名 → 名字不动
            {"content": "没 url", "url": ""},                  # 无 url → 跳过
            {"content": "同图再来", "url": "/a.png"},          # 与第 1 条同像素 → 命中缓存
        ]
        st = IV.enrich_images(imgs)
        check("无名字的被补上视觉图名", imgs[0].get("caption"), "验收流程图")
        check("（反面）已有的图名不被夺走", imgs[1].get("caption"), "文档自己的图题")
        check("content 被视觉读数覆写（旧的猜测不留）", imgs[0].get("content"), "看图得来：/a.png")
        check("kind 落进条目", imgs[0].get("kind"), "diagram")
        check("无 url 的条目原样不动", imgs[2], {"content": "没 url", "url": ""})
        check("同像素只调一次模型", len(calls), 2)
        # named=2：第 1 条与第 4 条都没名字（两条描述的是同一张图，各自都要名字）
        check("统计：命名 2 / 覆写 3 / 命中缓存 1",
              [st["named"], st["described"], st["cached"]], [2, 3, 1])

        # overwrite_names：**默认关**（只填空），开了才允许顶掉已有名字。
        # 一正一反——只测正面的话，"默认也覆盖"这个回归查不出来。
        IV.describe = fake_describe
        IV._cache.clear()
        keep = [{"caption": "模型猜的名字", "content": "旧的", "url": "/e.png"}]
        IV.enrich_images(keep)
        check("（反面）默认不覆盖已有名字", keep[0].get("caption"), "模型猜的名字")
        IV._cache.clear()
        over = [{"caption": "模型猜的名字", "content": "旧的", "url": "/f.png"}]
        st4 = IV.enrich_images(over, overwrite_names=True)
        check("（正面）overwrite_names=True 时名字被视觉顶掉", over[0].get("caption"), "验收流程图")
        check("（正面）覆盖计入 named", st4["named"], 1)
        IV._cache.clear()
        same = [{"caption": "验收流程图", "content": "旧的", "url": "/g.png"}]
        st5 = IV.enrich_images(same, overwrite_names=True)
        check("同名不算改动（免得白写库）", st5["named"], 0)

        # 反面：模型挂了也不能炸，条目原样留着，失败要计数
        def boom(data, mime, *, model=None):
            raise RuntimeError("模型不可用")

        IV.describe = boom
        IV._cache.clear()
        bad = [{"content": "原样", "url": "/c.png"}]
        st2 = IV.enrich_images(bad)
        check("（反面）模型报错时条目原样保留", bad[0], {"content": "原样", "url": "/c.png"})
        check("（反面）失败被计数", st2["failed"], 1)

        # 反面：读不出文件也算失败，不算"没图"
        IV.read_image = lambda url: None
        st3 = IV.enrich_images([{"content": "x", "url": "/d.png"}])
        check("（反面）读不到文件计入 failed", [st3["failed"], st3["described"]], [1, 0])
    finally:
        IV.read_image, IV.describe = real_read, real_desc
        IV._cache.clear()


# ---------------------------------------------------------------- 4. 提示词与入口
def section_4() -> None:
    print("\n4) 提示词契约 / 视觉入口 / 凭证扫描件不进【建议图片】")
    check("告诉模型 alt 里就是图题", "方括号里的就是这个文件的图题" in SYSTEM_PROMPT, True)
    check("仍明令不许拿文件名当图名", "绝对不要把文件名当图名" in SYSTEM_PROMPT, True)
    check("圆括号里的才是文件名（别再指向 `![图](…)` 整体）",
          "`docx_0009.png`" in SYSTEM_PROMPT and "圆括号里的" in SYSTEM_PROMPT, True)
    import app.services.llm as LLM

    check("chat_with_image 已删除（视觉入口收编到 image_vision）",
          hasattr(LLM.LLMClient, "chat_with_image"), False)
    check("（正面）image_vision.describe 在", hasattr(IV, "describe"), True)
    from app.core.config import settings

    check("视觉开关在配置里（可关）", hasattr(settings, "image_vision_enabled"), True)

    # 凭证扫描件不进【建议图片】（kind 过滤）。一正两反——只有反面就是假绿：
    # 真把整段图片建议都关掉，反面那条也会"通过"。
    from app.agents.writer_agent import _build_context

    # 夹具一律带 url：没有 url 的条目现在会被挡（见下面那条），
    # 不带 url 的话这几条正向断言全是假绿。
    U = "/uploads/parsed/1/images/"
    ctx = _build_context(
        {"title": "第二节 佐证材料"}, [],
        [{"images": [{"caption": "营业执照扫描件", "content": "公司营业执照", "kind": "scan",
                      "url": U + "a.png"},
                     {"caption": "系统总体架构图", "content": "分层架构", "kind": "diagram",
                      "url": U + "b.png"},
                     {"caption": "监控平台界面截图", "content": "指标看板", "kind": "screenshot",
                      "url": U + "c.png"}]}],
        [], domain="lowalt")
    check("scan 不进【建议图片】", "营业执照扫描件" in ctx, False)
    check("（正面）diagram 照旧进", "系统总体架构图" in ctx, True)
    check("（正面）screenshot 不误伤（软件界面是正经配图）", "监控平台界面截图" in ctx, True)

    # 没有 url = 模型从正文描述编出来的空壳，素材里没这张图 → 不能进【建议图片】，
    # 否则写手照着写一个必然落空的 [图片占位]（项目 4 成稿 8 处占位里 6 处是这么来的）。
    ctx_shell = _build_context(
        {"title": "第四节 关键指标"}, [],
        [{"images": [{"caption": "第三方CNAS环境适应性检测证明", "content": "通常对应…"},
                     {"caption": "飞行器姿态抗扰动数据曲线图", "content": "推测为…"}]}],
        [], domain="lowalt")
    check("（反面）没有 url 的空壳不进【建议图片】", "第三方CNAS环境适应性检测证明" in ctx_shell, False)
    check("（正面）同章里带 url 的照旧进（不是把整段图片建议都关了）",
          "系统总体架构图" in _build_context(
              {"title": "第五节 混装"}, [],
              [{"images": [{"caption": "飞行器姿态抗扰动数据曲线图", "content": "推测为…"},
                           {"caption": "系统总体架构图", "content": "分层架构", "kind": "diagram",
                            "url": U + "b.png"}]}], [], domain="lowalt"), True)

    # 老数据没有 kind 字段（回填后才有的），不能因此被误挡
    ctx_old = _build_context(
        {"title": "第三节 老数据"}, [],
        [{"images": [{"caption": "历史遗留的图", "content": "没有 kind 字段", "url": U + "d.png"}]}],
        [], domain="lowalt")
    check("（正面）没有 kind 的老条目照旧进", "历史遗留的图" in ctx_old, True)


# ---------------------------------------------------------------- 5. 真实数据
def section_5() -> None:
    """错位的回归哨：2.2.1 节那三张图，图名必须钉对。

    ⚠ 拿真文档跑，慢（~15s）。文档不在库/文件不在盘上就跳过并**明确说出来**，
       不静默通过——静默通过是假绿。
    """
    print("\n5) 真实数据：错位那三张图的图名钉对了没有")
    rows = query("SELECT id, file_name, file_path FROM knowledge_doc"
                 " WHERE file_name LIKE %s LIMIT 1", ("%实飞测试验证外协%",))
    if not rows:
        print("  [--] 库里没有那份文档，跳过（不是通过）")
        return
    from app.core.config import settings
    from app.services.docx_parser import chapters_from_docx

    doc = rows[0]
    src = Path(settings.uploads_dir) / doc["file_path"]
    if not src.exists():
        print(f"  [--] 源文件不在盘上：{src}，跳过（不是通过）")
        return
    with tempfile.TemporaryDirectory() as td:
        cap: dict[str, str] = {}
        chapters_from_docx(src, out_dir=Path(td), captions_out=cap)
        n_files = len(list((Path(td) / "images").glob("*")))
    check("这一册确实认出了图题（否则下面全是假绿）", len(cap) >= 2, True)
    # 编号 2026-10-16 由 0002/0003 后移一位：表 10（多场地无人机测试协同作业）排在
    # 2.2.1 节前面，表格图现在也占编号了。**错位哨要的就是这个具体数字**，别改成"找找看"。
    check("docx_0003.png 是「实飞测试现场（一）」而不是流程图",
          cap.get("docx_0003.png"), "无人机实飞测试现场（一）")
    check("docx_0004.png 是「实飞测试现场（二）」",
          cap.get("docx_0004.png"), "无人机实飞测试现场（二）")
    # 表格里的图：改前全丢（doc1 盘上 50 张），改后 64 张。少了这 14 张，
    # 写手照图题写下的 `[图片占位：缺陷管理系统操作界面]` 就永远插不上。
    check("（正面）表格里的 14 张图都导出了（改前 50 张，改后 64）", n_files, 64)
    check("（正面）表内图拿到了真图题：缺陷管理系统操作界面(一)",
          cap.get("docx_0034.png"), "缺陷管理系统操作界面(一)")
    check("（正面）同一单元格里的第二张也没串位",
          cap.get("docx_0035.png"), "缺陷管理系统操作界面(二)")


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

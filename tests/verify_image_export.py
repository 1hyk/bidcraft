"""校验「导出时把 [图片占位：图名] 换成真图」这条链（手工跑，非 pytest）。

用法：D:\\bidcraft\\.venv\\Scripts\\python.exe tests/verify_image_export.py

背景（2026-10-16）：写手**按设计从不插真图**（`writer_agent.py:9`：「历史图属于别的项目，
与数字同理不照搬」），只在需配图处写 `[图片占位：图名/用途]`；全链路没有任何代码把它变成图片
（`content_migrate.migrate_tdz_html` 只是把它包成 `<mark>` 红字）。于是成稿里留着刺眼的占位。

改法：导出时按图名去**本章**的图片索引里查文件，查到就插真图 + 灰图注，查不到保持红字。
索引来自 `image_ownership.project_image_index`——与写手同源（同 `_page_ids`、同 `image_name`、
同样扣掉让给别章的图名），所以插进去的一定是写手当初看到的那张图。

检查（每条反面都配一条正面——只有反面的话，"不该出现"是假绿）：
  1. 索引本身：空壳（无 url）不进、文件不在盘上不进、同名取先出现的、让给别章的不进；
  2. 换图：独占段 / 段落内混文本 / 列表项 三种形状都换得掉，且不吞掉周围的字；
  3. 不换：空壳图名、文件缺失、索引为空 → 红字原样留着，且**没有凭空多出图**；
  4. 图注是灰字且字面就是图名；
  5. 证伪：把替换分支短路，上面每条正面都必须变红；
  6. 没配上的占位：红字后面跟一句灰字，措辞按 `_pending_kind` 分类（实证类"需补真实材料"
     vs 其余"需补图"）——原来的红字只说了"缺图"，分不清"缺真件"还是"程序没找到"；
  7. 文末《本稿待补图清单》：`_collect_pending` 按文档顺序收、代码块里的不算、
     已配上的不进；`_add_pending_list` 空清单一个字不加。
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import lxml.html                                                          # noqa: E402
from docx import Document                                                 # noqa: E402
from docx.shared import Pt                                                # noqa: E402

from app.core.config import settings                                      # noqa: E402
from app.core.docx_style import DocxStyle                                 # noqa: E402
from app.services import docx_export as DX                                # noqa: E402
from app.services import image_ownership as IO                            # noqa: E402

ok = True

#: 夹具落在这个子目录里（照 `verify_mermaid_export.py` 的 `_verify_mermaid` 惯例）。
#: 用 settings.uploads_dir 而不是 ROOT/"uploads"——索引那边走的就是前者，两边必须同一个根。
FIXTURE_DIR = Path(settings.uploads_dir).resolve() / "parsed" / "_verify_image"
FIXTURE_URL = "/uploads/parsed/_verify_image/"

REAL = "系统总体架构图"            # 真图（有文件）
REAL2 = "系统总体架构图"           # 同名同章的第二张：平局取先出现的
PAREN = "无人机实飞测试现场（一）"   # 图名**自己带全角括号**（真实库里就有两条）
SHELL = "飞行器姿态抗扰动数据曲线图"   # 空壳：模型从描述性文字里编的，没有 url
GONE = "盘上没有的一张图"           # 有 url，但文件不在盘上
#: 没配上的**结构类**图名——用来把「需补真实材料」与「需补图」两种措辞分开。
#: 名字里**不能**出现 流程/架构 之外的实证词，否则分类器答对了也证明不了什么。
STRUCT_MISS = "尚未配上的示意图"

#: `docx_export._pending_kind` 的两个返回值（**不带括号**，清单里用的就是它）；
#: `_add_text` 就地那句灰字是把它裹上中文括号。两者都要钉，别只钉一头。
KIND_EVIDENCE = "需补真实材料"
KIND_PLAIN = "需补图"
NOTE_EVIDENCE = f"（{KIND_EVIDENCE}）"
NOTE_PLAIN = f"（{KIND_PLAIN}）"


def check(label: str, got, want) -> None:
    global ok
    good = got == want
    ok = ok and good
    print(f"  [{'OK' if good else '!!'}] {label}: {got!r}" + ("" if good else f" != 期望 {want!r}"))


def _make_png(name: str, color: str) -> Path:
    from PIL import Image

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    p = FIXTURE_DIR / name
    Image.new("RGB", (200, 120), color).save(p)     # 200x120 横图，不会触发按高缩窄
    return p


#: 一页知识页。`images` 就是 `knowledge_page.content` 里那个数组。
def _page(pid: int, images: list[dict]) -> dict:
    return {"id": pid, "images": images}


def _fixture_nodes_pages():
    """节点/知识页夹具：101 是容器章，102 是它的子章，两章**同页**（归属表要裁决）；
    103 单独一页，图名有、文件没有。"""
    nodes = [
        {"id": 101, "title": "第三章 测试验证", "parent_id": None, "sort_order": 1,
         "_page_ids": [11]},
        {"id": 102, "title": "第一节 单元测试", "parent_id": 101, "sort_order": 1,
         "_page_ids": [11]},
        {"id": 103, "title": "第二节 现场测试", "parent_id": None, "sort_order": 2,
         "_page_ids": [12]},
    ]
    pages = {
        11: _page(11, [
            {"caption": REAL, "url": FIXTURE_URL + "real.png", "kind": "diagram"},
            {"caption": REAL2, "url": FIXTURE_URL + "real2.png", "kind": "diagram"},
            {"caption": PAREN, "url": FIXTURE_URL + "paren.png", "kind": "photo"},
            {"caption": SHELL, "content": "此处结合上下文推测为…"},     # 无 url = 空壳
        ]),
        12: _page(12, [{"caption": GONE, "url": FIXTURE_URL + "nope.png"}]),
    }
    return nodes, pages


# ---------------------------------------------------------------- 1. 索引
def section_1() -> dict:
    print("\n1) 索引：空壳/缺文件/平局/让位")
    real = _make_png("real.png", "blue")
    real2 = _make_png("real2.png", "green")
    paren = _make_png("paren.png", "orange")
    check("夹具文件在位（否则下面全是假绿）",
          [real.is_file(), real2.is_file(), paren.is_file()], [True, True, True])

    IO_bak = IO._nodes_and_pages
    IO._nodes_and_pages = lambda pid: _fixture_nodes_pages()
    try:
        index = IO.project_image_index(1)
    finally:
        IO._nodes_and_pages = IO_bak

    own102 = index.get(102) or {}
    check("（正面）子章拿到了图名", REAL in own102, True)
    check("（正面）平局取先出现的那个文件", own102.get(REAL), real.resolve())
    check("（正面）名字自带全角括号的照旧进索引", own102.get(PAREN), paren.resolve())
    check("（反面）空壳（无 url）不进索引", SHELL in own102, False)
    check("（反面）容器章让位给子章（同页同名不再各配一张）", index.get(101), None)
    check("（反面）有图名但文件不在盘上 → 不进索引", index.get(103), None)

    return {"images": own102, "real": real}


# ---------------------------------------------------------------- 2/3. 换图 / 不换
def _render(payload: dict, images) -> tuple[Document, str]:
    """走真实链路：`_normalize_body`（含 migrate_tdz_html）→ `_walk_block`。"""
    html = DX._normalize_body(payload)
    root = lxml.html.fromstring(f"<div>{html}</div>")
    doc = Document()
    DX._walk_block(doc, DocxStyle(), root, base_level=1, images=images)
    return doc, "\n".join(p.text for p in doc.paragraphs)


def _pics(doc: Document) -> int:
    return len(doc.inline_shapes)


def _notes(doc: Document) -> list[str]:
    """正文里那几句**待补图提示**的字面。

    ⚠ 不能直接收"所有灰字 run"——**配上了的图注也是灰字**（同一个 `GRAY`），
    拿全部灰字去断言"没有提示"必然假红（第一版就是这么红的）。
    """
    out = []
    for p in doc.paragraphs:
        for r in p.runs:
            c = r.font.color
            if c is not None and c.type is not None and str(c.rgb) == str(DX.GRAY):
                if r.text in (NOTE_EVIDENCE, NOTE_PLAIN):
                    out.append(r.text)
    return out


def section_2(ctx: dict) -> None:
    print("\n2) 换图：三种形状都换得掉，且不吞掉周围的字")
    images, real = ctx["images"], ctx["real"]

    doc, text = _render({"format": "md",
                         "body": "## 系统架构\n\n本项目总体架构如下所示。\n\n"
                                 f"[图片占位：{REAL}]\n\n以上为总体架构。\n"}, images)
    check("（正面·独占一段）图进来了", _pics(doc) >= 1, True)
    check("（正面）占位文字没了", "[图片占位" in text, False)
    check("（正面）上下文两句话都还在",
          ("本项目总体架构如下所示。" in text, "以上为总体架构。" in text), (True, True))

    doc, text = _render({"format": "html",
                         "body": f"<p>如下图所示：[图片占位：{REAL}]</p>"}, images)
    check("（正面·段内混文本）图进来了", _pics(doc) >= 1, True)
    check("（正面）同一段的其它字没被吞", "如下图所示：" in text, True)

    doc, text = _render({"format": "html",
                         "body": f"<ul><li>[图片占位：{REAL}]</li></ul>"}, images)
    check("（正面·列表项）图进来了（列表结构没被插爆）", _pics(doc) >= 1, True)
    check("（正面）列表符号还在", "• " in text, True)

    # 图注：灰字、字面就是图名
    caps = [r for p in doc.paragraphs for r in p.runs if r.text == REAL]
    check("（正面）图注 run 在", len(caps) >= 1, True)
    check("（正面）图注是灰字", caps and str(caps[0].font.color.rgb), str(DX.GRAY))
    check("（正面）图注的字号是表内字号", caps and caps[0].font.size,
          Pt(DocxStyle().table_size_pt))


def section_3(ctx: dict) -> None:
    print("\n3) 不换：空壳 / 缺文件 / 索引为空 —— 红字原样留着，不凭空多出图")
    images = ctx["images"]
    real = ctx["real"]
    base = _render({"format": "md", "body": f"[图片占位：{REAL}]\n"}, images)
    base_pics = _pics(base[0])
    check("（对照）带索引的这一条是会出图的", base_pics >= 1, True)

    for label, name in (("空壳图名", SHELL), ("文件不在盘上", GONE)):
        doc, text = _render({"format": "md", "body": f"[图片占位：{name}]\n"}, images)
        check(f"（反面·{label}）红字占位仍在", f"[图片占位：{name}]" in text, True)
        check(f"（反面·{label}）没有凭空插图", _pics(doc), 0)

    doc, text = _render({"format": "md", "body": f"[图片占位：{REAL}]\n"}, None)
    check("（反面·没有索引）红字占位仍在", "[图片占位" in text, True)
    check("（反面·没有索引）没有凭空插图", _pics(doc), 0)

    doc, text = _render({"format": "md", "body": f"[图片占位：{REAL}]\n"}, {})
    check("（反面·空索引）红字占位仍在", "[图片占位" in text, True)
    check("（反面·空索引）没有凭空插图", _pics(doc), 0)

    # 「图名/用途」的写法：只拿斜杠前的图名去查
    doc, text = _render({"format": "md", "body": f"[图片占位：{REAL}/用于说明分层]\n"}, images)
    check("（正面）「图名/用途」按斜杠前查得到", _pics(doc) >= 1, True)
    check("（正面）用途那半截被换掉了", "/用于说明分层" in text, False)

    # 图名**自带全角括号**：先整串精确匹配，别拿 `（` 把名字腰斩。
    # 真数据上踩到过——`无人机实飞测试现场（一）`被切成`无人机实飞测试现场`，永远查不到。
    doc, text = _render({"format": "md", "body": f"[图片占位：{PAREN}]\n"}, images)
    check("（正面）名字带全角括号也能查到", _pics(doc) >= 1, True)
    check("（正面）图注是完整图名，不是被腰斩的半截",
          PAREN in [r.text for p in doc.paragraphs for r in p.runs], True)
    doc, text = _render({"format": "md", "body": f"[图片占位：{PAREN}/佐证实飞]\n"}, images)
    check("（正面）带括号的图名 + 斜杠用途也好使", _pics(doc) >= 1, True)

    # 代码块里的占位是字面内容，不该被动
    doc, text = _render({"format": "md", "body": f"```\n[图片占位：{REAL}]\n```\n"}, images)
    check("（反面·代码块）字面占位不换", _pics(doc), 0)
    check("（反面·代码块）原文留着", "[图片占位" in text, True)


# ---------------------------------------------------------------- 4. 索引构建不挡导出
def section_4() -> None:
    """打桩打的是**取料那一步**（`_nodes_and_pages`），不是 `_project_images` 本身
    ——把整个函数换掉只是在测 monkeypatch 会不会抛，跟这条链没关系。"""
    print("\n4) 索引取料失败不挡导出（退回空索引 = 占位保持红字）")
    bak = IO._nodes_and_pages

    def boom(pid):
        raise RuntimeError("库挂了")

    IO._nodes_and_pages = boom
    try:
        got = DX._project_images(1)
        check("（反面）取料炸了 → 空的索引，不往外抛", got, {})
    finally:
        IO._nodes_and_pages = bak

    # 正面：库正常时必须真拿到东西——否则上面那条"返回 {}"可能是恒等 {} 的假绿
    IO._nodes_and_pages = lambda pid: _fixture_nodes_pages()
    try:
        got2 = DX._project_images(1)
    finally:
        IO._nodes_and_pages = bak
    check("（正面）库正常时索引非空（上面那条不是恒等 {}）", bool(got2), True)


# ---------------------------------------------------------------- 5. 证伪
def section_5(ctx: dict) -> None:
    """把替换分支短路，正面必须全垮——不垮就是这些断言没在测东西。"""
    print("\n5) 证伪：短路替换分支后，正面断言必须变红")
    images = ctx["images"]
    real_re = DX._PLACEHOLDER_RE
    DX._PLACEHOLDER_RE = re.compile(r"(?!x)x")          # 永不匹配 → 等于关掉替换
    try:
        doc, text = _render({"format": "md", "body": f"[图片占位：{REAL}]\n"}, images)
        check("（证伪）短路后不出图了", _pics(doc), 0)
        check("（证伪）短路后占位留在正文里", "[图片占位" in text, True)
        doc2, _ = _render({"format": "html",
                           "body": f"<p>如下图所示：[图片占位：{REAL}]</p>"}, images)
        check("（证伪）段内那条也垮了", _pics(doc2), 0)
        # 灰字提示与换图在同一个 finditer 里，短路必须一起垮——只垮一半说明它挂在别处
        doc3, _ = _render({"format": "md", "body": f"[图片占位：{SHELL}]\n"}, images)
        check("（证伪）短路后灰字提示也没了", _notes(doc3), [])
    finally:
        DX._PLACEHOLDER_RE = real_re


# ---------------------------------------------------------------- 6. 未命中就地灰字
def section_6(ctx: dict) -> None:
    """没配上的占位 → 红字照旧 + 后面一句灰字说清"下一步干什么"。

    这句是本节唯一的产出：**红字原来只说了"缺图"**，看到的人分不清是自己要补一张示意图、
    还是要去要一份第三方检测报告。
    """
    print("\n6) 未命中：红字 + 灰字，措辞按类分")
    images, real = ctx["images"], ctx["real"]

    # 正面：命中的那条**不该**有灰字——灰字是"没配上"的记号，配上了还留着就是噪音
    doc, text = _render({"format": "md", "body": f"[图片占位：{REAL}]\n"}, images)
    check("（对照·命中）图进来了", _pics(doc) >= 1, True)
    check("（对照·命中）没有灰字提示", _notes(doc), [])

    doc, text = _render({"format": "md", "body": f"[图片占位：{SHELL}]\n"}, images)
    check("（反面·空壳）红字占位仍在", f"[图片占位：{SHELL}]" in text, True)
    check("（反面·空壳）灰字是「需补真实材料」", _notes(doc), [NOTE_EVIDENCE])
    check("（反面·空壳）没有凭空插图", _pics(doc), 0)

    doc, text = _render({"format": "md", "body": f"[图片占位：{STRUCT_MISS}]\n"}, images)
    check("（反面·结构类）灰字是「需补图」", _notes(doc), [NOTE_PLAIN])

    # 图名带 `/用途`：分类器只看图名那半截，别被用途里的字带偏
    doc, _ = _render({"format": "md",
                      "body": f"[图片占位：{STRUCT_MISS}/用于说明现场布置]\n"}, images)
    check("（反面）带 `/用途` 时不被用途里的「现场」带偏",
          _notes(doc), [NOTE_PLAIN])

    # 两种"空"含义不同，**别当成同一个**：
    #   `{}` = 本章没有可用图 → 和空壳一样要提示（`_walk_nodes` 传的就是 `{}`）；
    #   `None` = 字面上下文（`_walk_block` 的 pre 分支：代码块里的占位是内容）→ 一个字都别动。
    doc, text = _render({"format": "md", "body": f"[图片占位：{SHELL}]\n"}, {})
    check("（反面·空索引）红字在", f"[图片占位：{SHELL}]" in text, True)
    check("（反面·空索引）灰字照给", _notes(doc), [NOTE_EVIDENCE])

    doc, text = _render({"format": "md", "body": f"[图片占位：{SHELL}]\n"}, None)
    check("（反面·None 索引）红字在", f"[图片占位：{SHELL}]" in text, True)
    check("（反面·None 索引）一个字都不动（代码块上下文）", _notes(doc), [])

    # 灰字的字号与颜色：与图注同款
    doc, _ = _render({"format": "md", "body": f"[图片占位：{SHELL}]\n"}, images)
    runs = [r for p in doc.paragraphs for r in p.runs if r.text == NOTE_EVIDENCE]
    check("（正面）灰字字号是表内字号", runs and runs[0].font.size, Pt(DocxStyle().table_size_pt))
    check("（正面）灰字颜色 == GRAY", runs and str(runs[0].font.color.rgb), str(DX.GRAY))


# ---------------------------------------------------------------- 7. 文末清单
def _fixture_doc():
    """文档级夹具：一棵两层的树 + 各自的正文 + 只配上一半的索引。

    顺序断言靠的就是"101 在 102 之前"（先序）——这正是 `_walk_nodes` 写正文的顺序。
    """
    tree = [{"id": 101, "title": "第三章 测试验证",
             "children": [{"id": 102, "title": "第一节 单元测试", "children": []}]}]
    contents = {
        101: {"format": "html",
              "body": f"<p>[图片占位：{REAL}]</p><p>[图片占位：{SHELL}]</p>"},
        102: {"format": "md",
              # 代码块里的占位是**字面内容**（`_walk_block` 的 pre 分支刻意不换），
              # 不该混进清单——否则正文里明明是代码，清单却让人去补图
              "body": f"```\n[图片占位：{GONE}]\n```\n\n[图片占位：{STRUCT_MISS}]\n"},
    }
    # 索引是**全书**平表（2026-10-16 起不再按章分），故这里没有节点那一层。
    index = IO.ProjectImages({REAL: Path("x.png")})
    return tree, contents, index


def section_7(ctx: dict) -> None:
    print("\n7) 文末《本稿待补图清单》")
    tree, contents, index = _fixture_doc()

    got = DX._collect_pending(tree, contents, index)
    check("（正面）按先序收齐未命中的两处",
          [(g["chapter"], g["name"], g["kind"]) for g in got],
          [("第三章 测试验证", SHELL, KIND_EVIDENCE),
           ("第一节 单元测试", STRUCT_MISS, KIND_PLAIN)])
    check("（反面）已配上的图不进清单", any(g["name"] == REAL for g in got), False)
    check("（反面）代码块里的占位不进清单", any(g["name"] == GONE for g in got), False)

    # 索引取料失败（{}）= 全部算未命中，与正文表现一致
    allp = DX._collect_pending(tree, contents, {})
    check("（正面）空索引时三处未命中的都在", len(allp), 3)
    check("（反面）空索引时代码块里的仍不进", any(g["name"] == GONE for g in allp), False)

    # 追加：非空 → 段数增加且条目都在；空 → **一个字都不加**
    doc = Document()
    before = len(doc.paragraphs)
    DX._add_pending_list(doc, DocxStyle(), got)
    text = "\n".join(p.text for p in doc.paragraphs)
    check("（正面）清单追加后段数变多", len(doc.paragraphs) > before, True)
    check("（正面）标题带处数", f"（共 {len(got)} 处）" in text, True)
    for g in got:
        check(f"（正面）{g['name']} 在清单里",
              f"《{g['chapter']}》：{g['name']}（{g['kind']}）" in text, True)

    doc2 = Document()
    n0 = len(doc2.paragraphs)
    DX._add_pending_list(doc2, DocxStyle(), [])
    check("（反面）空清单不加任何段（也不留空标题）", len(doc2.paragraphs), n0)

    # 证伪：把"查得到"做成恒真，清单必须变空——否则上面那条顺序断言可能只是恒等
    ok_lookup = DX._lookup_name
    DX._lookup_name = lambda raw, images: (raw, "假装有文件")
    try:
        check("（证伪）查得到就一条都不留", DX._collect_pending(tree, contents, index), [])
    finally:
        DX._lookup_name = ok_lookup
    check("（回正）换回真判定后清单又有了", len(DX._collect_pending(tree, contents, index)), 2)


# ---------------------------------------------------------------- 8. 全书索引 + 归一化
def section_8() -> None:
    """2026-10-16 改的两条：**查全书**（跨章能查到、全书只插一次）与**归一化回退**
    （图名只差图号前缀/括号序号/空白标点时也认得）。

    为什么改：项目 4 里那两张实飞现场照片判给了 `#288`，而写占位的是 `#287`——
    287 的索引里压根没有这张图，连查的机会都没有，整本一张都没出；
    另有存量正文写的是改名前的短名（`无人机实飞测试现场`），库里已是 `…（一）`。
    """
    print("\n8) 全书索引：跨章查得到 + 全书只插一次；归一化：差个前缀/括号也认得")
    paren = _make_png("paren8.png", "orange")
    arch = _make_png("arch8.png", "purple")
    other = _make_png("other8.png", "black")
    ARCH_NAME = "系统总体架构图"          # 索引里有、占位里不写的另一张图
    FLOW_NAME = "研发测试验证流程图"       # 「名字相近但不是同一张」那条用

    # 两章：101 是拿到图的章（它没写占位），102 是写占位的章——**这正是项目 4 的形状**。
    tree = [{"id": 101, "title": "第三节 开发测试验证流程", "children": []},
            {"id": 102, "title": "第二节 阶段性交付物清单", "children": []}]

    def render(index):
        contents = {
            101: {"format": "md", "body": "本章叙述，没有占位。\n"},
            102: {"format": "md", "body": f"[图片占位：{PAREN}]\n"},
        }
        doc = Document()
        DX._walk_nodes(doc, DocxStyle(), tree, contents, level=1, index=index)
        return doc, "\n".join(p.text for p in doc.paragraphs)

    # —— 正面：图在别的章匹配到的页上，占位这一章照样查得到
    doc, text = render(IO.ProjectImages({PAREN: paren}))
    check("（正面·跨章）别的章匹配到的图，本章写的占位也插得上", _pics(doc), 1)
    check("（正面·跨章）红字占位没了", "[图片占位" in text, False)

    # —— 反面（对照）：索引里确实没有这张图时就不该插 —— 证明上面那条不是恒真
    doc, _ = render(IO.ProjectImages({ARCH_NAME: arch}))
    check("（反面·对照）索引里没有这张图 → 一张都不插", _pics(doc), 0)

    # —— 全书去重：两章各写同一个图名的占位 → 只插一次，第二处留红字
    tree2 = [{"id": 201, "title": "第一节 甲", "children": []},
             {"id": 202, "title": "第二节 乙", "children": []}]
    doc = Document()
    DX._walk_nodes(doc, DocxStyle(), tree2,
                   {201: {"format": "md", "body": f"[图片占位：{PAREN}]\n"},
                    202: {"format": "md", "body": f"[图片占位：{PAREN}]\n"}},
                   level=1, index=IO.ProjectImages({PAREN: paren}))
    check("（正面·去重）同一个图名全书只插一次", _pics(doc), 1)
    check("（正面·去重）第二处仍是红字占位",
          "\n".join(p.text for p in doc.paragraphs).count("[图片占位"), 1)

    # —— 归一化：占位名与索引名只差"图号前缀/括号序号/空白标点"
    #: (说明, 正文里写的, 索引里存的) —— 索引里存的始终是**规范名**，正文写的是变体
    norm_cases = [
        ("差括号序号", "无人机实飞测试现场", PAREN),
        ("差图号前缀", f"图1：{PAREN}", PAREN),
        ("差空白与标点", f"　{PAREN} ，", PAREN),
    ]
    for label, written, indexed in norm_cases:
        doc = Document()
        DX._walk_nodes(doc, DocxStyle(), tree,
                       {102: {"format": "md", "body": f"[图片占位：{written}]\n"}},
                       level=1, index=IO.ProjectImages({indexed: paren}))
        check(f"（正面·归一化）{label}也能查到", _pics(doc), 1)
        check(f"（正面·归一化）{label}图注用索引里的规范名",
              indexed in [r.text for p in doc.paragraphs for r in p.runs], True)

    # —— 反面：**不是同一张图就不许靠相似度凑**（这条是本节的底线）
    doc = Document()
    DX._walk_nodes(doc, DocxStyle(), tree,
                   {102: {"format": "md", "body": "[图片占位：测试执行与风险控制闭环流程图]\n"}},
                   level=1, index=IO.ProjectImages({FLOW_NAME: other}))
    check("（反面·不同图）名字相近但不是同一张 → 不插", _pics(doc), 0)

    # —— 证伪：把归一化关掉（恒等），上面三条"归一化"正面必须全垮
    ok_norm = IO.normalize_image_name
    IO.normalize_image_name = lambda s: (s or "").strip()
    try:
        doc = Document()
        DX._walk_nodes(doc, DocxStyle(), tree,
                       {102: {"format": "md", "body": f"[图片占位：无人机实飞测试现场]\n"}},
                       level=1, index=IO.ProjectImages({PAREN: paren}))
        check("（证伪）关掉归一化后，差括号序号那条必须查不到", _pics(doc), 0)
    finally:
        IO.normalize_image_name = ok_norm

    # —— 两个切分正则必须同口径（一个切图名、一个归一到图名，分了家就会各查各的）
    check("正文侧与索引侧的切分正则同口径",
          DX._NAME_SPLIT_RE.pattern, IO._NAME_SPLIT_RE.pattern)


def section_9() -> None:
    print("\n9) 同图异名去重与歧义保护")
    a = _make_png("identity-a.png", "red")
    b = _make_png("identity-b.png", "blue")
    copied = FIXTURE_DIR / "identity-copy.png"
    shutil.copyfile(a, copied)
    index = IO.ProjectImages({"现场照片甲": a, "现场照片乙": copied, "独立图片": b})
    check("首次可复用", index.take("现场照片甲")[1], a)
    check("相同像素不同文件与名字不能重复", index.take("现场照片乙")[1], None)
    check("不同图片仍可插入", index.take("独立图片")[1], b)
    check("新导出重置去重状态", index.fork().take("现场照片乙")[1], copied)
    layers = IO.ProjectImages({"系统架构图（应用层）": a})
    check("不把数据层误插成应用层", layers.take("系统架构图（数据层）")[1], None)
    check("完整括号图名加用途正常匹配",
          layers.take("系统架构图（应用层）/系统说明")[1], a)
    photos = IO.ProjectImages({"现场照片（一）": a, "现场照片（二）": b})
    check("省略序号后有两张候选则不猜", photos.take("现场照片")[1], None)
    check("带序号仍精确命中", photos.take("现场照片（二）")[1], b)
    nodes = [{"id": 1, "_page_ids": [1]}]
    IO._with_captions(nodes, {1: {"images": [
        {"caption": "空壳图片"},
        {"caption": "扫描附件", "url": "/uploads/a.png", "kind": "scan"},
        {"caption": "可用图片", "url": "/uploads/b.png"},
    ]}})
    check("归属候选与写手过滤一致", nodes[0]["captions"], ["可用图片"])


def main() -> int:
    ctx = section_1()
    section_2(ctx)
    section_3(ctx)
    section_4()
    section_5(ctx)
    section_6(ctx)
    section_7(ctx)
    section_8()
    section_9()
    shutil.rmtree(FIXTURE_DIR, ignore_errors=True)
    print("\n" + ("全部通过" if ok else "**有检查未通过**"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

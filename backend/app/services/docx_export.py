"""Word 交付件导出（工作台「导出」按钮 → POST /api/export/{project_id}/docx）。

正文在 section_content.content 里是 **md / html 混存**：
  - AI 生成走 section_service.persist_one → format="md"（原始 Markdown）
  - 用户在编辑器保存走 PUT /api/sections/content/{id} → format="html"（Tiptap 序列化）
故导出前统一归一到 HTML，再走本模块受控子集的 HTML→docx 遍历器（见 _render_html）。

排版规格**不再是本模块的常量**：每个项目在「排版」抽屉里配一份，存在
project.docx_style（JSON），导出时读出来。规格的取值域、默认值与防御式解析都在
app/core/docx_style.py；本模块只消费 DocxStyle 对象。

约定：所有依赖排版的私有函数都**显式收一个 style 参数**并用它取值（不接受隐式
的模块级状态）——本模块跑在 FastAPI 的线程池里，模块级可变全局会串号；而显式
透传能让"这行字为什么是这个字号"在调用点上就看得见。
默认档（工程标书常见）就是第 1 轮用户拍板的那套：
  A4 / 上下 2.54cm / 左右 3.17cm；
  正文 宋体 小四(12pt) 1.5 倍行距 首行缩进 2 字符；
  标题 黑体 三号(16pt)/四号(14pt)/小四(12pt)；表内 宋体 五号(10.5pt) 单倍行距；
  页眉 项目名，页脚 居中页码。

**页码与目录是 Word 的「域」**：服务端只写域代码，真实页码由 Word 打开时计算
（python-docx 不做分页计算，服务端拿不到真实页数）——这正是「页数控制」只能靠
导出后回填真实页数的原因。目录域带 dirty 标记，Word 打开会提示更新。

依赖：python-docx（已装）+ markdown（md→HTML）+ lxml（随 python-docx）。
**唯一的例外是 Mermaid 图**：它要一个浏览器把它渲成位图，走 `app/services/mermaid_render.py`
（headless Chrome 截图）。那条链路**找不到浏览器就自动降级**成红字占位，不影响其它任何正文，
所以本模块照旧是"不装什么也能导出"。
"""
from __future__ import annotations

import datetime as _dt
import re
import tempfile
from html import unescape as html_unescape
from pathlib import Path

import lxml.html
import markdown as _md
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.core import docx_style
from app.core.config import settings
from app.core.database import query, query_one
from app.core.docx_style import DocxStyle
from app.services.content_migrate import migrate_tdz_html
from app.services.section_service import get_tree

# ---- 与排版无关的固定量 ----

#: 封面/目录/各级标题一律黑体（不随正文字体走）；西文一律 Times New Roman。
HEI = "黑体"
ASCII_FONT = "Times New Roman"

WARN_RED = RGBColor(0xC0, 0x39, 0x2B)   # [待补充]/[图片占位] 等需人工处理的标记
GRAY = RGBColor(0x80, 0x80, 0x80)

#: Mermaid 图嵌进 Word 的宽度上限（与 _add_image 的 14cm 同源：正文栏宽约 14.6cm）。
#: 纵长图（一张竖着排的流程图）按纵横比往下缩到不顶穿一页——A4 正文可用高约 24cm，
#: 留 19cm 给图，剩下的够放段前段后与可能的一行说明。
MERMAID_MAX_W_CM = 14.0
MERMAID_MAX_H_CM = 19.0
# 关系图优先留出上下文空间；照片等普通素材仍使用原来的 19cm 上限。
MERMAID_PREFERRED_H_CM = 17.0

# 树深度 + 正文内标题级别可能很深（实测正文有 #### ），全量映射会让 300+ 条
# 深层小节挤进目录页；四级封顶：四～六级统一按四级排版，目录只取 1-3 级。
MAX_HEAD_LEVEL = 9  # Word 支持九级；不能把正文更深层标题全部压成四级。

# markdown 扩展：tables 管 | 表格，fenced_code 管 ``` 围栏。
# 不用 sane_lists——它要求列表前必须空行，而正文里「**标题**」紧跟「* 项」是常态。
_MD_EXTS = ["tables", "fenced_code"]

# 写手按设计**从不插真图**（`writer_agent.py:9`：「历史图属于别的项目，不照搬」），
# 只在需要配图处写 `[图片占位：图名/用途]`。`content_migrate.migrate_tdz_html` 把它包成
# `<mark>` 红字，导出后就是一个刺眼的占位——导出时按图名把它换成真图，见 `_add_text`。
# 不认 `<mark>` 只认文本：`<mark>` 里也可能有别的字（用户在 Tiptap 里高亮过的正文）。
_PLACEHOLDER_RE = re.compile(r"\[图片占位[：:]\s*([^\]]*)\]")
# `[图片占位：图名/用途]` 里只有图名能对上索引，用途是给写手看的。
# 切法沿用 `tests/verify_image_ownership.py` 已有的口径。
# ⚠ **只能当"退而求其次"的备选**：真实图名自己就带全角括号
# （`无人机实飞测试现场（一）`），拿 `（` 硬切会把名字腰斩成 `无人机实飞测试现场`，
# 于是永远查不到——真数据上实测踩到过。所以先整串精确匹配，不行再按分隔符逐段回退。
_NAME_SPLIT_RE = re.compile(r"[/／，,（(]")

# 没配上的占位要分两句说：**实证类**是"得去找真实材料"，其余是"缺一张图"。
# 两者给的是完全不同的下一步动作——说反了，拿到稿子的人会去 AI 生成一张"CNAS 检测证明"。
#
# ⚠ 这两条与 `writer_agent._IMG_SOURCE_RULE` 是**同一套判据的两个形态**（那边是给模型看的
# 中文散文，这边是可执行的词表）：改判据要两边一起改，`tests/verify_pending_images.py`
# 钉这条同源关系。
#
# **结构词优先**：`材料进场流程图` 里有「材料」但它是结构类——所以先问结构词，再问实证词。
_STRUCTURAL_RE = re.compile(
    r"流程|架构|组织|时序|因果|进度|判定|层级|分类|模式|方案|方法|规划|结构|关系|导图|矩阵|拓扑"
)
_EVIDENCE_RE = re.compile(
    r"截图|扫描|证明|报告|照片|实景|现场|合同|执照|身份证|证书|证照|票据|机房|大屏|仪表|界面"
    r"|数据|曲线|图表|客户|佐证|材料|摘录|文档|实物"
)


def _pending_kind(name: str) -> str:
    """未配上的占位属于哪一类 → 就地在红字后面跟的那句灰字。

    只决定**措辞**，不决定画不画——画不画在提示词侧（`writer_agent._IMG_SOURCE_RULE`）。
    默认落「需补图」：说成"缺一张图"而实际该补材料，代价小于反过来。
    """
    return "需补真实材料" if (_EVIDENCE_RE.search(name) and not _STRUCTURAL_RE.search(name)) else "需补图"


# ================================================================ 对外入口

def export_project_docx(
    project_id: int, style: DocxStyle | None = None
) -> tuple[Path, str]:
    """导出项目为 .docx，返回 (临时文件路径, 建议的下载文件名)。

    排版规格（含封面文档名）只有一个来源：项目的 docx_style。style 参数只给
    测试/调试用，传了就跳过读库——不给「第二份规格」，免得同一个文档名有两处能改。
    """
    proj = query_one(
        "SELECT name, project_type FROM project WHERE id = %s", (project_id,)
    )
    if not proj:
        raise ValueError("项目不存在")

    tree = get_tree(project_id)
    if not tree:
        raise ValueError("项目还没有目录树，请先完成目录生成再导出")

    if style is None:
        style = docx_style.get_project_style(project_id)

    contents = _load_contents(project_id)
    from app.services.document_checks import resolve_requirement_refs, internal_references
    if any(internal_references(content.get('body') or '') for content in contents.values()):
        from app.services.compilation_requirements import load_requirements
        requirements = load_requirements(project_id)
        contents = {nid: {**content, 'body': resolve_requirement_refs(content.get('body') or '', requirements)}
                    for nid, content in contents.items()}
    from app.core.generation_scope import node_ids

    allowed = node_ids(tree)
    contents = {nid: content for nid, content in contents.items() if nid in allowed}

    doc = Document()
    _setup_page(doc.sections[0], style)
    _setup_styles(doc, style)

    _add_cover(doc, style, proj.get("name") or "未命名项目")

    # 目录独占一节：封面与目录不带页眉页脚
    doc.add_section(WD_SECTION.NEW_PAGE)
    _add_toc(doc, style)
    # 评分索引节点已停用（2026-10-27）：历史项目里残留的 scoring_index 节点
    # 不再导出前置索引表；正文遍历也会跳过它们（见 _walk_nodes）。

    # 正文节：页眉项目名 + 页脚页码，页码从 1 重新起算（目录不占正文页码）
    body = doc.add_section(WD_SECTION.NEW_PAGE)
    _setup_page(body, style)
    _add_header(body, style, proj.get("name") or "")
    _add_page_footer(body, style)
    _restart_page_numbering(body, 1)

    # 两件整篇预处理都在遍历前做完（按项目算一次，别在递归里按章重算）：
    # 预热 Mermaid（起一次浏览器就够整篇用）+ 取图名索引。
    index = _project_images(project_id)
    _warm_mermaid(contents)
    try:
        pending = _collect_pending(tree, contents, index)
    except Exception as e:  # noqa: BLE001 清单只是附录，算不出来不该让整篇导不出来
        print(f"[导出] 待补图清单统计失败，本次不附清单：{type(e).__name__}: {str(e)[:150]}")
        pending = []

    body_paragraph = len(doc.paragraphs)
    _walk_nodes(doc, style, tree, contents, level=1, index=index)
    if len(doc.paragraphs) > body_paragraph:
        for number, name, paragraph in (
            (900001, '_bc_body_start', doc.paragraphs[body_paragraph]),
            (900002, '_bc_body_end', doc.paragraphs[-1]),
        ):
            start = OxmlElement('w:bookmarkStart')
            start.set(qn('w:id'), str(number)); start.set(qn('w:name'), name)
            end = OxmlElement('w:bookmarkEnd'); end.set(qn('w:id'), str(number))
            target = doc.element.body.xpath('.//w:p')[-1] if name == '_bc_body_end' else paragraph._p
            target.append(start); target.append(end)
    _add_pending_list(doc, style, pending)

    _sweep_old_exports()

    stamp = _dt.date.today().strftime("%Y%m%d")
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", (proj.get("name") or "项目").strip())[:60]
    out = Path(tempfile.gettempdir()) / f"bidcraft_export_{project_id}_{int(_dt.datetime.now().timestamp())}.docx"
    doc.save(str(out))
    return out, f"{safe}_{stamp}.docx"


def _sweep_old_exports(max_age_h: float = 6) -> None:
    """清理临时目录里过期的导出件。

    每次导出落一个 ~200KB 的 .docx 到临时目录，FileResponse 发完不会删——不清理会
    一路堆下去。只删超过 max_age_h 小时的，不会碰到正在下载的那个。
    """
    cutoff = _dt.datetime.now().timestamp() - max_age_h * 3600
    for f in Path(tempfile.gettempdir()).glob("bidcraft_export_*.docx"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:   # 文件被占用（正在下载）/已删除：跳过即可
            pass


# ================================================================ 页面与样式

def _setup_page(section, style: DocxStyle) -> None:
    """A4 + 项目选定的页边距。"""
    section.page_width = Cm(docx_style.PAGE_W_CM)
    section.page_height = Cm(docx_style.PAGE_H_CM)
    section.top_margin = Cm(style.margin_top_cm)
    section.bottom_margin = Cm(style.margin_bottom_cm)
    section.left_margin = Cm(style.margin_left_cm)
    section.right_margin = Cm(style.margin_right_cm)


def _font(target, size_pt: float, ea: str, ascii_: str = ASCII_FONT,
          bold: bool | None = None, color=None) -> None:
    """给 Style 或 Run 设中西文字体。

    python-docx 的 font.name 只写 w:ascii/w:hAnsi，中文要靠 w:eastAsia——
    不设它的话 Word 会用默认中文字体，宋体/黑体规格就失效了。
    """
    f = target.font
    f.name = ascii_
    f.size = Pt(size_pt)
    if bold is not None:
        f.bold = bold
    if color is not None:
        f.color.rgb = color
    rfonts = target._element.get_or_add_rPr().get_or_add_rFonts()
    # 默认模板的标题样式带 *Theme 属性，Word 里主题字体**优先于**显式字体，
    # 不清掉的话中文标题会渲染成主题的等线（而非要求的黑体）。
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        rfonts.attrib.pop(qn(attr), None)
    rfonts.set(qn("w:eastAsia"), ea)
    rfonts.set(qn("w:ascii"), ascii_)
    rfonts.set(qn("w:hAnsi"), ascii_)


def _setup_styles(doc, style: DocxStyle) -> None:
    """正文/标题样式。Normal 不设首行缩进——缩进按段落加，否则目录、表格会跟着缩。"""
    normal = doc.styles["Normal"]
    _font(normal, style.body_size_pt, style.body_font)
    pf = normal.paragraph_format
    pf.line_spacing = style.line_spacing
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)

    specs = list(style.head_specs)
    specs.extend([specs[-1]] * (MAX_HEAD_LEVEL - len(specs)))
    for lvl, (size, ea, bold, before, after) in enumerate(specs, start=1):
        st = doc.styles[f"Heading {lvl}"]
        # 默认模板的标题是主题蓝、且带 *Theme 字体属性，都得压掉
        _font(st, size, ea, ascii_=ea, bold=bold, color=RGBColor(0, 0, 0))
        hpf = st.paragraph_format
        hpf.space_before = Pt(before)
        hpf.space_after = Pt(after)
        hpf.line_spacing = style.line_spacing
        hpf.keep_with_next = True


def _indent(paragraph, style: DocxStyle, chars: int = 2) -> None:
    """首行缩进 N 字符（默认 2）。用 Word 原生的 firstLineChars 而非固定磅值，
    换字号时缩进会跟着变，符合中文排版习惯。

    两个值要一起给：firstLineChars 是"几字符"这个语义，Word（及 WPS）认它；
    firstLine 是按当前字号折算的磅值（1 字符 = 字号磅值 → ×20 得缇），
    给不认 Chars 的老版本兜底。字号可配，所以磅值必须按 style 现算。
    """
    ind = paragraph._p.get_or_add_pPr().get_or_add_ind()
    ind.set(qn("w:firstLineChars"), str(chars * 100))
    ind.set(qn("w:firstLine"), str(int(style.body_size_pt * 20 * chars)))


def _no_indent(paragraph) -> None:
    ind = paragraph._p.get_or_add_pPr().get_or_add_ind()
    ind.set(qn("w:firstLineChars"), "0")
    ind.set(qn("w:firstLine"), "0")


def _para(doc, style: DocxStyle, indent: bool = False, align=None):
    p = doc.add_paragraph()
    if indent:
        _indent(p, style)
    if align is not None:
        p.alignment = align
    return p


# ================================================================ 封面 / 目录 / 页眉页脚

def _add_cover(doc, style: DocxStyle, project_name: str) -> None:
    for _ in range(6):
        doc.add_paragraph()

    p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
    _font(p.add_run(project_name), 26, HEI, ascii_=HEI)   # 一号

    doc.add_paragraph()

    p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
    _font(p.add_run(style.cover_title), 22, HEI, ascii_=HEI)   # 二号

    for _ in range(6):
        doc.add_paragraph()

    p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
    _font(p.add_run(f"编制日期：{_dt.date.today().strftime('%Y 年 %m 月 %d 日')}"),
          style.body_size_pt, style.body_font)

    p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
    _font(p.add_run("本稿由 AI 辅助生成，提交前请人工复核（红色标记为待补充内容）"),
          style.table_size_pt, style.body_font, color=GRAY)


def _add_toc(doc, style: DocxStyle) -> None:
    p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
    size, ea, bold = style.head_specs[0][:3]
    _font(p.add_run("目　　录"), size, ea, ascii_=ea, bold=bold)
    doc.add_paragraph()

    p = _para(doc, style)
    _add_field(
        p,
        ' TOC \\o "1-3" \\h \\z \\u ',
        style,
        placeholder="（在 Word 中按 F9 或右键「更新域」生成目录与页码）",
        dirty=True,
    )


def _add_header(section, style: DocxStyle, project_name: str) -> None:
    section.header.is_linked_to_previous = False
    p = section.header.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    text = style.header_text or project_name      # 规格里留空 = 沿用项目名
    if text:
        _font(p.add_run(text), style.table_size_pt, style.body_font)


def _add_page_footer(section, style: DocxStyle) -> None:
    """页脚：按 footer_format 里的 {page} 拆成「前缀 + 页码域 + 后缀」。

    页码是 Word 的 PAGE 域，服务端算不出真实值——拆出来的只是域前后的静态文字。
    from_dict 已保证 {page} 一定在（缺了会回落到默认格式），所以这里只需按它切一刀。
    """
    section.footer.is_linked_to_previous = False
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pre, _, post = style.footer_format.partition("{page}")
    if pre:
        _font(p.add_run(pre), style.table_size_pt, style.body_font)
    _add_field(p, " PAGE ", style, placeholder="1")
    if post:
        _font(p.add_run(post), style.table_size_pt, style.body_font)


def _restart_page_numbering(section, start: int = 1) -> None:
    """正文节页码从 1 起（封面与目录不占正文页码）。

    w:pgNumType 在 CT_SectPr 的元素顺序里排在 w:cols 之前，直接 append 会落到
    docGrid 后面 —— Word 对这种乱序可能判文档损坏，必须按顺序插到 cols 前。
    """
    pg = OxmlElement("w:pgNumType")
    pg.set(qn("w:start"), str(start))
    section._sectPr.insert_element_before(pg, "w:cols", "w:formProt", "w:vAlign", "w:docGrid")


def _add_field(paragraph, instr: str, style: DocxStyle,
               placeholder: str = "", dirty: bool = False) -> None:
    """插入 Word 域（目录 / 页码）。服务端只写域代码，取值由 Word 计算。"""
    def _fld(kind: str, dirty_flag: bool = False):
        el = OxmlElement("w:fldChar")
        el.set(qn("w:fldCharType"), kind)
        if dirty_flag:
            el.set(qn("w:dirty"), "true")
        run = paragraph.add_run()
        run._r.append(el)
        return run

    _fld("begin", dirty)

    run = paragraph.add_run()
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = instr
    run._r.append(it)

    _fld("separate")
    if placeholder:
        run = paragraph.add_run(placeholder)
        _font(run, style.table_size_pt, style.body_font, color=GRAY)
    _fld("end")


# ================================================================ 正文

def _load_contents(project_id: int) -> dict[int, dict]:
    """一次取全项目的正文，避免逐节查询。"""
    rows = query(
        "SELECT n.id AS node_id, c.content AS cj FROM chapter_node n"
        " LEFT JOIN section_content c ON c.chapter_node_id = n.id"
        " WHERE n.project_id = %s",
        (project_id,),
    )
    out: dict[int, dict] = {}
    for r in rows:
        cj = r.get("cj")
        if not cj:
            continue
        import json
        try:
            out[r["node_id"]] = json.loads(cj) if isinstance(cj, str) else (cj or {})
        except (TypeError, ValueError):
            out[r["node_id"]] = {}
    return out


def _normalize_body(payload: dict | None) -> str:
    """正文 → HTML：md 走 markdown 库，html 原样；最后统一把 [待补充]/[图片占位]
    包成 <mark>（复用 content_migrate，与前端 wrapTdzAsMark 同一套正则）。"""
    if not payload:
        return ""
    body = (payload.get("body") or "").strip()
    if not body:
        return ""
    from app.services.customer_voice import strip_internal_notes
    # 导出前最后一道保险：历史正文或手工编辑里残留的内部审阅提示，不能进成稿。
    body = strip_internal_notes(body)
    fmt = (payload.get("format") or "html").lower()
    # 与前端 markdownToHtml 的自动识别保持一致：标了 md 但内容已是 HTML 的（历史数据）按 HTML 处理
    if fmt == "md" and not body.startswith("<"):
        html = _md.markdown(
            _blank_around_tables(_break_before_lists(body)), extensions=_MD_EXTS)
    else:
        html = body
    from app.services.mermaid_source import normalize_html_body

    return migrate_tdz_html(normalize_html_body(html))


# 顶层列表标记（0-3 空格缩进 + *、-、+ 或 1./1) ）；缩进 4+ 的是上一项的嵌套列表，不是"新开列表"
_LIST_START = re.compile(r"^ {0,3}(?:[*+-]|\d{1,9}[.)])(?:\s|$)")
# 任意缩进的列表标记：用来判断"上一行是否已经在列表里"（缩进 4 格的嵌套项也算）
_ANY_LIST_LINE = re.compile(r"^\s*(?:[*+-]|\d{1,9}[.)])(?:\s|$)")
_FENCE = re.compile(r"^ {0,3}(?:```|~~~)")


def _is_table_separator(line: str) -> bool:
    text = line.strip().replace('｜', '|')
    if '|' not in text:
        return False
    cells = [cell.strip() for cell in text.strip('|').split('|')]
    return bool(cells) and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells)


def _table_cells(line: str) -> list[str]:
    text = line.strip().replace('｜', '|').strip('|')
    return [cell.strip() for cell in text.split('|')]


def _table_like(line: str) -> bool:
    """至少两根竖线才当表格行：一行普通文字里偶尔出现一个 | 不应被改写成表。

    全角竖线只在候选表格行里转换，避免改动正文里的普通符号。
    """
    return line.strip().replace('｜', '|').count('|') >= 2


def _blank_around_tables(md: str) -> str:
    """规范 Markdown 表格并补空行，避免表题紧贴表头或模型漏写分隔行时整表变段落。

    - 表题和表头之间没有空行：补空行（原有行为）；
    - 模型只写表头和数据行、漏了 `| --- |`：按表头列数补分隔行；
    - 模型用全角竖线 `｜`：只在表格块内转成半角，正文不受影响；
    - 围栏代码块内一律不处理。
    """
    lines = md.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if not in_fence and _table_like(line):
            run: list[str] = []
            j = i
            while j < len(lines) and lines[j].strip() and _table_like(lines[j]):
                run.append(lines[j].replace('｜', '|'))
                j += 1
            if len(run) >= 2:
                header, second = run[0], run[1]
                columns = len(_table_cells(header))
                if _is_table_separator(second):
                    block = run
                elif columns >= 2 and len(_table_cells(second)) == columns:
                    # 模型漏写分隔行：按表头列数补齐，后面的数据行原样保留。
                    block = [header, '| ' + ' | '.join(['---'] * columns) + ' |', *run[1:]]
                else:
                    block = None
                if block:
                    if out and out[-1].strip():
                        out.append('')
                    out.extend(block)
                    if j < len(lines) and lines[j].strip():
                        out.append('')
                    i = j
                    continue
        out.append(line)
        i += 1
    return '\n'.join(out)


def _break_before_lists(md: str) -> str:
    """给「紧贴上一段落的列表」补一个空行。

    python-markdown 不会为了开列表而断段：正文里「**1. 缴纳状态确认**」紧跟
    「*   **缴纳方式**：…」（中间没有空行）是常态，整块会被当成一个段落，
    星号原样落进 Word。而前端编辑器用的 marked 是 CommonMark，会正常断段开列表
    —— 不补这一步，导出稿就和用户屏幕上看到的不一致。
    围栏代码块内部不处理（那里的星号可能是真的星号）。
    """
    out: list[str] = []
    in_fence = False
    for ln in md.split("\n"):
        if _FENCE.match(ln):
            in_fence = not in_fence
        if (
            not in_fence
            and out
            and out[-1].strip()                        # 上一行非空
            and not _ANY_LIST_LINE.match(out[-1])      # 且上一行不在任何列表里
            and not out[-1][:1].isspace()              # 也不是缩进的续行（如 4 空格嵌套项）
            and _LIST_START.match(ln)
        ):
            out.append("")
        out.append(ln)
    return "\n".join(out)


def _project_images(project_id: int):
    """整个项目的**全书**图名索引 `{图名: 本地文件}`，供把 `[图片占位：X]` 换成真图。

    **一份索引走全篇、整篇共用一个去重状态**（`image_ownership.ProjectImages`）：
    同一个图名整本书只插一次，先写到的章得。2026-10-16 之前是本章各查本章的索引，
    于是"归属章没写这张图"等于整本都没有（项目 4 那两张实飞现场照片就是这样丢的）。

    取料失败一律退回空索引（= 占位保持红字，和以前一样），**绝不让配图把导出带崩**——
    与 `_render_mermaid` / `_add_image` 的"任何失败都不抛"是同一条约定。
    """
    try:
        from app.services.image_ownership import project_image_index_flat

        return project_image_index_flat(project_id)
    except Exception as e:  # noqa: BLE001 读库/读盘任何一步出错都只是"这次不换图"
        print(f"[导出] 图名索引取失败，占位保持原样：{type(e).__name__}: {str(e)[:150]}")
        from app.services.image_ownership import ProjectImages

        return ProjectImages()


def _numbered_title(title: str, level: int, position: int) -> str:
    """无编号的目录标题按同级顺序补号；已有人工编号原样保留。"""
    from app.core.chapter_title import normalize
    compact = re.sub(r"\s+", "", title)
    if normalize(title) != re.sub(r"[“”‘’\"'「」『』]", "", compact):
        return title

    def chinese(n):
        digits = '零一二三四五六七八九'
        if n < 10:
            return digits[n]
        if n < 100:
            tens, ones = divmod(n, 10)
            return (digits[tens] if tens > 1 else '') + '十' + (digits[ones] if ones else '')
        return str(n)

    if level == 1:
        prefix = chinese(position) + '、'
    elif level == 2:
        prefix = '（' + chinese(position) + '）'
    elif level == 3:
        prefix = str(position) + '.'
    elif level == 4:
        prefix = '（' + str(position) + '）'
    elif level == 5:
        prefix = chr(0x2460 + position - 1) if position <= 20 else f'{position}）'
    else:
        prefix = (chr(ord('a') + position - 1) if position <= 26 else str(position)) + ')'
    return prefix + title


def _walk_nodes(doc, style: DocxStyle, nodes: list[dict],
                contents: dict[int, dict], level: int,
                index: dict | None = None) -> None:
    """深度优先写正文。树深度即标题级别，章正文内部的 ## / ### 依次往下排。

    `index`：项目的**全书**图名索引（见 `_project_images`）——**整棵树传同一个对象**，
    别在递归里按章取子集：去重状态（"这个图名插过没有"）挂在它身上，逐章另取一份
    就等于全书去重失效，同一个图名会在多处各插一张。
    """
    position = 0
    for node in nodes:
        from app.services.scoring_index import is_index
        if is_index(node):
            continue
        nid = node.get("id")
        title = (node.get("title") or "").strip()
        if title:
            position += 1
            heading = doc.add_paragraph(_numbered_title(title, level, position), style=f"Heading {min(level, MAX_HEAD_LEVEL)}")
            start=OxmlElement('w:bookmarkStart'); end=OxmlElement('w:bookmarkEnd')
            bookmark_id=str(1000+len(doc.element.body.xpath('.//w:bookmarkStart')))
            start.set(qn('w:id'),bookmark_id); start.set(qn('w:name'),f'bc_node_{nid}')
            end.set(qn('w:id'),bookmark_id)
            heading._p.append(start);heading._p.append(end)

        html = _normalize_body(contents.get(nid))
        if html:
            # `or {}` 不是多余的：`images=None` 是"字面上下文，一个字都别动"（`_walk_block`
            # 的 pre 分支就传 None），而"没有可用图"必须是 `{}`——否则正文里的占位连
            # 「需补真实材料」那句灰字都拿不到，文末清单却把同一处列出来了（两处各说各话）。
            _render_html(doc, style, html, base_level=min(level, MAX_HEAD_LEVEL),
                         drop_title=title or None,
                         auxiliary_headings=bool(node.get('children')),
                         images=index or {})
        elif not (node.get("children") or []):
            # 叶子章没有正文：显式标出来，免得导出稿看起来"漏了"
            p = _para(doc, style)
            _font(p.add_run("[本章尚未生成正文]"), style.body_size_pt, style.body_font,
                  color=WARN_RED)

        _walk_nodes(doc, style, node.get("children") or [], contents, level + 1, index)


def _render_html(doc, style: DocxStyle, html: str, base_level: int = 1,
                 drop_title: str | None = None, images: dict | None = None,
                 auxiliary_headings: bool = False) -> None:
    try:
        root = lxml.html.fromstring(f"<div>{html}</div>")
    except Exception:  # noqa: BLE001 HTML 畸形时退化成纯文本，宁可少格式也别丢内容
        p = _para(doc, style, indent=True)
        _font(p.add_run(re.sub(r"<[^>]+>", "", html)), style.body_size_pt, style.body_font)
        return          # 这条兜底路不过 _walk_block，占位不会被换成图（已知）
    if drop_title:
        from app.core.chapter_title import _PREFIX_RE
        def clean_heading(text):
            for _ in range(8):
                before = text
                text = re.sub(r'^\s*#{1,6}\s*', '', text).strip()
                text = _PREFIX_RE.sub('', text).strip()
                if before == text:
                    break
            return text
        # 富文本标题中有时还残留 Markdown 前缀，先清理，才能识别复述章名。
        for element in root.iter():
            if isinstance(element.tag, str) and re.fullmatch(r'h[1-6]', element.tag.lower()):
                text = clean_heading(element.text_content().strip())
                element.clear()
                element.text = text
        _drop_restated_title(root, drop_title)
        root.set('data-chapter-title', drop_title)
        # 正文内的小标题同样需要编号；仅在章节导出入口启用，独立渲染保持原行为。
        headings = [e for e in root.iter() if isinstance(e.tag, str) and re.fullmatch(r'h[1-6]', e.tag.lower())]
        levels = sorted({int(e.tag[1]) for e in headings})
        counters = {}
        for element in headings:
                clean = element.text_content().strip()
                if auxiliary_headings:
                    # 目录已有独立子章；父章正文的小标题仅作段内提示，不能抢占子章编号和目录位置。
                    element.clear()
                    element.tag = 'p'
                    strong = lxml.html.Element('strong')
                    strong.text = clean
                    element.append(strong)
                    continue
                depth = levels.index(int(element.tag[1])) + 1
                counters[depth] = counters.get(depth, 0) + 1
                counters = {k: v for k, v in counters.items() if k <= depth}
                numbered = _numbered_title(clean, base_level + depth, counters[depth])
                # 导出副本统一编号；保留正文原始内容与数据库里的标题。
                element.clear()
                element.tag = 'h' + str(min(base_level + depth, 9))
                element.set('data-export-heading-level', str(min(base_level + depth, 9)))
                element.text = numbered
    _walk_block(doc, style, root, base_level, images)


def _norm_title(s: str) -> str:
    return re.sub(r"正文$", "", re.sub(r"\s+", "", s or "")).replace("：", ":").lower()


def _same_title(a: str, b: str) -> bool:
    """两个标题是不是同一节（用于判断正文首行是否只是复述节点标题）。"""
    from app.core.chapter_title import normalize
    na, nb = _norm_title(normalize(a)), _norm_title(normalize(b))
    if not na or not nb:
        return False
    if na == nb:
        return True
    # 实测有一例：节点标题「第二节 相关资质证书（如ISO体系认证…）」、正文写简称
    # 「第二节 相关资质证书」——一方是另一方的前缀，前 6 字（含序号）一致即认。
    return na.startswith(nb[:6]) and (na.startswith(nb) or nb.startswith(na))


def _drop_restated_title(root, node_title: str) -> None:
    """删掉正文开头的「复述标题」。

    撰写模型习惯在正文首行再写一遍章节标题（实测 56 章里 55 章如此），而节点标题
    已经作为上一级标题输出过，不删就会连着出现两遍同样的字。
    只看第一个元素：正文一旦真正开始（首个非空块不是标题），就不再判断。
    """
    for el in root:
        if not isinstance(el.tag, str):
            continue
        if el.tag.lower() in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if _same_title(el.text_content(), node_title):
                el.getparent().remove(el)
            return
        if el.text_content().strip():   # 首个有内容的块不是标题 → 正文已开始
            return


def _walk_block(doc, style: DocxStyle, parent, base_level: int,
                images: dict | None = None) -> None:
    """受控子集的块级遍历器：只处理实际会出现的标签，未知块按段落兜底。

    `images`：本章「图名 → 文件」索引，一路透传给 `_add_inline`（换掉 `[图片占位：X]`）。
    """
    # 图题是图片的附属段落，不按普通正文缩进排版。仅处理紧邻图片的明确图题，
    # 不把正文中“图：……”的普通说明误搬到别处。
    # 只认紧邻表格的明确表题，不把章节标题或普通说明搬作表题。
    for table in list(parent):
        if table.tag != 'table':
            continue
        for candidate in (table.getprevious(), table.getnext()):
            if candidate is None or candidate.tag != 'p':
                continue
            text = candidate.text_content().strip()
            match = re.fullmatch(r'表\s*[：:]\s*(\S[^\n]{0,100})', text)
            if not (match or candidate.get('data-table-caption') == 'true' or
                    (len(text) <= 60 and text.endswith(('表', '模板')) and
                     len(candidate) == 1 and candidate[0].tag in ('strong', 'b'))):
                continue
            for child in list(candidate):
                candidate.remove(child)
            candidate.text = match.group(1).strip() if match else text
            candidate.set('data-table-caption', 'true')
            table.addprevious(candidate)
            break
    claimed_captions = set()
    for figure in list(parent):
        if not isinstance(figure.tag, str):
            continue
        tag = figure.tag.lower()
        is_figure = (tag == 'img' or
                     (tag == 'pre' and _mermaid_code(figure) is not None) or
                     (tag == 'div' and 'mermaid-block' in (figure.get('class') or '')) or
                     (tag == 'p' and len(figure) == 1 and figure[0].tag == 'img'
                      and not figure.text_content().strip()))
        if not is_figure:
            continue
        found_caption = False
        for candidate in (figure.getprevious(), figure.getnext()):
            if candidate is None or candidate.tag != 'p' or candidate in claimed_captions:
                continue
            match = re.fullmatch(r'图\s*[：:]\s*(\S[^\n]{0,150})', candidate.text_content().strip())
            if not match and candidate.get('data-figure-caption') != 'true':
                continue
            from app.services.image_caption import print_caption
            caption_text = print_caption(match.group(1).strip() if match
                                         else candidate.text_content().strip())
            candidate.clear()
            candidate.text = caption_text
            candidate.set('data-figure-caption', 'true')
            figure.addnext(candidate)
            claimed_captions.add(candidate)
            found_caption = True
            break
        if not found_caption and (tag == 'pre' or tag == 'div'):
            # 老正文未带图名：只从现有标题取主题，不编造项目事实、不调用模型。
            from app.core.chapter_title import normalize
            from lxml.html import Element
            topic = parent.get('data-chapter-title') or ''
            for preceding in parent.iter():
                if preceding is figure:
                    break
                if isinstance(preceding.tag, str) and re.fullmatch(r'h[1-9]', preceding.tag.lower()):
                    topic = preceding.text_content().strip()
            topic = re.sub(r'^[①-⑳]|^[a-z]\)', '', normalize(topic)).strip() or '方案'
            code = _mermaid_code(figure) if tag == 'pre' else figure.get('data-code') or figure.text or ''
            kind = next((label for pattern, label in [
                (r'\bgantt\b', '进度计划图'), (r'\bsequenceDiagram\b', '时序图'),
                (r'\bmindmap\b', '思维导图'), (r'\bclassDiagram\b', '类关系图'),
                (r'\berDiagram\b', '实体关系图'), (r'\bstateDiagram', '状态转换图'),
            ] if re.search(pattern, code or '')), '流程图')
            caption = Element('p')
            caption.set('data-figure-caption', 'true')
            stem = kind[:-1]
            caption.text = topic if topic.endswith('图') else topic + ('图' if topic.endswith(stem) else kind)
            figure.addnext(caption)
            claimed_captions.add(caption)
    for el in parent:
        if not isinstance(el.tag, str):      # 注释等非元素节点
            continue
        tag = el.tag.lower()

        if re.fullmatch(r'h[1-9]', tag):
            lvl = int(el.get('data-export-heading-level')) if el.get('data-export-heading-level') else min(base_level + int(tag[1]) - 1, MAX_HEAD_LEVEL)
            p = doc.add_paragraph(style=f"Heading {lvl}")
            _add_inline(p, style, el, images=images)

        elif tag == "p":
            marker = _PLACEHOLDER_RE.fullmatch(el.text_content().strip()) if images is not None else None
            name, path = _lookup_name(marker.group(1), images) if marker else ('', None)
            if path is not None:
                # 独占段落的历史图片使用图下独立居中题注；不改段内混排的上下文。
                p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
                p.paragraph_format.first_line_indent = Cm(0)
                try:
                    p.add_run().add_picture(str(path), width=Cm(_fit_width_cm(path, style)))
                    caption = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
                    caption.paragraph_format.first_line_indent = Cm(0)
                    caption.paragraph_format.line_spacing = 1.0
                    _font(caption.add_run(name), style.table_size_pt, style.body_font, color=GRAY)
                    note=_image_source_note(marker.group(1),images)
                    if note:
                        _font(caption.add_run('\n'+note),style.table_size_pt,style.body_font,color=GRAY)
                except Exception:
                    _run(p, '[图片占位：'+name+']', False, False, True)
            elif el.get('data-table-caption') == 'true':
                p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
                p.paragraph_format.first_line_indent = Cm(0)
                p.paragraph_format.keep_with_next = True
                _font(p.add_run(el.text_content()), style.table_size_pt, style.body_font)
            elif el.get('data-figure-caption') == 'true':
                from app.services.image_caption import print_caption
                p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
                p.paragraph_format.first_line_indent = Cm(0)
                p.paragraph_format.line_spacing = 1.0
                _font(p.add_run(print_caption(el.text_content())), style.table_size_pt, style.body_font, color=GRAY)
            else:
                _add_inline(_para(doc, style, indent=True), style, el, images=images)

        elif tag in ("ul", "ol"):
            _render_list(doc, style, el, images=images)

        elif tag == "table":
            _render_table(doc, style, el, images=images)

        elif tag == "div" and "mermaid-block" in (el.get("class") or ""):
            _render_mermaid(doc, style, el.get("data-code") or el.text or "")

        elif tag == "img":
            _add_image(doc, None, el, style)

        elif tag == "pre":
            mm = _mermaid_code(el)
            if mm is not None:                          # ```mermaid 围栏 → 画成图
                _render_mermaid(doc, style, mm)
            else:
                p = _para(doc, style, indent=True)
                _add_inline(p, style, el, collapse=False)   # 代码块里的换行有含义，不折
                for run in p.runs:
                    _font(run, style.table_size_pt, style.body_font, ascii_="Consolas")

        elif tag == "hr":
            _add_rule(doc)

        elif tag == "blockquote":
            p = _para(doc, style, indent=True)
            p.paragraph_format.left_indent = Cm(0.74)
            _add_inline(p, style, el, images=images)

        elif tag in ("div", "section", "article", "body", "html"):
            _walk_block(doc, style, el, base_level, images)

        else:
            _add_inline(_para(doc, style, indent=True), style, el, images=images)


def _add_rule(doc) -> None:
    """<hr>（正文里的 --- 分隔线）→ 一条细横线。

    Word 没有独立的分隔线对象，用段落的**下边框**实现。w:pBdr 在 CT_PPr 的
    元素顺序上排在 w:ind 之前，故插到 pPr 首位，不能 append（顺序错 Word 会报文档损坏）。
    """
    p = doc.add_paragraph()
    _no_indent(p)
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "999999")
    pbdr.append(bottom)
    p._p.get_or_add_pPr().insert(0, pbdr)


def _render_list(doc, style: DocxStyle, el, level: int = 0,
                 images: dict | None = None) -> None:
    """列表：编号自己写、缩进自己加，不用 Word 的 List Number 样式。

    原因：python-docx 的「List Number」样式全篇共用同一个 w:numId，Word 把它当成
    一个列表——227 个有序项会跨章一路连续编号（第 5 章从 130 开始）。自写编号
    保证每个列表都从 1 起；悬挂缩进让折行与首行文字对齐。
    """
    ordered = el.tag.lower() == "ol"
    for idx, li in enumerate(el.findall("li"), start=1):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.left_indent = Cm(0.74 * (level + 1))
        pf.first_line_indent = -Cm(0.74)     # 悬挂：编号凸出，折行缩进对齐
        # 先写内容再插编号：这样"段首空白"判定（见 _run）看到的是空段落，
        # 松散列表 <li>\n<p>… 折出来的那个空格才不会被留下来顶出双空格
        _add_inline(p, style, li, images=images)   # 嵌套列表在 _add_inline 里被跳过
        marker = p.add_run(f"{idx}. " if ordered else "• ")
        _font(marker, style.body_size_pt, style.body_font)
        p._p.insert(1, marker._r)             # pPr 固定是第 0 个子元素，插到它后面即最前
        for sub in li:                        # 再逐层渲染嵌套列表
            if isinstance(sub.tag, str) and sub.tag.lower() in ("ul", "ol"):
                _render_list(doc, style, sub, level + 1, images)


def _lookup_name(raw: str, images) -> tuple[str, object | None]:
    """`[图片占位：…]` 里那一段 → `(图名, 文件)`；查不到返回 `("", None)`。

    匹配本身全在 `image_ownership.ProjectImages.take`（三级：整串精确 → 按分隔符逐段回退
    → 归一化后相等，外加**全书去重**），这里只做一次包装。

    `images` 传进来的有两种：导出链路给的是 `ProjectImages`（全书索引 + 整篇共用的去重状态，
    **同一个对象一路透传**，去重才跨章生效）；夹具与旧调用给的是普通 `{图名: 文件}`——
    就地包一层，匹配口径因此只有一份实现，不会两边分家。包装是**单次**的：
    普通 dict 不携带跨次状态，同一个图名在同一篇里出现两次会各插一张（旧口径如此）。
    """
    if not images:
        return "", None
    from app.services.image_ownership import ProjectImages

    if not isinstance(images, ProjectImages):
        images = ProjectImages(images)
    from app.services.image_caption import print_caption
    name,path=images.take(raw)
    return print_caption(name),path


def _image_source_note(raw,images):
    if '|自定义图注=1' not in raw:return ''
    bound=re.search(r'素材ID=(IMG-[0-9a-f]{24})',raw)
    asset=getattr(images,'assets',{}).get(bound.group(1)) if bound else None
    if not asset or not asset.get('generated'):return ''
    # 导出件是给客户看的正式稿，不标注「AI生成示意」；数据示例的免责说明仍然保留。
    return '数据仅作示例，非实测结果' if '数据仅作示例，非实测结果' in str(asset.get('caption')) else ''


def _lookup_raw(raw: str) -> str:
    """`[图片占位：…]` 里那一段 → 光图名（切掉 `/用途`）。**只给分类器看，不查索引。**

    口径与 `_lookup_name` 的回退分支一致（第一个分隔符之前那截）。图名自带全角括号的
    （`无人机实飞测试现场（一）`）在这里会被切短，但那截照样带着「现场」——
    分类器只认词，切短不影响它答对。
    """
    raw = (raw or "").strip()
    raw = re.split(r"\|\s*素材ID", raw, maxsplit=1)[0].strip()
    m = _NAME_SPLIT_RE.search(raw)
    return raw[:m.start()].strip() if m else raw


def _add_text(paragraph, style: DocxStyle, text: str, bold: bool, italic: bool, red: bool,
              collapse: bool, images: dict | None) -> None:
    """落一段文本，顺手把其中的 `[图片占位：图名]` 换成真图。

    `images` 是**本章**的「图名 → 本地文件」索引（`image_ownership.project_image_index`
    取出的那一份，与写手看到的图完全同源）。两种"空"含义不同、**不能混**：
      - `None` = 这一段是**字面上下文**（`_walk_block` 的 `pre` 分支：代码块里的占位是内容），
        一个字都不动；
      - `{}` = 本章没有可用图 → 占位照旧是红字 + 灰字提示（`_walk_nodes` 传的就是它）。

    **命不中就原样落红字**：占位是"这里缺一张图"的显式记号，静默吞掉会让成稿看起来
    完整、实际漏图。反过来说，能插上就说明这张图确实属于本章、文件也真在盘上。

    红字后面再跟一句**灰字**（`_pending_kind` 给措辞）——原来的红字只说了"缺图"，
    分不清"缺真件"还是"程序没找到"：实证类的下一步是**去找真实材料**（现场照片、界面截图、
    检测报告），结构类的下一步才是补一张图。灰字只在导出件上，不落库、不动编辑器里的正文。
    文末还会按同一口径附一张《本稿待补图清单》（`_collect_pending` / `_add_pending_list`）。

    图注（灰字）跟在图后面、同一段里 —— 与 `_add_image` 的行内分支同款；`_add_inline`
    拿不到 `doc`，另起一个居中段落要绕 oxml，不值得为一个排版细节开这条路。
    """
    if images is None or not text or "[图片占位" not in text:
        _run(paragraph, text, bold, italic, red, collapse=collapse)
        return
    pos = 0
    for m in _PLACEHOLDER_RE.finditer(text):
        if m.start() > pos:
            _run(paragraph, text[pos:m.start()], bold, italic, red, collapse=collapse)
        name, path = _lookup_name(m.group(1), images)
        from app.services.image_caption import print_caption
        visible_name = print_caption(
            re.split(r"\|\s*素材ID", m.group(1), maxsplit=1)[0].strip())
        if path is None:
            _run(paragraph, f"[图片占位：{visible_name}]", bold, italic, red=True, collapse=collapse)
            _font(paragraph.add_run(f"（{_pending_kind(_lookup_raw(m.group(1)))}）"),
                  style.table_size_pt, style.body_font, color=GRAY)
        else:
            try:
                paragraph.add_run().add_picture(str(path), width=Cm(_fit_width_cm(path, style)))
                _font(paragraph.add_run(name), style.table_size_pt, style.body_font, color=GRAY)
                note=_image_source_note(m.group(1),images)
                if note:_font(paragraph.add_run('\n'+note),style.table_size_pt,style.body_font,color=GRAY)
            except Exception:  # noqa: BLE001 图片损坏/格式不认，退回红字占位而不是丢图
                _run(paragraph, f"[图片占位：{visible_name}]", bold, italic, red=True, collapse=collapse)
                _font(paragraph.add_run(f"（{_pending_kind(_lookup_raw(m.group(1)))}）"),
                      style.table_size_pt, style.body_font, color=GRAY)
        pos = m.end()
    if pos < len(text):
        _run(paragraph, text[pos:], bold, italic, red, collapse=collapse)


def _add_inline(paragraph, style: DocxStyle, el, bold: bool = False, italic: bool = False,
                red: bool = False, collapse: bool = True,
                images: dict | None = None) -> None:
    """行内递归：文本落成 run，strong/em/mark 累积格式，br 断行，img 就地插图。

    `images`：本章的「图名 → 文件」索引，只为把 `[图片占位：X]` 换成真图（见 `_add_text`）。
    一路透传下去——正文里任何位置的占位都该换，不只是顶层段落。
    """
    if el.text:
        _add_text(paragraph, style, el.text, bold, italic, red, collapse, images)
    for child in el:
        if not isinstance(child.tag, str):
            continue
        tag = child.tag.lower()
        if tag == 'span' and child.get('data-page-reference'):
            identity = child.get('data-page-reference')
            if identity.isdigit():
                _add_field(paragraph, f' PAGEREF bc_node_{int(identity)} \\h ', style, placeholder='待更新', dirty=True)
            else:
                _font(paragraph.add_run('[章节引用无效]'), style.body_size_pt, style.body_font, color=WARN_RED)
        elif tag in ("strong", "b"):
            _add_inline(paragraph, style, child, True, italic, red, collapse, images)
        elif tag in ("em", "i"):
            _add_inline(paragraph, style, child, bold, True, red, collapse, images)
        elif tag == "mark":
            _add_inline(paragraph, style, child, bold, italic, True, collapse, images)
        elif tag == "br":
            paragraph.add_run().add_break()
        elif tag == "img":
            _add_image(None, paragraph, child, style)   # 段落内图片：就地插进当前段落
        elif tag in ("ul", "ol"):
            pass       # 嵌套列表由 _render_list 逐层处理，这里取文本会把子项压平进父项
        else:                                  # span / a / code 等：只取文本
            _add_inline(paragraph, style, child, bold, italic, red, collapse, images)
        if child.tail:
            _add_text(paragraph, style, child.tail, bold, italic, red, collapse, images)


_WS = re.compile(r"\s+")


def _run(paragraph, text: str, bold: bool = False, italic: bool = False,
         red: bool = False, collapse: bool = True):
    """写一个 run。

    collapse：HTML 里标签之间的换行/缩进（如 <li>\\n<p>…）不是内容，得按 HTML 的
    规则折成单个空格；否则 python-docx 会把文本里的 "\\n" 变成 <w:br/>，正文里凭空多出空行。
    <pre> 内部不折（那里换行有含义）。
    """
    if not text:
        return None
    if collapse:
        text = _WS.sub(" ", text)
        # 段首的空白（块标签之间的换行折出来的）不该变成前导空格
        if not text.strip() and not any(r.text.strip() for r in paragraph.runs):
            return None
    run = paragraph.add_run(text)
    # 只在需要时置 True——置 False 会盖掉样式继承（黑体标题等）
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    if red:
        run.font.color.rgb = WARN_RED
    return run


def _render_table(doc, style: DocxStyle, table_el, images: dict | None = None) -> None:
    rows = table_el.findall(".//tr")
    if not rows:
        return
    widths = [len(r.findall("th")) + len(r.findall("td")) for r in rows]
    ncols = max(widths) if widths else 0
    if not ncols:
        return

    table = doc.add_table(rows=0, cols=ncols)
    try:
        table.style = "Table Grid"
    except KeyError:      # 模板无该样式时退化为无边框表，不影响内容
        pass
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    explicit_widths = []
    for cell in rows[0].findall('th') + rows[0].findall('td'):
        value = cell.get('colwidth', '')
        explicit_widths.append(int(value) if value.isdigit() and int(value) > 0 else 0)
    if len(explicit_widths) == ncols and all(explicit_widths):
        from docx.shared import Emu
        section = doc.sections[-1]
        available = section.page_width - section.left_margin - section.right_margin
        total = sum(explicit_widths)
        table.autofit = False
        for column, width in zip(table.columns, explicit_widths):
            column.width = Emu(int(available * width / total))

    for r in rows:
        cells = r.findall("th") + r.findall("td")
        row = table.add_row()
        if len(explicit_widths) == ncols and all(explicit_widths):
            for cell, width in zip(row.cells, explicit_widths):
                cell.width = Emu(int(available * width / total))
        for i in range(ncols):
            cell = row.cells[i]
            p = cell.paragraphs[0]
            _no_indent(p)
            pf = p.paragraph_format
            pf.line_spacing = 1.0
            pf.space_before = Pt(0)
            pf.space_after = Pt(0)
            if i < len(cells):
                header = cells[i].tag.lower() == "th"
                _add_inline(p, style, cells[i], bold=header, images=images)
                if header:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for run in p.runs:
                _font(run, style.table_size_pt, style.body_font)

    # 表头跨页重复
    try:
        trPr = table.rows[0]._tr.get_or_add_trPr()
        trPr.append(OxmlElement("w:tblHeader"))
    except Exception:  # noqa: BLE001
        pass


#: 正文里找 Mermaid 源码用的正则（只为**预热**服务，见 _warm_mermaid）。
#: 正文 md / html 混存，两种格式各一条：md 是 ```mermaid 围栏，html 是 data-code 属性。
_MD_MERMAID_RE = re.compile(
    r"^[ \t]*`{3,}[ \t]*mermaid[ \t]*\r?\n(.*?)^[ \t]*`{3,}[ \t]*$", re.S | re.M
)
_HTML_MERMAID_RE = re.compile(r'data-code="([^"]*)"')


def _warm_mermaid(contents: dict[int, dict]) -> None:
    """导出前把正文里的 Mermaid 图**整批**渲掉，让后面的同步遍历全是缓存命中。

    **为什么必须整批**：headless Chrome 光是启动（空白页也是）就要 3.4~8.5s，
    而 mermaid 渲染本身只占 ~0.5s——开销全在起进程上。逐张渲是一张一次启动，
    二三十张图就是三五分钟；`mermaid_render` 把一批图放进同一个页面截一次再切开，
    于是整篇文档的图只花一次启动（详见该模块的说明）。

    这里刻意用正则而不是 lxml/markdown 解析正文：预热只是"提前把可能用到的渲出来"，
    多出（渲了没被引用的图）或漏掉（那张在遍历时按需渲）都不影响正确性，但**快**很重要。
    `html.unescape` 是必需的：data-code 里的 `&amp;`/`&quot;` 不还原，
    拼出来的 key 就和遍历侧（lxml 已还原）对不上，同一张图白渲两遍。
    """
    sources: list[str] = []
    for c in (contents or {}).values():
        body = (c or {}).get("body") or ""
        if not body:
            continue
        fmt = ((c or {}).get("format") or "html").lower()
        if fmt == "md" and not body.startswith("<"):
            sources += [m.group(1) for m in _MD_MERMAID_RE.finditer(body)]
        else:
            sources += [html_unescape(m.group(1)) for m in _HTML_MERMAID_RE.finditer(body)]
    if not sources:
        return
    try:
        from app.services import mermaid_render
        from app.services.mermaid_source import split_mindmap

        sources=[piece for source in sources for piece in split_mindmap(source)]
        got = len(mermaid_render.render_batch(sources))
        print(f"[导出] Mermaid 图 {got}/{len(set(s.strip() for s in sources))} 张就绪")
    except Exception as e:  # noqa: BLE001 预热失败无所谓，遍历时会按需渲
        print(f"[导出] Mermaid 预热跳过：{e}")


# ---- 本稿待补图清单（导出件末尾的附录）----
#
# `_warm_mermaid` 是"遍历前扫一遍全文"，这里是同一个形状的另一种用法：扫出"最后仍然没配上图"
# 的占位，成稿末尾列成一张给人工用的清单。`_add_text` 负责**就地**说清这一点（红字 + 灰字），
# 清单负责**汇总**——拿着稿子去要材料的人不必从头翻到尾。

#: 剥掉代码块再扫占位。`_walk_block` 的 `pre` 分支**刻意不透传 images**
#: （代码块里的 `[图片占位：…]` 是字面内容，不该被换成图），原样扫会把它们误算进清单。
_FENCE_RE = re.compile(r"```.*?```", re.S)
_PRE_RE = re.compile(r"<pre\b.*?</pre>", re.S | re.I)


def _strip_code(body: str) -> str:
    return _PRE_RE.sub(" ", _FENCE_RE.sub(" ", body or ""))


def _collect_pending(tree: list[dict], contents: dict[int, dict],
                     index) -> list[dict]:
    """按**文档顺序**收集最终没配上的占位：`[{"chapter": 章标题, "name": 图名, "kind": 措辞}]`。

    与 `_walk_nodes` 走同一棵树、同一个顺序，判定复用 `_lookup_name`——
    所以「清单里有」⟺「正文里那处还是红字」，两处不会各说各话。
    `index` 取料失败（空）时全部算未命中，与正文的表现一致。

    ⚠ **必须先 `fork()` 一份再消费**：`index` 身上挂着"这个图名插过没有"的去重状态
    （全书一个图名只插一次），而清单跑在正文之前——共用同一份状态就等于先替正文把图
    "插"了一遍，正文里所有占位都会变成红字，清单却一条不留。fork 后从零开始、走的又是
    同一棵树的同一顺序，两处对同一处占位的判定必然一致。

    **不写页码**：服务端算不出真实页数（页码是 Word 的域，见模块 docstring）。
    """
    from app.services.image_ownership import ProjectImages

    look = index.fork() if isinstance(index, ProjectImages) else ProjectImages(index or {})
    out: list[dict] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            nid = node.get("id")
            body = (contents.get(nid) or {}).get("body") or ""
            if body:
                for m in _PLACEHOLDER_RE.finditer(_strip_code(body)):
                    raw = m.group(1)
                    if _lookup_name(raw, look)[1] is None:
                        from app.services.image_caption import print_caption
                        nm = print_caption(_lookup_raw(raw) or raw.strip())
                        out.append({"chapter": (node.get("title") or "").strip(),
                                    "name": nm, "kind": _pending_kind(nm)})
            walk(node.get("children") or [])

    walk(tree or [])
    return out


def _add_pending_list(doc, style: DocxStyle, items: list[dict]) -> None:
    """成稿末尾附《本稿待补图清单》。`items` 为空则**一个字都不加**（不留一个空标题）。

    标题用一级标题样式 → 会进 Word 的目录域，这是想要的：附录该在目录里。
    用 `add_page_break` 而不是新起一节：留在正文节里就自动继承页眉页脚。
    """
    if not items:
        return
    doc.add_page_break()
    doc.add_paragraph(f"附：本稿待补图清单（共 {len(items)} 处）", style="Heading 1")
    p = _para(doc, style)
    _font(p.add_run(
        "下列图片未能在素材中找到，导出件里对应位置仍是红字占位。"
        "「需补真实材料」的一类要另找原件（现场照片、界面截图、检测报告、证照等），"
        "补好后重新生成本章即可。本清单不写页码——服务端算不出真实页数，"
        "请在 Word 里更新目录域后自行核对。"),
        style.body_size_pt, style.body_font, color=GRAY)
    for it in items:
        p = _para(doc, style)
        _font(p.add_run(f"- 《{it['chapter']}》：{it['name']}（{it['kind']}）"),
              style.body_size_pt, style.body_font)


def _mermaid_code(el) -> str | None:
    """`<pre><code class="language-mermaid">…</code></pre>` → Mermaid 源码；不是围栏返回 None。

    **这条路和上面的 `div.mermaid-block` 都必须认**，因为正文两种格式混存：
    AI 生成的是 Markdown（`app/services/section_service.py` 存 format="md"），```
    ```mermaid 经 python-markdown 的 fenced_code 就变成这个 `<pre><code>`；
    而在编辑器里打开并保存过的正文是 Tiptap 序列化的 HTML，走的是 `div.mermaid-block`。
    原先只认后者——所以 **AI 自己写出来的 mermaid 一直落进下面的通用 `pre` 分支，
    被当成代码块用 Consolas 渲染成一片文本**，编辑器里是图、导出件里是字。
    """
    code = el.find("code")
    if code is None:
        return None
    classes = (code.get("class") or "").lower().split()
    if "language-mermaid" not in classes and "mermaid" not in classes:
        return None
    return code.text_content()


def _render_mermaid(doc, style: DocxStyle, code: str) -> None:
    """把一段 Mermaid 源码画成图片嵌进正文；画不出来就退回红字占位。

    **任何失败都不抛**（与 `_add_image`、`mermaid_render.render_cached` 同一条约定）：
    一张图画不出来，不该让整篇标书导不出来。图渲好后按纵横比定宽——
    与普通图片不同，mermaid 的图没有 `width` 属性可用，只能自己从这里卡上限。
    """
    if not code or not code.strip():
        return
    from app.services.mermaid_source import split_mindmap
    pieces=split_mindmap(code)
    if len(pieces)>1:
        for number,piece in enumerate(pieces,1):
            p=_para(doc,style,align=WD_ALIGN_PARAGRAPH.CENTER)
            _font(p.add_run(f'关系图分图 {number}/{len(pieces)}'),style.table_size_pt,style.body_font,color=GRAY)
            _render_mermaid(doc,style,piece)
        return
    path = None
    try:
        from app.services import mermaid_render

        path = mermaid_render.render_cached(code)
    except Exception:  # noqa: BLE001 浏览器缺失/渲染异常都退化成占位
        path = None
    if path is not None:
        try:
            p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
            # 图是一个内联对象，不能继承正文的 1.5/2 倍行距扩大所在行。
            p.paragraph_format.line_spacing = 1.0
            p.paragraph_format.space_after = Pt(4)
            p.add_run().add_picture(str(path), width=Cm(_mermaid_width_cm(path, style)))
            return
        except Exception:  # noqa: BLE001 图片损坏不该让整篇导出失败
            pass
    _mermaid_placeholder(doc, style, code)


def _fit_width_cm(path: Path, style: DocxStyle | None = None) -> float:
    """按纵横比把图宽缩到 MERMAID_MAX_H_CM 之内（读不出尺寸就按满宽，不因它失败）。

    上限这套常量是给 mermaid 定的，但量的是"正文栏能放多宽多高的图"，与图从哪来无关
    ——导出时插的历史插图同样用它卡高度，免得一张竖长图顶穿一页。
    """
    max_w, max_h = MERMAID_MAX_W_CM, MERMAID_MAX_H_CM
    if style is not None:
        max_w = min(max_w, docx_style.PAGE_W_CM - style.margin_left_cm - style.margin_right_cm)
        # 为图注、段落基线留出余量；不把边距较大的页面仍当作默认正文栏。
        max_h = min(max_h, docx_style.PAGE_H_CM - style.margin_top_cm - style.margin_bottom_cm - 1.5)
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
        if w > 0 and h > 0:
            return min(max_w, max_h * w / h)
    except Exception:  # noqa: BLE001
        pass
    return max_w


def _mermaid_width_cm(path: Path, style: DocxStyle | None = None) -> float:
    """（保留旧名）——`tests/verify_mermaid_export.py` 断言的就是这个名字。"""
    width = _fit_width_cm(path, style)
    try:
        from PIL import Image
        with Image.open(path) as im:
            if im.height > 0:
                width = min(width, MERMAID_PREFERRED_H_CM * im.width / im.height)
    except Exception:
        pass
    return width


def _mermaid_placeholder(doc, style: DocxStyle, code: str) -> None:
    """渲染不出来时的红字占位：把图的第一行源码带出来，方便人工定位是哪个图。"""
    first = next((ln.strip() for ln in (code or "").splitlines() if ln.strip()), "")
    p = _para(doc, style, align=WD_ALIGN_PARAGRAPH.CENTER)
    _font(p.add_run(f"[待补图：Mermaid 关系图{('（' + first[:30] + '）') if first else ''}]"),
          style.body_size_pt, style.body_font, color=WARN_RED)


def _add_image(container, paragraph, el, style: DocxStyle) -> None:
    """把 /uploads 图片嵌进文档；取不到就留文字提示，不静默丢图。"""
    src = (el.get("src") or "").strip()
    path = _local_upload_path(src)
    target = paragraph if paragraph is not None else _para(container, style, align=WD_ALIGN_PARAGRAPH.CENTER)
    if not path:
        _font(target.add_run(f"[图片未随导出携带：{src or '无地址'}]"),
              style.table_size_pt, style.body_font, color=WARN_RED)
        return
    try:
        target.add_run().add_picture(str(path), width=Cm(_fit_width_cm(path, style)))
        alt = (el.get("alt") or "").strip()
        if alt:
            cap = (_para(container, style, align=WD_ALIGN_PARAGRAPH.CENTER)
                   if paragraph is None else paragraph)
            _font(cap.add_run(alt), style.table_size_pt, style.body_font, color=GRAY)
    except Exception:  # noqa: BLE001 图片损坏不该让整篇导出失败
        _font(target.add_run(f"[图片无法嵌入：{src}]"),
              style.table_size_pt, style.body_font, color=WARN_RED)


def _local_upload_path(src: str) -> Path | None:
    """/uploads/... → 磁盘路径。越界（目录穿越）或文件不存在返回 None。"""
    s = (src or "").strip()
    if not s:
        return None
    m = re.search(r"/uploads/(.+)$", s)
    if not m:
        return None
    root = settings.uploads_dir.resolve()
    p = (root / m.group(1)).resolve()
    if p != root and root not in p.parents:
        return None
    return p if p.is_file() else None

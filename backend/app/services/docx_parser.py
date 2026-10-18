"""docx 原生解析（python-docx）——对象级切章 + full.md 渲染。

2026-09-10 决策：docx（知识库 knowledge_doc 与项目文件 project_file）统一走 python-docx，
不再"转 md → 正则抠 # 标题"或 docx→PDF→MinerU：

- chapters_from_docx：对象级切章（知识库提炼用），按 Word 样式/图表编号建树，
  段落+表格原位归属，图表区标题(13.图表部分/13-N)归位，杜绝"转 md 吞并"。
- parse_docx：docx → full.md（项目文件/工法索引用，保留真实 # 层级供 file_index 按行取原文）。

.PDF 仍走 MinerU（无样式可读）。
已知取舍（待补）：无页码出处（MinerU 有）。
图片：段内 inline 图导出到 out_dir/images/ 并在文本插引用，由 knowledge_service
_image_index/_attach_image_urls 解析为 /uploads URL 挂到知识页 images.url（可预览）。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

HEADING_STYLES = {
    "Heading 1": 1,
    "Heading 2": 2,
    "Heading 3": 3,
    "Heading 4": 4,
    "Heading 5": 5,
    "Heading 6": 6,
    "标题 1": 1,
    "标题 2": 2,
    "标题 3": 3,
}
TOC_STYLES = {"toc 1", "toc 2", "toc 3", "TOC 1", "TOC 2", "TOC 3", "toc"}

# 图表/章节编号标题：Subtitle / 6-正文 等样式 + 阿拉伯编号（13.图表部分 / 13-1施工…）
# 且不含制表符页码（目录页同编号行带 \t页码，正文标题无 tab）
_CHART_TITLE_RE = re.compile(r"^\s*(\d+(?:[.\-–]\d+)*)\s*[、.．]?\s*\S")
_CHART_TITLE_STYLES = {"Subtitle", "6-正文"}

# ---- 图题识别（2026-10-16）----
# 图题是**确定性可得**的：实测《实飞测试验证外协项目投标文件(正本).docx》，图题段落是
# `[首行缩进|CENTER]`、正文段落一律 `[Normal|JUSTIFY]`——**居中是唯一判别信号**
# （该文档全篇 0 个段落用 Word 题注样式，"题注样式"这条常规路子在本书上不存在）。
# 识别结果写进图片引用的 alt（`![无人机实飞测试现场（一）](docx_0002.png)`），
# 由 knowledge_service._attach_image_urls 取出来当图名——**覆盖模型自己编的名字**。
#
# 为什么必须覆盖：模型有真图题时反而会**错位**。实测同文档 2.2.1 节三张图，
# 模型的三个图名整体后移一格：把上一段的正文话题「端到端测试验证流程图」安给了
# 一张无人机照片，真图题「（一）」「（二）」被推给了后两张。锚点对不上，
# 结果就是"图名写着流程图、画面是无人机"且不报错——比没名字更坏。
_CAPTION_MAX_CHARS = 40


def _caption_after(children: list, i: int, doc) -> str:
    """取第 i 个 body 子元素后面的图题；没有则返回空串。

    只看紧邻的、最多隔一个空段的一格（`range(i+1, i+3)`）：图题紧跟图片，
    中间允许有空段落，遇到正文/标题/另一张图立刻停——再往前看就会把正文当图题。
    """
    from docx.text.paragraph import Paragraph

    for j in range(i + 1, min(i + 3, len(children))):
        if not children[j].tag.endswith("}p"):
            return ""
        p = Paragraph(children[j], doc)
        t = p.text.strip()
        if not t:
            continue                                   # 空段落跳过，不算"遇到正文"
        if p._element.findall(".//" + qn("a:blip")):
            return ""                                  # 下一张图，中间没有图题
        if p.alignment != WD_ALIGN_PARAGRAPH.CENTER:
            return ""
        if len(t) > _CAPTION_MAX_CHARS:
            return ""
        style = p.style.name if p.style else ""
        if style in TOC_STYLES or _heading_level(style, t, p._p.pPr) is not None:
            return ""                                  # 标题不算图题
        return t
    return ""


@dataclass
class DocxChapter:
    """对象级切章产出的章节（字段对齐 heading_agent.ChapterV2 的消费方）。"""
    leaf_title: str            # 叶子标题原文
    title: str                 # 全路径标题："工程概况 > 线路概况"
    path: list[str]            # 祖先标题链（不含自己）
    level: int                 # Word 样式层级（Heading N 的 N）
    text: str = ""             # 该节正文（段落 + 内联表格 md）
    index: int = field(default=0)

    @property
    def body_len(self) -> int:
        """正文有效字符数（去表格分隔符与空白的粗估）。"""
        return len(re.sub(r"[\s|:—\-]+", "", self.text))


def _table_to_markdown(table) -> list[str]:
    """docx 表格 → Markdown 表格行列表（cell 压成单行、补齐列宽、整行全空跳过）。"""
    rows: list[list[str]] = []
    for row in table.rows:
        cells = []
        for c in row.cells:
            t = c.text.strip().replace("\n", " ").replace("\r", " ")
            t = " ".join(t.split())
            cells.append(t)
        if any(cells):
            rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    md = ["| " + " | ".join(rows[0]) + " |"]
    md.append("| " + " | ".join(["---"] * width) + " |")
    md.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return md


def _heading_level(style: str, text: str, pPr=None) -> int | None:
    """标题判定：优先 Word 大纲级别，退回 Heading 样式 / 图表编号文本。

    pPr（段落 pPr 元素）非空时先读 w:outlineLvl（0 基，9=正文）：实测大量投标文件
    用自定义样式（DirectoryLevel0From2 / DirectoryLevel1From4 …）建标题，样式名
    HEADING_STYLES 认不出来，只按样式名判会整层漏掉标题——知识页标题路径（匹配主键）
    全错。大纲级别是 Word 目录的权威依据，自定义样式同样带得全。
    返回 None 表示非标题。图表区标题（13.图表部分 / 13-N…）层级：
    - "13.图表部分" 编号段数 1 → 章级(1)
    - "13-1施工…"    编号段数 2 → 节级(2)
    与 Heading1=章、Heading2=节 对齐，并入同一棵树。
    """
    if pPr is not None:
        ol = pPr.find(qn("w:outlineLvl"))
        if ol is not None:
            v = ol.get(qn("w:val"))
            if v is not None and v.isdigit() and 0 <= int(v) <= 8:
                return int(v) + 1
    if style in HEADING_STYLES:
        return HEADING_STYLES[style]
    if style in _CHART_TITLE_STYLES and text and "\t" not in text:
        m = _CHART_TITLE_RE.match(text)
        if m:
            segs = re.split(r"[.\-–]", m.group(1))
            return max(1, min(len(segs), 6))
    return None


def chapters_from_docx(file_path: Path, min_text_chars: int = 60, out_dir: Path | None = None,
                       on_progress=print, captions_out: dict | None = None) -> list[DocxChapter]:
    """对象级直接切章：按 Word 样式/图表编号建树，段落+表格原位归属。

    返回 DocxChapter 列表（leaf/title 全路径/text 含内联表格 md 与图片引用）。
    正文有效字符 < min_text_chars 的纯标题空壳不产出。

    out_dir：若给，段内 inline 图片**与表格单元格里的图片**都导出到 out_dir/images/，并在所属章节 text 插入
    `![图题或「图」](导出文件名)` 引用——供 knowledge_service._image_index/_attach_image_urls
    解析为 /uploads URL 挂到知识页 images.url（前端可预览）。不给则只切章不导出图。

    captions_out：可选字典，回填 `{导出文件名: 文档里的图题}`（识别不到的图不进去）。
    knowledge_service 拿它把图名钉成**文档原文**，而不是让模型自己编（见 `_caption_after`）。
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    on_progress("[python-docx] 对象级切章（按 Word 样式）")
    doc = Document(str(Path(file_path)))

    img_dir = None
    if out_dir is not None:
        img_dir = Path(out_dir) / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
    img_counter = 0
    captions: dict[str, str] = {}          # 导出文件名 → 文档里的图题

    def export_para_images(p: Paragraph, caption: str = "") -> str:
        """导出段内 inline 图片，返回待插入的 markdown 引用串（多图换行分隔）。

        caption 非空时写进 alt（`![图题](docx_0002.png)`）——alt 就是**文档原文的图题**，
        比模型自己起的名更贴近编制口径。alt 里不能出现 `]`（会截断 markdown），换掉。
        """
        nonlocal img_counter
        if img_dir is None:
            return ""
        label = " ".join((caption or "").split()).replace("[", "（").replace("]", "）") or "图"
        refs = []
        from docx.oxml.ns import qn

        seen = set()
        for r in p.runs:
            for blip in r._element.findall(".//" + qn("a:blip")):
                embed = blip.get(qn("r:embed"))
                if not embed or embed in seen:
                    continue
                seen.add(embed)
                try:
                    part = p.part.related_parts[embed]
                except KeyError:
                    continue
                blob = getattr(part, "blob", None)
                if not blob:
                    continue
                ct = getattr(part, "content_type", "") or ""
                ext = {".png": ".png", ".jpg": ".jpg", "jpeg": ".jpg",
                       "gif": ".gif", "bmp": ".bmp", "webp": ".webp", "tiff": ".tiff"}.get(
                    ct.split("/")[-1], ".png")
                img_counter += 1
                fname = f"docx_{img_counter:04d}{ext}"
                (img_dir / fname).write_bytes(blob)
                if caption:
                    captions[fname] = caption
                refs.append(f"![{label}]({fname})")
        return "\n".join(refs)

    def export_table_images(tbl_el) -> str:
        """导出表格**单元格内**的图片，返回待插入的 markdown 引用串。

        `_table_to_markdown` 只读 `c.text`，单元格里的图会被**静默丢掉**——不报错、
        不计数，只是没了。实测《实飞测试验证外协项目投标文件(正本).docx》：14 张图
        全在 1×1 表格里（实拍现场照 + 软件界面截图），正是写手写实证类占位时要的
        那类；丢了以后写手照着图题写下 `[图片占位：缺陷管理系统操作界面]`，
        图却永远插不上——表里的图题文字还在，图没了。

        表格里的图与正文图**同构**：图段落 + 紧跟一个居中图题段。所以图题判据
        直接复用 `_caption_after`，只是"兄弟列表"由 body 换成单元格的子元素。
        """
        if img_dir is None:
            return ""
        refs: list[str] = []

        def walk(container) -> None:
            kids = list(container.iterchildren())
            for j, el in enumerate(kids):
                if el.tag.endswith("}p"):
                    refs.append(export_para_images(
                        Paragraph(el, doc), caption=_caption_after(kids, j, doc)))
                elif el.tag.endswith(("}tbl", "}tr", "}tc")):
                    walk(el)          # 行 / 单元格 / 单元格里的嵌套表，继续往下找

        walk(tbl_el)
        return "\n".join(r for r in refs if r)

    stack: list[DocxChapter] = []
    out: list[DocxChapter] = []

    def close_top():
        node = stack.pop()
        if node.body_len >= min_text_chars:
            out.append(node)

    children = list(doc.element.body.iterchildren())   # 要向前看一格取图题，先取全
    for i, child in enumerate(children):
        tag = child.tag
        if tag.endswith("}p"):
            p = Paragraph(child, doc)
            style = p.style.name if p.style else ""
            text = p.text.strip()
            img_refs = export_para_images(p, caption=_caption_after(children, i, doc))
            if style in TOC_STYLES:
                continue  # 目录页跳过（含目录图也不导出）
            lvl = _heading_level(style, text, p._p.pPr) if text else None
            if lvl is not None:
                while stack and stack[-1].level >= lvl:
                    close_top()
                parent_titles = [n.leaf_title for n in stack]
                stack.append(DocxChapter(
                    leaf_title=text,
                    title=" > ".join([*parent_titles, text]),
                    path=parent_titles,
                    level=lvl,
                ))
                if img_refs:
                    stack[-1].text += img_refs + "\n"
            else:
                if stack:
                    if text:
                        stack[-1].text += text + "\n"
                    if img_refs:
                        stack[-1].text += img_refs + "\n"
        elif tag.endswith("}tbl"):
            tbl_md = _table_to_markdown(Table(child, doc))
            img_refs = export_table_images(child)   # 表里的图不能丢（见函数注释）
            if stack and tbl_md:
                stack[-1].text += "\n".join(tbl_md) + "\n"
            if stack and img_refs:
                stack[-1].text += img_refs + "\n"

    while stack:
        close_top()
    for i, c in enumerate(out):
        c.index = i
    if captions_out is not None:
        captions_out.update(captions)
    return out


def parse_docx(file_path: Path, out_dir: Path, on_progress=print, write_md: bool = False) -> dict:
    """docx → full.md（保留真实 # 层级，段落+表格原位；项目文件 / 工法索引用）。

    与 chapters_from_docx 同源遍历，但输出为行式 full.md（供 file_index 按行号取原文、
    MinerU 同构下游使用），而非对象级章节列表。标题按 _heading_level 判定转 #，
    Subtitle/6-正文 图表编号标题同样识别，杜绝"吞并"。
    返回: {"markdown", "markdown_path", "images_dir", "parser", "content_list_path": None}
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    file_path = Path(file_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = Document(str(file_path))
    lines: list[str] = []
    heading_count = 0
    for child in doc.element.body.iterchildren():
        tag = child.tag
        if tag.endswith("}p"):
            p = Paragraph(child, doc)
            style = p.style.name if p.style else ""
            text = p.text.strip()
            if not text or style in TOC_STYLES:
                continue
            lvl = _heading_level(style, text, p._p.pPr)
            if lvl is not None:
                lines.append(f"\n{'#' * lvl} {text}\n")
                heading_count += 1
            else:
                lines.append(text)
        elif tag.endswith("}tbl"):
            tbl_md = _table_to_markdown(Table(child, doc))
            if tbl_md:
                lines.extend(["\n", *tbl_md, "\n"])

    md = "\n".join(lines)
    # 2026-09-10 改造：**不再落 full.md**——章节原文随索引一起存进 file_section_index.content，
    # 取原文不再依赖文件按行切。md 文本直接返回给调用方（build_index(md_text=...)）。
    # write_md=True 时仍写盘（兼容需要实体 md 的场景，如工法浏览）。
    if write_md:
        md_path = out_dir / "full.md"
        md_path.write_text(md, encoding="utf-8")
    else:
        md_path = None
    return {
        "markdown": md,
        "markdown_path": md_path,
        "images_dir": None,
        "parser": "python-docx",
        "content_list_path": None,
    }


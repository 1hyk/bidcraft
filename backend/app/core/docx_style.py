"""导出 Word 的排版规格（项目级可配置，工作台「排版」抽屉）。

一张**受控选项表** + 一个 dataclass。面板只让用户在这几个枚举里选，其余的量
（各级标题字号、表格字号、首行缩进、目录层级、封面字号）由正文字号自动推导——
不是偷懒：中文排版里这些量之间有固定的比例关系（标题必须比正文大、表格必须比
正文小），逐项放开只会配出「标题比正文还小」这类废稿。

**字体和字号必须是受控枚举**。选了本机没装的字体会**静默回退**：屏幕上看着对，
打印出来是另一回事——所以要给出「选它一定有效」的清单，而不是一个自由输入框。
字号同理，12pt 和 12.5pt 的差别没人看得出来，但会让「字/页」系数在页数控制里失真。

本模块只描述规格，不 import python-docx；消费方是 app/services/docx_export.py。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

# ---- 受控选项（前端下拉直接读这里，不硬编码） ----

#: 正文字体候选。这几款在中文版 Windows / Office / WPS 上必然存在。
FONT_OPTIONS = ("宋体", "仿宋", "楷体", "黑体", "微软雅黑")

HEI_FONT = "黑体"

#: 正文字号候选：(磅值, 中文字号名)
BODY_SIZE_OPTIONS = (
    (10.5, "五号"),
    (12.0, "小四"),
    (14.0, "四号"),
    (16.0, "三号"),
)

LINE_SPACING_OPTIONS = (1.0, 1.25, 1.5, 2.0)

#: 页边距预设。普通 = 用户拍板的那套（上下 2.54 / 左右 3.17）。
MARGIN_PRESETS = (
    {"key": "normal", "name": "普通", "top": 2.54, "bottom": 2.54, "left": 3.17, "right": 3.17},
    {"key": "narrow", "name": "窄", "top": 1.27, "bottom": 1.27, "left": 1.27, "right": 1.27},
    {"key": "moderate", "name": "适中", "top": 2.0, "bottom": 2.0, "left": 2.0, "right": 2.0},
    {"key": "wide", "name": "宽", "top": 2.54, "bottom": 2.54, "left": 5.08, "right": 5.08},
)

#: 各级标题的段前/段后（磅）。与字号无关，所以不进 dataclass。
_HEAD_SPACING = ((12, 6), (10, 6), (8, 3), (6, 3))

MAX_HEAD_LEVEL = len(_HEAD_SPACING)

PAGE_W_CM = 21.0   # A4
PAGE_H_CM = 29.7


@dataclass(frozen=True)
class DocxStyle:
    """一份导出规格。默认值 = 「工程标书常见」，即第 1 轮用户拍板的那套。"""

    body_font: str = "宋体"
    body_size_pt: float = 12.0          # 小四
    line_spacing: float = 1.5
    margin_top_cm: float = 2.54
    margin_bottom_cm: float = 2.54
    margin_left_cm: float = 3.17
    margin_right_cm: float = 3.17
    header_text: str = ""               # 空 = 用项目名
    footer_format: str = "- {page} -"
    cover_title: str = "响应文件"

    # ---- 由正文字号推导的量（不开放给用户） ----

    @property
    def table_size_pt(self) -> float:
        """表内字号：比正文小一号半（正文小四 → 五号 10.5），但不小于小五。"""
        return max(round(self.body_size_pt - 1.5, 1), 8.0)

    @property
    def head_specs(self) -> tuple[tuple[float, str, bool, int, int], ...]:
        """各级标题 (字号, 中西文字体, 加粗, 段前pt, 段后pt)。

        标题比正文大 4/2/0 磅，即正文小四时正好是用户定的 16/14/12。
        四级与三级同字号，靠「换字体 + 加粗」区分；正文本就是黑体时改用宋体才分得开。
        """
        b = self.body_size_pt
        l4_font = "宋体" if self.body_font == HEI_FONT else self.body_font
        levels = ((b + 4, HEI_FONT, False), (b + 2, HEI_FONT, False),
                  (b, HEI_FONT, False), (b, l4_font, True))
        return tuple((s, f, bold, sp[0], sp[1])
                     for (s, f, bold), sp in zip(levels, _HEAD_SPACING))

    # ---- 序列化 ----

    def to_dict(self) -> dict:
        return asdict(self)


#: 内置的唯一一套规格。面板的「恢复默认」就是回到它。
DEFAULT = DocxStyle()


# ---- 防御式解析 ----

def _num(v, lo: float, hi: float, fallback: float) -> float:
    """转成 [lo, hi] 内的数字；转不动就回落。"""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return fallback
    if n != n:            # NaN
        return fallback
    return min(max(n, lo), hi)


def _text(v, limit: int, fallback: str = "") -> str:
    """单行文本：压掉换行与制表符。

    页眉/页脚会落成普通的 Word run（不在 instrText 里），换行会变成 <w:br/>、
    tab 会把居中顶歪；封面名过长会撑破版心。所以这里一律折成空格再截断。
    """
    if v is None:
        return fallback
    s = re.sub(r"[\r\n\t]+", " ", str(v)).strip()
    s = re.sub(r" {2,}", " ", s)
    return s[:limit]


def from_dict(raw, *, strict: bool = False) -> DocxStyle:
    """把库里存的 JSON（或已解析的 dict）还原成 DocxStyle。

    默认**不抛错**：落到非法值就回落到默认那一项。排版是「导出好看」这种锦上
    添花的事，不该因为一个手改坏的数字让整份标书导不出来。

    strict=True 时改为抛 ValueError（带人话说明），给 PUT 接口用它来当场告诉
    用户哪一项不合法，而不是悄悄改掉他填的值。
    """
    if raw is None or raw == "":
        return DEFAULT
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            if strict:
                raise ValueError("排版设置不是合法的 JSON")
            return DEFAULT
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("排版设置必须是一个对象")
        return DEFAULT

    def bad(msg: str, fallback):
        if strict:
            raise ValueError(msg)
        return fallback

    d = DEFAULT
    body_font = raw.get("body_font")
    if body_font is not None and body_font not in FONT_OPTIONS:
        body_font = bad(f"正文字体只能是 {'/'.join(FONT_OPTIONS)}", d.body_font)

    footer = raw.get("footer_format")
    if footer is None:
        footer = d.footer_format
    else:
        footer = _text(footer, 40)
        # 页脚没有 {page} 就等于把页码弄丢了；这不像别的项能静默回落。
        if "{page}" not in footer:
            footer = bad("页脚格式必须包含 {page}（页码占位符）", d.footer_format)

    return DocxStyle(
        body_font=body_font or d.body_font,
        body_size_pt=_num(raw.get("body_size_pt"), 8.0, 22.0, d.body_size_pt),
        line_spacing=_num(raw.get("line_spacing"), 1.0, 3.0, d.line_spacing),
        margin_top_cm=_num(raw.get("margin_top_cm"), 0.5, 6.0, d.margin_top_cm),
        margin_bottom_cm=_num(raw.get("margin_bottom_cm"), 0.5, 6.0, d.margin_bottom_cm),
        margin_left_cm=_num(raw.get("margin_left_cm"), 0.5, 6.0, d.margin_left_cm),
        margin_right_cm=_num(raw.get("margin_right_cm"), 0.5, 6.0, d.margin_right_cm),
        header_text=_text(raw.get("header_text"), 100),
        footer_format=footer,
        cover_title=_text(raw.get("cover_title"), 60) or d.cover_title,
    )


def options() -> dict:
    """供前端下拉的全部受控取值（前端不硬编码清单）。"""
    return {
        "fonts": list(FONT_OPTIONS),
        "body_sizes": [{"pt": pt, "name": name} for pt, name in BODY_SIZE_OPTIONS],
        "line_spacings": list(LINE_SPACING_OPTIONS),
        "margin_presets": [dict(m) for m in MARGIN_PRESETS],
        "default": DEFAULT.to_dict(),
    }


def get_project_style(project_id: int) -> DocxStyle:
    """读项目的排版设置；没存过（NULL）就是内置默认。"""
    from app.core.database import query_one   # 延迟 import：避免 core 内互相引用

    row = query_one("SELECT docx_style FROM project WHERE id = %s", (project_id,))
    if not row:
        return DEFAULT
    return from_dict(row.get("docx_style"))


def save_project_style(project_id: int, style: DocxStyle) -> None:
    """落库。存与默认完全一致的值时写 NULL，将来改默认规格能一并生效。"""
    from app.core.database import execute

    payload = None if style == DEFAULT else json.dumps(style.to_dict(), ensure_ascii=False)
    execute("UPDATE project SET docx_style = %s WHERE id = %s", (payload, project_id))


__all__ = [
    "DocxStyle", "DEFAULT", "from_dict", "options",
    "get_project_style", "save_project_style",
    "FONT_OPTIONS", "BODY_SIZE_OPTIONS", "LINE_SPACING_OPTIONS", "MARGIN_PRESETS",
    "MAX_HEAD_LEVEL", "HEI_FONT", "PAGE_W_CM", "PAGE_H_CM",
]

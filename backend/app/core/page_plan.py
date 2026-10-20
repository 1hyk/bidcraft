"""篇幅控制：把「招标要求正文不少于 N 页」翻译成每章该写多少字（工作台「页数」抽屉）。

**为什么不能直接把「页数」下发给模型**：页是 Word 分页的产物，服务端算不出来
（目录与页码是 Word 的域，见 docx_export.py 顶部说明）。生成阶段唯一能拧的旋钮是**字数**。
所以链路是：

    目标正文页数 × 当前排版的「字/页」系数  →  目标总字数  →  按章节权重分摊到章
    → 写进该章 prompt 的【篇幅要求】

**系数拆成两半**，因为只有一半能算：

    字/页 = 密度(排版) × 填充率(文档结构)

密度是纯几何量（页宽÷字宽 × 页高÷行高），排版一定就能算准；填充率是全文档里
表格、标题、段间距吃掉版面的比例，跟文档内容有关、算不出来，只能实测反推。
好在实测发现填充率**几乎不随排版变**——项目 4 同一份正文（本模块口径 86830 字），
默认档 151 页、改「三号 + 2 倍行距 + 窄边距」后 249 页（页数差 65%），
反推出的填充率却是 **0.454 与 0.467**，相差只有 2.9%。

也就是说密度的比值（1266 : 746 = 1.697）与页数的比值（249 : 151 = 1.649）
只差 2.9%——**排版那半由几何解析式决定，结构那半（填充率）测一次就能跨排版复用**，
这正是「第一次导出之前也能给出可用预算」的依据。

**字数的口径**（`count_words`）：中日韩字符逐个计 1，西文按单词计 1（Word 的「字数」语义）。
对项目 4：本口径正文 86830 + 各章标题 832 = 87662，而第 1 轮 Word
`ComputeStatistics(0)` 报 90283，差 −2.9%（Word 还数了目录域文本等这边没有的东西）。
**同尺度即可**——系数与各章字数都用本口径，两边一致，绝对值不必与 Word 完全吻合。

本模块只描述规则与算法，不 import python-docx。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from app.core.docx_style import PAGE_H_CM, PAGE_W_CM

# ---- 受控取值 ----

#: 目标正文页数的允许区间。招标要求常见几十到几百页，上限给足但不至于手滑填出天文数字。
TARGET_PAGES_RANGE = (1, 2000)

#: 「偏差多大算需要重写」的可选档（导出后回收真实页数时用，见第 2b 步）。
REWRITE_TOLERANCES = (0.10, 0.15, 0.20, 0.30)

#: 默认填充率。由项目 4 两个实测点反推（0.4541 与 0.4673，见模块说明），取均值到 0.46。
#: 用它会同时高/低估两档排版不到 2%（默认档 583 vs 实际 575，窄边距三号档 343 vs 349）。
#: 第 2b 步接入实测后，每个项目会用自己的实测值覆盖它。
DEFAULT_FILL_FACTOR = 0.46

#: 页眉页脚占掉的高度（cm）。这是估的：普通页边距下页眉/页脚各约 0.7cm。
#: 估偏一点只影响「还没实测过」时的预算，实测一次就被真实值取代。
HEADER_FOOTER_CM = 1.4

_CM_PER_PT = 2.54 / 72.0


# ---- 项目级配置 ----

@dataclass(frozen=True)
class PagePlan:
    """一个项目的篇幅计划。默认 = 不设目标（生成时不注入篇幅要求，维持原行为）。"""

    target_pages: int | None = None      # 目标**正文**页数（不含封面/目录）；None = 不限
    rewrite_tolerance: float = 0.15      # 偏差超过该比例视为需要重写（2b 用）

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT = PagePlan()


def _to_target_pages(v, *, strict: bool = False) -> int | None:
    """目标页数：空 → None（不限）；转不动或超范围 → strict 抛错、非 strict 记 None。"""
    if v is None or v == "":
        return None
    lo, hi = TARGET_PAGES_RANGE
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        if strict:
            raise ValueError("目标页数必须是数字") from None
        return None
    if not (lo <= n <= hi):
        if strict:
            raise ValueError(f"目标页数需在 {lo}–{hi} 之间")
        return None
    return n


def from_dict(raw, *, strict: bool = False) -> PagePlan:
    """把库里存的 JSON（或已解析的 dict）还原成 PagePlan。

    与 docx_style.from_dict 同一套双轨：默认**不抛错**（库里的值坏掉不该让整份标书
    生成不出来），strict=True 时抛 ValueError 给人话说明，供 PUT 接口当场报错。
    """
    if raw is None or raw == "":
        return DEFAULT
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            if strict:
                raise ValueError("页数设置不是合法的 JSON")
            return DEFAULT
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("页数设置必须是一个对象")
        return DEFAULT

    tol = raw.get("rewrite_tolerance")
    if tol is None:
        tol = DEFAULT.rewrite_tolerance
    else:
        try:
            tol = float(tol)
        except (TypeError, ValueError):
            tol = DEFAULT.rewrite_tolerance
        if tol not in REWRITE_TOLERANCES:
            if strict:
                raise ValueError("重写偏差阈值只能取 "
                                 + "/".join(f"{t:.0%}" for t in REWRITE_TOLERANCES))
            tol = DEFAULT.rewrite_tolerance

    return PagePlan(target_pages=_to_target_pages(raw.get("target_pages"), strict=strict),
                    rewrite_tolerance=tol)


def options() -> dict:
    """供前端下拉的受控取值（前端不硬编码清单，与 docx_style.options 同一约定）。"""
    return {
        "default": DEFAULT.to_dict(),
        "target_pages_range": list(TARGET_PAGES_RANGE),
        "rewrite_tolerances": [
            {"value": t, "name": f"±{t:.0%}"} for t in REWRITE_TOLERANCES
        ],
    }


def get_project_plan(project_id: int) -> PagePlan:
    """读项目的篇幅计划；没存过（NULL）就是不设目标。"""
    from app.core.database import query_one   # 延迟 import：避免 core 内互相引用

    row = query_one("SELECT page_plan FROM project WHERE id = %s", (project_id,))
    if not row:
        return DEFAULT
    return from_dict(row.get("page_plan"))


def save_project_plan(project_id: int, plan: PagePlan) -> None:
    """落库。存与默认完全一致的值时写 NULL，将来改默认能一并生效（同 docx_style）。"""
    from app.core.database import execute, query_one

    row = query_one('SELECT page_plan FROM project WHERE id=%s',(project_id,)) or {}
    previous = row.get('page_plan') or {}
    if isinstance(previous,str):
        try: previous=json.loads(previous)
        except (ValueError,TypeError): previous={}
    data=plan.to_dict()
    if isinstance(previous,dict) and previous.get('measurement'):
        data['measurement']=previous['measurement']
    payload = None if plan == DEFAULT and 'measurement' not in data else json.dumps(data, ensure_ascii=False)
    execute("UPDATE project SET page_plan = %s WHERE id = %s", (payload, project_id))


# ---- 章节权重 ----

#: 层级权重：一级章一条通常比四级条目重（索引 = 层级-1，超出取下限）。
LEVEL_WEIGHT = (3.0, 2.0, 1.2, 1.0)

#: 有子章节的节点多半只是引言式的壳（真正内容在子节里），按此折扣，免得重复分配给壳。
CONTAINER_FACTOR = 0.4

#: 章节类型权重：按标题关键词，命中即乘。顺序即优先级，先命中先算。
#: 这三组是**可调起点不是真理**——配得不合适可以在页数面板里逐章改额度。
#:
#: 组1 原先只有铁路施组那套词（施工方案/施工工艺/专项…）。拿低空测试标实测：
#: 56 个标题里组1、组2 **一个都没命中**，只有组3 的「响应/编制依据/承诺」命中 14 个
#: ——也就是说这张表唯一的实际效果是给那 14 章降权，从来没给任何章加过权。
#: 「技术方案」四个字只出现在一级章名上，它下面 7 个子节叫「…技术路线」「…验证方案」，
#: 一个都命不中，于是技术核心章和营业执照复印件拿到了几乎一样的额度。
#: 所以这里补的是**跨领域通用**的技术核心词，不是给某个领域开小灶。
TYPE_WEIGHT = (
    (("施工方案", "施工方法", "施工工艺", "工艺流程", "施工技术",
      "技术方案", "实施方案", "验证方案", "解决方案", "总体方案",
      "技术路线", "技术架构", "施工组织设计",
      "专项", "重难点", "关键技术", "技术措施"), 1.6),
    (("质量", "安全", "进度", "环保", "文明施工", "应急", "保证措施", "管理体系"), 1.3),
    (("编制依据", "编制说明", "编制原则", "工程概况", "项目概况",
      "承诺", "响应", "概况", "简介", "目标"), 0.6),
)

#: 子节继承父章类型权重时的折扣。见 type_factor：标题没说自己是哪类，就随父章（打点折）。
INHERIT_FACTOR = 0.9


def own_type_factor(title: str) -> float | None:
    """标题自己命中的类型倍率；一个词都没命中返回 None（= 没有自己的主张）。"""
    t = title or ""
    for words, factor in TYPE_WEIGHT:
        if any(k in t for k in words):
            return factor
    return None


def type_chain(title: str, ancestor: float | None = None) -> float | None:
    """沿祖先链解析出的类型倍率；**整条链一个都没命中时返回 None**。

    注意这个 None 不是可有可无的：它和「倍率等于 1.0」必须分开。否则中性父章
    （倍率 1.0）会把 1.0 当成「命中过」传给子节，子节被凭空打 0.9 折——
    「纳税及社会保障资金缴纳证明」就这么从 2.00 掉成了 1.80。

    `ancestor` = 父章**解析后**的链值（不是父章的原始命中值，这样多级能逐层衰减）。
    """
    own = own_type_factor(title)
    if own is not None:
        return own
    return ancestor * INHERIT_FACTOR if ancestor is not None else None


def type_factor(title: str, ancestor: float | None = None) -> float:
    """类型倍率（数值形式，未命中即中性 1.0）：自己的标题说了算；没说就**继承父章**。

    只看节点自己的标题是不够的——一本标书的技术核心往往只写在**一级章名**上
    （「四、技术方案」），子节标题是「总体技术路线」「局点实飞演示场景支撑方案」
    这类具体说法，未必命得中关键词。没有继承，技术核心章的子节就会被当中性内容，
    和「纳税及社会保障资金缴纳证明」拿一样的额度。
    """
    chain = type_chain(title, ancestor)
    return 1.0 if chain is None else chain


def chapter_weight(title: str, level: int, has_children: bool,
                   ancestor: float | None = None) -> float:
    """一章的相对权重：层级 × 章节类型（含父章继承） × 容器折扣。

    ancestor 一般由 `_ordered_nodes` 从目录树上带下来；手工构造节点时省略即为「无父章」。
    """
    idx = min(max(level, 1), len(LEVEL_WEIGHT)) - 1
    w = LEVEL_WEIGHT[idx] * type_factor(title, ancestor)
    if has_children:
        w *= CONTAINER_FACTOR
    return w


def allocate(nodes: list[dict], total_chars: int | None) -> dict[int, int]:
    """把目标总字数按权重分摊到各章，返回 {node_id: 目标字数}。

    nodes 每项需含 `id` / `title` / `level` / `has_children`，可选 `override`（人工填的额度）
    与 `ancestor_type_factor`（父章已解析的类型倍率，子节据此继承父章的重要度）。
    有人工额度的章先按额度扣减，余下的再按权重摊给其余的章——
    这样「我就要这章写 3000 字」不会被归一化冲掉。

    total_chars 为空（没设目标页数）时返回空字典，调用方据此不注入篇幅要求。
    """
    if not total_chars or total_chars <= 0 or not nodes:
        return {}

    out: dict[int, int] = {}
    fixed = 0
    rest: list[tuple[int, float]] = []
    intros = sum(bool(n.get("intro_words")) and not n.get("override") for n in nodes)
    for n in nodes:
        ov = n.get("override")
        if not ov and n.get("intro_words"):
            ov = min(n["intro_words"], max(1, int(total_chars * .1 / max(intros, 1))))
        if ov:
            out[n["id"]] = int(ov)
            fixed += int(ov)
            continue
        rest.append((n["id"], chapter_weight(n.get("title", ""), n.get("level", 1),
                                              bool(n.get("has_children")),
                                              n.get("ancestor_type_factor"))))

    remaining = total_chars - fixed
    if not rest:
        return out
    if remaining <= 0:
        # 人工额度已经把总预算吃光（或超了）：其余章不给目标，也不去负向压缩——宁可不注入。
        return out

    total_w = sum(w for _, w in rest) or 1.0
    for nid, w in rest:
        out[nid] = max(int(remaining * w / total_w), 1)
    return out


# ---- 「字/页」系数 ----

def page_density(style) -> float:
    """纯排版决定的满版密度（字/页）：页宽÷字宽 × 页高÷行高。

    中文一个字占一个 em 宽，所以每行字数 = 版心宽 ÷ 字号。行高 = 字号 × 行距倍数。
    这是**理论满版值**，真实页面还要乘填充率。
    """
    pt_cm = max(style.body_size_pt, 1.0) * _CM_PER_PT
    w_cm = PAGE_W_CM - style.margin_left_cm - style.margin_right_cm
    h_cm = PAGE_H_CM - style.margin_top_cm - style.margin_bottom_cm - HEADER_FOOTER_CM
    chars_per_line = max(w_cm, 1.0) / pt_cm
    lines_per_page = max(h_cm, 1.0) / (pt_cm * max(style.line_spacing, 0.5))
    return chars_per_line * lines_per_page


def estimate_chars_per_page(style, fill_factor: float = DEFAULT_FILL_FACTOR) -> float:
    """估算当前排版下的「字/页」。fill_factor 由实测给出时，估算即逼近实测。"""
    return page_density(style) * fill_factor


# ---- 字数口径 ----

_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_CJK_PUNCT_RE = re.compile(r"[　-〿！-～‘’“”—…]")
_LATIN_RE = re.compile(r"[A-Za-z0-9]+(?:[.'\-][A-Za-z0-9]+)*")
_TAG_RE = re.compile(r"<[^>]+>")
_FENCE_RE = re.compile(r"```.*?```", re.S)
_MERMAID_CODE_RE = re.compile(r'data-code="[^"]*"')
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _strip_markup(text: str) -> str:
    """剥掉 HTML 标签、代码块、mermaid 的 data-code、图片引用。

    正文是 md / html 混存（见 docx_export._normalize_body），这里不引 markdown 库，
    用正则同时吃掉两种格式的标记——代价是少数边缘写法会残留，但字数只求同尺度。
    """
    s = _FENCE_RE.sub(" ", text or "")
    s = _MERMAID_CODE_RE.sub(" ", s)
    s = _IMG_RE.sub(" ", s)
    s = _TAG_RE.sub("", s)
    return s


def count_words(text: str) -> int:
    """正文字数：中日韩字符逐个计 1、西文按单词计 1（对齐 Word 的「字数」语义）。"""
    s = _strip_markup(text)
    return (len(_CJK_RE.findall(s))
            + len(_CJK_PUNCT_RE.findall(s))
            + len(_LATIN_RE.findall(s)))


def content_words(content) -> int:
    """section_content.content（JSON 或已解析 dict）里的正文字数。"""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            return 0
    if not isinstance(content, dict):
        return 0
    return count_words(content.get("body") or "")


__all__ = [
    "PagePlan", "DEFAULT", "from_dict", "options",
    "get_project_plan", "save_project_plan",
    "chapter_weight", "type_factor", "type_chain", "own_type_factor", "allocate",
    "LEVEL_WEIGHT", "CONTAINER_FACTOR", "TYPE_WEIGHT", "INHERIT_FACTOR",
    "page_density", "estimate_chars_per_page", "DEFAULT_FILL_FACTOR",
    "count_words", "content_words",
    "TARGET_PAGES_RANGE", "REWRITE_TOLERANCES",
]

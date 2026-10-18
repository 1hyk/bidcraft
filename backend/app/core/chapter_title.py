"""章节标题的规范化与匹配（招标要求 → 施工章节的定位依据）。

同一章在不同地方写法不同：目录树里是「四、技术方案」，招标文件规定的目录里可能是
「第一章 技术方案」，模型填 suggested_chapter 时又可能写成「技术方案」。要让
「这条招标要求该注入哪一章」判得准，得先去掉序号前缀再比。

**为什么值得单独一个模块**：抽取侧（`tender_agent` 校验模型填的章名是否真在目录里）
和消费侧（`requirement_service` 按章取要求）必须用**同一套**归一化规则——两边不一致
就会出现「抽取时说对上了、消费时又对不上」。纯函数，不 import 任何项目模块。
"""
from __future__ import annotations

import re

#: 章节标题的序号前缀。中文章节号、括注式、阿拉伯数字与多级编号都要吃掉：
#: 「四、」「第一章」「第一节」「（一）」「1.」「1.1」「3.2.1」
_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"第[一二三四五六七八九十百零〇\d]+[章节部分篇]"
    r"|[（(][一二三四五六七八九十百零〇\d]+[)）]"
    r"|[一二三四五六七八九十百零〇]+[、.．]"
    r"|\d+(?:[.．]\d+)*[、.．]?"
    r")\s*"
)

#: 归一化后短于这个长度就不做包含匹配了——「技术」⊂「技术方案」这种太容易误伤
_MIN_CONTAIN = 3

#: 标题里的引号（「第四节 “九天智感”实景验证实施方案」）。不剥掉的话，要求标题
#: 「九天智感实景验证」永远包含不进这个章名里——明明是同一条，却因为一对引号落空。
_QUOTE_RE = re.compile(r"[“”‘’\"'「」『』]")


def normalize(title) -> str:
    """去掉序号前缀、空白与引号，得到可比较的章名主体。

    前缀可能叠着（「第一章 第一节 概述」），所以剥到不再变化为止（最多 3 轮，防病态输入）。
    """
    s = str(title or "").strip()
    for _ in range(3):
        stripped = _PREFIX_RE.sub("", s)
        if stripped == s:
            break
        s = stripped
    return _QUOTE_RE.sub("", re.sub(r"\s+", "", s))


def related(a, b) -> bool:
    """两个章节名是不是指同一章：归一化后**相等**，或其中一个**包含**另一个。

    包含匹配是这里的主要价值——招标要求常写得更概括（「营业执照」对应目录里的
    「第一节 企业营业执照及基本信息」），只判相等会全都落空。
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= _MIN_CONTAIN and shorter in longer


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)}


#: 二元组少于这个数就不判相似——短标题会把分数撑虚。实测：「技术方案」只有 3 个二元组，
#: 只要对方也以「方案」结尾，重合 1 个就报 0.333，把「第七节 局点实飞演示场景支撑方案」、
#: 「第二节 相关资质证书（如ISO认证）」这类毫不相干的章全拉进来（0.333 一簇 11 对）。
#: 掐掉短标题后，那一簇整体消失。
_MIN_BIGRAMS = 4

#: 相似度阈值。实测标定（项目 4，56 章节 × 规定子项的全量扫描）：
#:   ✅ 项目售后服务与保障能力 ↔ 售后服务体系与保障能力介绍 → 0.700
#:   ✅ 项目实施方案           ↔ “九天智感”实景验证实施方案  → 0.600
#:   ✗ 测试验收准备方案       ↔ 验收交付物清单符合性对照     → 0.143
#:   ✗ 项目实施方案           ↔ 起降场智能调度能力验证方案    → 0.200
#: 好配对 ≥0.600、噪音 ≤0.333（掐掉短标题后），取 0.55 落在空档里。
#: **这是措辞变体的兜底，不是语义理解**：换词它就认不出来——「售后服务承诺书及分级响应
#: 机制说明」与「项目售后服务与保障能力」只到 0.300、够不着阈值，虽然语义上该配。
#: 那种要靠人补章节名或别名表，别指望调阈值能救（0.300 上面正好压着 0.333 那一簇噪音）。
SIM_THRESHOLD = 0.55


def similarity(a, b) -> float:
    """字符二元组重合度（重叠系数 = 交集 ÷ 较短方），对中文标题的**改述**比包含匹配宽容。

    为什么需要它：包含匹配要求一方是另一方的子串，而「项目售后服务与保障能力」和
    「第四节 售后服务体系与保障能力介绍」彼此都不包含对方——「售后服务体系」多了个
    「体系」，于是双向包含都不成立、定位落空。二元组不必连续，能扛住这种中间插入/替换。
    """
    ba, bb = _bigrams(normalize(a)), _bigrams(normalize(b))
    if len(ba) < _MIN_BIGRAMS or len(bb) < _MIN_BIGRAMS:
        return 0.0
    return len(ba & bb) / min(len(ba), len(bb))


def toc_titles(toc: dict | None) -> list[str]:
    """招标规定目录里的全部章名（一级章 + 子节），按原文顺序。"""
    out: list[str] = []
    for ch in (toc or {}).get("chapters") or []:
        title = str(ch.get("title") or "").strip()
        if title:
            out.append(title)
        for kid in ch.get("children") or []:
            kt = str(kid.get("title") or "").strip()
            if kt:
                out.append(kt)
    return out


def align(title, candidates) -> str:
    """把一个章名对齐到候选清单里的**原样写法**；对不上返回空串。

    返回原样写法而不是归一化结果，是为了让消费侧能直接做相等比较（且界面上显示的是
    界面上的那种写法）。包含匹配优先取最接近的那个（归一化长度差最小）。
    """
    target = normalize(title)
    if not target:
        return ""
    best, best_gap = "", None
    for cand in candidates or []:
        nc = normalize(cand)
        if not nc:
            continue
        if nc == target:
            return str(cand).strip()
        shorter = min(len(nc), len(target))
        if shorter >= _MIN_CONTAIN and (nc in target or target in nc):
            gap = abs(len(nc) - len(target))
            if best_gap is None or gap < best_gap:
                best, best_gap = str(cand).strip(), gap
    return best


#: 下沉时认「措辞变体」的阈值，比消费侧 SIM_THRESHOLD(0.55) 高一档。
#: 为什么高一档：消费侧那 0.55 是「这一章再试一次」的兜底，判错只是多给一条；
#: 下沉是**改这条要求的归属**，判错就把它钉死在别的子节上，代价大得多。
#: 实测（项目 4）：真命中的分数是 0.889 / 0.875 / 0.700，而泛称误配
#: 「项目实施方案」↔「“九天智感”实景验证实施方案」正好 0.600——0.65 卡在中间，
#: 把泛称那批压下去，同时留住 0.700 那条。
REPIN_SIM_THRESHOLD = 0.65


def children(toc: dict | None, parent) -> list[str]:
    """规定目录里 `parent` 这一章的**子节**章名；找不到这一章、或它是叶子就返回空。

    `parent` 传的是 `align()` 回来的**原样章名**，所以这里按字符串（归一化后）**相等**比，
    不能用 `related()`：包含匹配会把子节也当父章——「技术方案」正是
    「监视能力全量验证技术方案」的子串，一用 related 就串了。
    """
    p = str(parent or "").strip()
    if not p:
        return []
    pn = normalize(p)
    for ch in (toc or {}).get("chapters") or []:
        t = str(ch.get("title") or "").strip()
        if t == p or normalize(t) == pn:
            return [str(k.get("title") or "").strip()
                    for k in ch.get("children") or []
                    if str(k.get("title") or "").strip()]
    return []


def descend(title, assigned, toc: dict | None) -> str:
    """把挂在**父章**上的要求下沉到真正对应的子节；没有对应子节就原样返回。

    实测（项目 4，2026-10-15）：模型抽技术要求时一律填顶层章名——
    「监视能力全量验证」挂到「四、技术方案」，而目录里明明有
    「第三节 监视能力全量验证技术方案」，只差「第三节」和「方案」两处前后缀。
    父章一口气吃下 8 条，底下 7 个子节各写各的正文时一条要求都拿不到。

    判据是**要求自己的标题**：它和哪个子节最像就归哪个子节——包含优先（最可信），
    措辞变体次之（阈值见 REPIN_SIM_THRESHOLD）。只在 `assigned` 这一章的子树里找，
    不跨章抢：模型挑的那棵子树本身通常是对的，只是粒度粗了一层。
    """
    kids = children(toc, assigned)
    if not kids:
        return assigned
    for k in kids:
        if related(title, k):
            return k
    best, best_s = "", 0.0
    for k in kids:
        s = similarity(title, k)
        if s > best_s:
            best, best_s = k, s
    return best if best_s >= REPIN_SIM_THRESHOLD else assigned


__all__ = ["normalize", "related", "similarity", "toc_titles", "align",
           "children", "descend", "SIM_THRESHOLD", "REPIN_SIM_THRESHOLD"]

"""同一张图全书只由一章配：为「建议图片」算一份**确定性**的归属表。

背景（2026-10-16，用户原话「确实不能出现两张一样的图」）：知识页是按章**独立**匹配的
（`matching.match_pages`），而容器章与它的子章标题/路径天然相似，于是常常匹配到同一批页。
项目 4 里 `#285 工程化交付方案` 与它的子章 `#286 第一节 交付批次规划` 的 `_page_ids`
**一字不差**，两章的【建议使用的图片】里于是出现同一个图名
（「跨区异地并行作业流水线横道图」），模型各画一张，成稿里就是两张一样的图。
33 章里被点名的图名 24 个，其中 6 个是两章共用的。

**为什么不能在生成时"领取"图名**：正文生成的两条批量路径都是
`ThreadPoolExecutor(max_workers=BATCH_CONCURRENCY)` 并发（`writer_service.py` 的
`start_batch` / `rewrite_chapters`），`_chapter_context` 内部还再并发取材一次。
按运行时抢图必然是不确定的竞态——同一份目录重跑两次可能换个章拿到图，
而"哪一章更需要这张图"是有正确解的。所以归属**在开写之前按目录结构一次算定**，
与生成顺序、并发度都无关。

归属规则（用户 2026-10-16 拍板）：
  1. **层级最深者得**——容器章因此天然让位给它的子章（子章才是展开这项内容的那一章，
     容器章按既有口径"只当引言写"，见 `page_plan_service` 的容器折扣）；
  2. 同层级按**目录序**（先序：先比 `sort_order`，再比 `id`）最靠前者得；
  3. 只有一章点名的图不受影响，照旧注入——**不做多余的裁决**。

`level` 是现算的：`chapter_node` 没有 level 列（见 `scripts/init_db.sql`），层级只能沿
`parent_id` 上溯，与 `page_plan_service` 的口径一致。
"""
from __future__ import annotations

import re
import hashlib
import unicodedata
from pathlib import Path

from app.core.database import query

# 文件名不是图名。2026-10-16 实测：全库 207 条 image 条目里 **103 条**图名是
# `docx_0005.png` 这类文件名——源头是 `docx_parser.export_para_images` 导出内联图时
# 写的是 `![图](docx_0009.png)`（alt 恒为「图」，文件名是唯一标识），而 `knowledge_agent`
# 的提示词只解释了 content/usage、**没说 caption 该从哪来**，模型没有图题可抄时就照抄了
# 这两样。图名一路流进正文提示词的【建议图片】，模型照着写出 `[图片占位：docx_0005.png]`。
_FILE_SUFFIX_RE = re.compile(r"\.(png|jpe?g|gif|bmp|webp|tiff)\b", re.I)
# 解析器写死的 alt（「图」）被原样回显时也不是图名：`- 图「图」` 没有任何信息。
_BLANK_NAMES = {"图", "图片", "图示", "figure", "image", "img"}


# `images[].kind` 里**不该进【建议图片】的类别**（视觉给的分类，见 image_vision KINDS）。
# 只挡 scan：合同/证书/检测报告扫描件是**附件**不是配图素材，留在建议里写手只会照着写
# "见附件扫描件"这种占位（实测全库 20 条）。
# **screenshot 不挡**——软件界面截图在技术方案里是正经配图（28 条）；
# logo/other 也不挡（other 只是视觉没归好类的杂项，误伤代价大于收益）。
NOT_SUGGESTIBLE_KINDS = frozenset({"scan"})


# ---------------------------------------------------------------- 图名归一化
# **相等判定的预处理，不是相似度打分**：归一化后仍要完全相等才认。
# 只消"改写不消信息"的噪音——图号前缀（`图1：`）、括号里的序号（`（一）`）、
# 空白与标点、全角半角。2026-10-16 实测的病根就在这儿：视觉回填给图名加了「（一）」
# 之后，正文里早先写下的「无人机实飞测试现场」当场失配（`_lookup_name` 只有整串相等
# 和按分隔符切前缀两级，两级都够不着）。
#
# ⚠ **不引模糊打分**（`matching._dice` 那种 bigram 相似度）：`端到端测试验证流程图`
# 与 `研发测试验证流程图` 的相似度不低，但它们是不同节的两张图——插错图比留红字坏
# （同 `writer_agent._IMG_SOURCE_RULE` 的"判不准就按占位"）。
_LEAD_NOISE_RE = re.compile(r"^(图|表|附图|图表|如下图)\s*[0-9０-９一二三四五六七八九十]*\s*[-－—–_.、：:]*\s*")
_BRACKET_RE = re.compile(r"[（(【\[]\s*[0-9一二三四五六七八九十]+\s*[）)】\]]")
_NORM_DROP_RE = re.compile(r"[\s　\-－—–_/／、，,。.：:；;·…\"'“”‘’]")


def normalize_image_name(s: str) -> str:
    """图名的归一化形式，只给 `ProjectImages._match` 做相等判定用。

    顺序有讲究：**先去图号前缀，再去括号**——`图1：虚实结合三级递进…示意图` 的
    `图1：` 在括号处理之前剥掉才剥得干净。
    """
    s = unicodedata.normalize("NFKC", (s or "").strip())
    if not s:
        return ""
    s = _LEAD_NOISE_RE.sub("", s)
    s = _BRACKET_RE.sub("", s)
    return _NORM_DROP_RE.sub("", s).lower()


def is_real_name(s: str) -> bool:
    """这个字符串能不能当图名用：文件名、解析器的占位 alt、空串都不算。"""
    s = (s or "").strip()
    if not s:
        return False
    if _FILE_SUFFIX_RE.search(s):          # docx_0009.png、图 (docx_0009.png)
        return False
    return s.lower() not in _BLANK_NAMES


def image_name(im) -> str:
    """知识页里一个 image 元素的图名：`caption` 优先，回退 `title`（老数据没 caption）；
    **名字不成立时返回空串**（见 `is_real_name`），调用方按"这张图没有名字"处理。

    ⚠ **算归属和渲染提示词必须走同一个函数**：这里算归属表，`writer_agent._build_context`
    渲染【建议图片】。口径分了家就会出现"归属表里叫 A、提示词里写 B"，
    结果就是那张图照样在两章里各出现一次——挡不住的假绿。

    ⚠ 名字归一**放在这里而不是抽取时**，是为了连**已经落库的那 103 条脏图名**一起管住
    ——它们不会再被清洗一遍（要清洗得重解析整册），但从此进不了【建议图片】。
    `_build_context` 见空名即跳过，前端显示"未命名"，两头都不会把文件名当图名用。
    """
    if not isinstance(im, dict):
        return ""
    name = str(im.get("caption") or im.get("title") or "").strip()
    return name if is_real_name(name) else ""


def resolve_image_skips(nodes: list[dict]) -> dict[int, dict[str, str]]:
    """{节点 id: {该章要让出的图名: 归属章的标题}}——**纯函数，不碰库**。

    nodes：`[{"id": int, "title": str, "parent_id": int|None, "sort_order": int,
              "captions": [str, ...]}]`，`captions` 是该章被分配到的知识页里的图名。

    让出的图名**只对"被多章点名"的图**产生；单章点名的图不在结果里（调用方 `.get(id)` 拿到的
    是空 dict，等于不干预）。
    """
    by_id = {int(n["id"]): n for n in nodes}

    def level(n: dict) -> int:
        lv, cur, guard = 1, n, 0
        while cur.get("parent_id") and int(cur["parent_id"]) in by_id and guard < 50:
            lv += 1
            guard += 1          # parent_id 万一成环也不能挂死（脏数据不该让生成起不来）
            cur = by_id[int(cur["parent_id"])]
        return lv

    # 先序序位：父在子前，同父按 sort_order。只用来给"同层级"做稳定裁决。
    kids: dict[int | None, list[dict]] = {}
    for n in nodes:
        p = n.get("parent_id")
        p = int(p) if p is not None and str(p).isdigit() else None
        kids.setdefault(p if p in by_id else None, []).append(n)   # 父不在本批 → 当根，别把节点丢了
    order: dict[int, int] = {}

    def walk(pid: int | None) -> None:
        for n in sorted(kids.get(pid) or [],
                        key=lambda x: (x.get("sort_order") or 0, int(x["id"]))):
            order[int(n["id"])] = len(order)
            walk(int(n["id"]))

    walk(None)

    # 图名 → 点名它的章（去重，同一章同一图名只算一次）
    owners: dict[str, list[dict]] = {}
    for n in nodes:
        nid = int(n["id"])
        for cap in dict.fromkeys(n.get("captions") or []):   # 去重且保序
            if cap:
                owners.setdefault(cap, []).append({"id": nid, "title": n.get("title") or "",
                                                   "lv": level(n), "ord": order.get(nid, 10**6)})

    skips: dict[int, dict[str, str]] = {}
    for cap, who in owners.items():
        if len(who) < 2:            # 只有一章点名：不干预
            continue
        win = min(who, key=lambda w: (-w["lv"], w["ord"]))   # 层级最深；同层取目录序最前
        for w in who:
            if w["id"] != win["id"]:
                skips.setdefault(w["id"], {})[cap] = win["title"]
    return skips


def _nodes_and_pages(project_id: int) -> tuple[list[dict], dict[int, dict]]:
    """项目的章节点 + 它们 `_page_ids` 命中的知识页。`(nodes, {page_id: page})`。

    生成侧（`project_image_skips`）与导出侧（`project_image_index`）**共用这一份读法**，
    两边的图名视图才不会分家。
    """
    rows = query(
        "SELECT id, parent_id, sort_order, title, outline FROM chapter_node"
        " WHERE project_id = %s ORDER BY sort_order, id",
        (project_id,),
    )
    from app.services.section_service import generation_node_ids

    allowed = generation_node_ids(project_id)
    rows = [r for r in rows if r["id"] in allowed]
    if not rows:
        return [], {}

    import json

    from app.services.matching import load_pages_by_ids

    nodes: list[dict] = []
    all_ids: set[int] = set()
    for r in rows:
        o = r.get("outline")
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except (TypeError, ValueError):
                o = {}
        pids = [int(p) for p in ((o or {}).get("_page_ids") or []) if str(p).isdigit()]
        all_ids.update(pids)
        nodes.append({"id": int(r["id"]), "title": r.get("title") or "",
                      "parent_id": r.get("parent_id"), "sort_order": r.get("sort_order") or 0,
                      "_page_ids": pids, "image_needs": (o or {}).get("image_needs") or []})
    if not all_ids:
        return nodes, {}

    # 复用 `load_pages_by_ids` 按 id 取回原文。**这不构成新的取料口**：`_page_ids` 是目录阶段
    # 经两道墙（domain 硬过滤 + scope 归属，见 `matching._scope_sql`）选出来的，这里只按 id 读回，
    # 没有重新匹配，所以不需要再传 domain/project_id（AGENTS.md 那条约束针对的是**重新选料**）。
    pages = {int(p["id"]): p for p in load_pages_by_ids(sorted(all_ids))}
    return nodes, pages


def _with_captions(nodes: list[dict], pages: dict[int, dict]) -> None:
    """就地把每章的图名列表挂到 `n["captions"]`（同一章同一图名只算一次，保序）。"""
    for n in nodes:
        caps: list[str] = []
        for pid in n["_page_ids"]:
            for im in (pages.get(pid, {}).get("images") or []):
                nm = image_name(im)
                if nm and im.get("url") and im.get("kind") not in NOT_SUGGESTIBLE_KINDS:
                    caps.append(nm)
        from app.services.generated_images import eligible

        caps.extend(str(need["name"]).strip() for need in n.get("image_needs") or [] if eligible(need))
        n["captions"] = caps


def project_image_skips(project_id: int) -> dict[int, dict[str, str]]:
    """按项目算一次归属表：{节点 id: {让出的图名: 归属章标题}}。

    **生成前调一次、结果传给每章**，别在 `_chapter_context` 里每章重算一遍：批量/重写路径
    是并发 10 章，每章都重算等于把同一份目录解析 10 遍（且白读几百行知识页）。
    """
    nodes, pages = _nodes_and_pages(project_id)
    if not nodes:
        return {}
    _with_captions(nodes, pages)
    return resolve_image_skips(nodes)


def project_image_index(project_id: int) -> dict[int, dict[str, "Path"]]:
    """导出插真图用：`{节点 id: {图名: 本地文件路径}}`。

    **与写手同源同序**：同一份 `_page_ids`、同一把 `image_name` 尺、同样扣掉
    `project_image_skips` 判给别章的图名——所以这里能查到的，就是那一章当初在
    【建议图片】里看到的图（`writer_service.py:157,179`）。不一致就会出现"写手没见过的图
    凭空插进正文"。

    进不了索引的一律让占位保持红字，**不猜、不兜底**：
      - 没有 `url` 的条目 = 模型从描述性文字里编出来的**空壳**（实测全库 27 条），素材里没这张图；
      - `url` 定位不到文件（文件不在盘上）；
      - 已让给别章的图名。

    **平局规则**：同名取"先出现的"——先按 `_page_ids` 顺序，再按页内 `images[]` 顺序；
    若先出现的那条取不到文件，则让给后面能取到文件的同名条目。实测 5 个图名对应多个文件、
    4 张页内有重名，必须有确定解（`resolve_image_skips` 挑归属时用的也是"目录序最前"，口径一致）。
    """
    from app.services.image_vision import local_path_of   # 延迟 import：image_vision 反过来要用 image_name

    nodes, pages = _nodes_and_pages(project_id)
    if not nodes or not pages:
        return {}
    _with_captions(nodes, pages)
    skips = resolve_image_skips(nodes)

    index: dict[int, dict[str, Path]] = {}
    for n in nodes:
        nid = int(n["id"])
        own: dict[str, Path] = {}
        yielded = skips.get(nid) or {}
        for pid in n["_page_ids"]:
            for im in (pages.get(pid, {}).get("images") or []):
                nm = image_name(im)
                if (not nm or nm in yielded or nm in own
                        or im.get("kind") in NOT_SUGGESTIBLE_KINDS):
                    continue
                p = local_path_of(str(im.get("url") or ""))
                if p is not None:
                    own[nm] = p
        if own:
            index[nid] = own
    return index


class ProjectImages(dict):
    """导出插真图用的**全书**索引：`{图名: 本地文件}`，外加两级能力。

    ❶ **归一化回退**（`_match` 的第三级）：占位里的图名与索引里的图名只差图号前缀、
    括号序号、空白标点时也认得出来。仍是**相等判定**，不引相似度（见 `normalize_image_name`）。
    ❷ **全书去重**（`take`）：同一个图名整本书**只插一次**——先写到的章得，后面的留红字。

    ⚠ 为什么是全书而不是"本章"（2026-10-16 改）：本章索引来自 `project_image_index` 的
    **归属裁决**（同一图名全书只由一章配，层级最深者得）。裁决本身是对的——它防的是
    "同一个图名在两章各配一张、成稿里出现两张一样的图"。但它的代价是**归属章没写这张图时，
    整本就都没有了**：项目 4 里那两张实飞现场照片判给了 `#288 第三节 开发测试验证流程`，
    而真正写占位的是 `#287 第二节 阶段性交付物清单`——287 的索引里压根没有这张图，
    连查的机会都没有。改成"查全书、插一次"，既救回这张图，又仍然不会出现两张。
    ⚠ 代价要认：略微放松了"写手只见到本章匹配到的图"这条同源保证——写手写的图名可能
    命中别的章匹配到的图。可接受，因为**图名仍是写手从上下文里见过的**
    （【建议图片】或【历史原文参考】），且插在哪一章由文档顺序唯一确定。

    **平局取先出现的**：归一化后撞到一起的多个图名（`无人机实飞测试现场（一）`/`（二）`、
    `架构图（顶层）`/`（应用层）`）取索引里先建进去的那个——与 `project_image_index` 内部
    处理同名重图的规则同款，确定解，不随调用顺序变。
    """

    def __init__(self, names=None, assets=None):
        super().__init__(names or {})
        self.assets = assets or {}
        norm: dict[str, list[str]] = {}
        for nm in self:
            key = normalize_image_name(nm)
            if key:
                norm.setdefault(key, []).append(nm)
        self._norm = norm
        self._used: set[str] = set()
        self._used_files: set[str] = set()
        self._file_keys: dict[Path, str] = {}

    def __bool__(self):
        return len(self) > 0 or bool(self.assets)

    def _file_key(self, path: Path) -> str:
        """按像素识别同图；不同文件名、不同编码也不能在全书重复插入。"""
        path = Path(path)
        if path not in self._file_keys:
            try:
                from PIL import Image, ImageOps

                with Image.open(path) as image:
                    rgb = ImageOps.exif_transpose(image).convert("RGBA")
                    payload = str(rgb.size).encode() + rgb.tobytes()
                key = hashlib.sha256(payload).hexdigest()
            except Exception:
                key = str(path.resolve()).casefold()
            self._file_keys[path] = key
        return self._file_keys[path]

    def fork(self) -> "ProjectImages":
        """同内容、**全新的去重状态**。给 `_collect_pending` 用：文末清单和正文行走的是
        同一棵树的同一顺序，各自从零开始消费才能对同一处给出同样的判定
        （清单跑在正文之前，共用 `_used` 会把整篇都算成"已插过"）。"""
        return ProjectImages(self, self.assets)

    def _match(self, raw: str) -> tuple[str, Path | None]:
        """三级：整串精确 → 按分隔符逐段回退（从长到短）→ 归一化后相等。查不到返回 `("", None)`。

        前两级与改动前一字不差（`_lookup_name` 的旧口径），第三级是新增的兜底。
        整串精确必须排在最前：图名**自己带全角括号**（`无人机实飞测试现场（一）`）时，
        一上来就按 `（` 切会把名字腰斩、反而永远查不到——真数据上踩过。
        """
        raw = (raw or "").strip()
        if not raw:
            return "", None
        bound = re.search(r"素材ID\s*[=:：]\s*(IMG-[0-9a-f]{24})", raw)
        if "素材ID" in raw:
            # 明确绑定了 ID 却不可用时，不许回退同名的另一张图。
            asset = self.assets.get(bound.group(1)) if bound else None
            if asset and '|自定义图注=1' in raw:
                from app.services.image_caption import reference_caption
                return reference_caption(raw.split('|',1)[0].strip(),asset),asset['path']
            return (asset["caption"], asset["path"]) if asset else ("", None)
        if raw in self:
            return raw, self[raw]
        # 括号内的层次/类别是图名的一部分，不可把数据层误认成应用层。
        cands = [raw[:m.start()].strip() for m in re.finditer(r"[/／，,]", raw)]
        cands.reverse()  # 有多个用途分隔符时先试最长的图名
        for cand in cands:
            if cand and cand in self:
                return cand, self[cand]
        for cand in [raw, *cands]:
            if not cand:
                continue
            hits = self._norm.get(normalize_image_name(cand)) or []
            # 去掉序号后对应多张不同图片时无法确定，不擅自取第一张。
            if hits and len({self._file_key(self[nm]) for nm in hits}) == 1:
                return hits[0], self[hits[0]]
        return "", None

    def take(self, raw: str) -> tuple[str, Path | None]:
        """`_match` + 全书去重：已经插过的图名一律不再命中（返回空 = 正文里留红字）。"""
        name, path = self._match(raw)
        key = self._file_key(path) if path is not None else None
        if name and key in self._used_files:
            return "", None
        if name:
            self._used.add(name)
            if key is not None:
                self._used_files.add(key)
        return name, path


#: 与 `docx_export._NAME_SPLIT_RE` 同口径（`[图片占位：图名/用途]` 里切图名那半截）。
#: 两处必须一起改：这边切不出正确的前缀，第三级归一化就会拿整串（含用途）去比，必然不中。
_NAME_SPLIT_RE = re.compile(r"[/／，,（(]")


def project_image_index_flat(project_id: int) -> ProjectImages:
    """把 `project_image_index` 的分章索引按目录序并成一份**全书**索引（导出侧用）。

    并集包含所有章被分配到的图名——**包括被判给别章、在本章让位掉的那些**：让位只该影响
    【建议图片】给不给（防写手照着画重复图），不该让这张图在别处写了占位时查不到。
    """
    flat: dict[str, Path] = {}
    try:
        per_node = project_image_index(project_id)
    except Exception:  # noqa: BLE001 读库/读盘任何一步出错都只是"这次不换图"
        return ProjectImages()
    for own in per_node.values():
        for nm, p in own.items():
            flat.setdefault(nm, p)          # 先出现的赢（`project_image_index` 的键序 = 目录序）
    try:
        from app.services.image_retrieval import catalog

        assets = {}
        for asset in catalog(project_id):
            assets.setdefault(asset["asset_id"], asset)
    except Exception:
        assets = {}
    return ProjectImages(flat, assets)

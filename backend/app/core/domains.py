"""领域注册表：专业标签词表按领域隔离，各领域知识库互不串味。

背景：原先 PROFESSION_TAGS 是一张铁路专用词表，硬编码在 knowledge_agent /
matching / orchestrate_agent（死代码）三处，加根目录盘点脚本共 4 份副本。低空文档
提炼时提示词里给的仍是铁路词，模型只能往最泛的「信息」上靠——实测 68% 的知识页
被贴上同一个标签，三级漏斗第一级过滤形同虚设。

domain 贯穿两条链路：
  提炼：knowledge_doc.domain → 提示词用该域词表 → 写入 knowledge_page.domain
  匹配：project.domain       → 只在同域知识页里找候选（不再全表读）

新增领域只需在 DOMAINS 里加一项（前端下拉经 list_domains 自动出现）。
**领域词表一共五张，都挂在这里的 `Domain` 上：专业标签 `tags`、章级骨架 `chapters`、
工程类型 `project_types`、标段档案字段 `lot_fields`、体裁名词 `genre`。**前端与提示词里
不该再有第六张写死的领域词表——2026-10-15 清掉的那几张（SKELETON_CHAPTERS /
SUGGESTED_CHAPTERS / HomeView 的工程类型数组 / lot_profile_service 里那张工程量字段表 /
十几处提示词里的「施工组织设计 / 施组 / 技术标」）都是这么来的。
判据很简单：**换一个领域，这张表还能原样用吗？** 不能，它就该挂到这里来。

**领域知识只能有两个来源：这个项目的招标文件，和这个领域的知识库。**
2026-10-15 补记：此前章级骨架还偷偷有第三个来源——`orchestrate_agent.SKELETON_CHAPTERS`，
一张写死的铁路土建章名表（「施工方案/临时与过渡工程/控制与重难点工程」…），模块级常量、
读的时候不看 domain。它当初的注释写着「11 类，**跨工程类型通用**」——写它的人真的以为它通用，
因为那时候只有一个领域。加了 lowalt 之后它就成了「一个被误认为领域无关的领域常量」：
新建的低空项目照领一份铁路骨架，而知识库里 1156 页对得上评审办法的语料**在章级压根够不着**
（`chapter_skeleton` 只喂子节层）。那张表已删，改为 `chapters` 字段 + 下面的回退顺序。

**章级骨架的回退顺序**（`chapters_of`）：
    招标规定的目录 > 本域 `chapters` > 让模型按项目描述自编
但"招标规定的目录"只是招标文件的一种形态——**招标要求与评分办法同样是招标文件**。
它在场时本域 `chapters` 就只是**补漏参考**，不是红线：`Domain.chapters` 里那几支往往是
从**某一份**招标文件读出来的（lowalt 五支就是从本项目评审办法抄的），拿它去压另一份
招标文件，新招标要求的内容会被挤掉——表现成"目录被强制收缩"。
第二档留空（`()`）是**合法且常见**的——域没有声明就交给模型，**绝不回退到别的域**。
静默回退正是上面那个 bug 的成因，`get_domain` 原先的 `or DOMAINS[default]` 是同一个形状，
2026-10-15 一并改掉：**「没给」才回退，「不认识」要出声**（见 `_fallback_domain`）。
"""
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class LotField:
    """「标段档案」（`lot.profile`）的一个字段。

    抽什么、叫什么、给模型怎么描述，三件事都在这里；`hint` 里写什么例子就等于给模型
    定什么调——原先那张写死的字段表就是这么把「梁场、拌和站、盾构管片场、铺轨基地」
    塞进每个项目的抽取提示词的。
    """

    key: str      # 存进 lot.profile 的字段名（稳定标识，不改）
    label: str    # 界面与注入文本里那一行的名字
    hint: str     # 抽取提示词里给模型的说明
    #: 值形态，决定怎么渲染、怎么合并、算不算白名单词：
    #:   "list"       —— ["名字", ...]（主要工点、大型临时设施…）→ **参与正文白名单**
    #:   "milestones" —— [{"name","days","start","end"}]（关键节点，界面上是表格）
    #:   "ranges"     —— ["DK1+000～DK2+000"]（区间，供标段归属判定；**不进白名单**）
    kind: str = "list"


@dataclass(frozen=True)
class DocGenre:
    """本领域**要编制的这份文件**叫什么——提示词里的体裁标签全从这里取。

    这是第五张领域词表（前四张：`tags`/`chapters`/`project_types`/`lot_fields`）。
    它比看上去更像一张领域表：原先这些词**写死在十来个提示词常量里**，于是每个低空
    项目的提示词都在自称「资深施工组织设计（实施性施组/技术标）编制专家」，模型据此
    把成稿写成了铁路施组的口吻；`requirement_agent.TOC_KW` 同样拿这几个词去原文里
    认「本册目录」，低空采购文件一个都不沾，关键词窗口恒为空。

    三个词分开是因为它们在提示词里**确实分位置出现**：自我介绍用正式全称（`full`），
    行文里指代这份成稿用简称（`short`，「整本<short>即该标段的<bid>」），说到"它在投标
    文件里是哪一册"用 `bid`（评标口径）。三个词一样时（低空就是）`intro` 不摆括号别名。
    """

    full: str       # 正式全称：施工组织设计 / 技术方案
    short: str      # 简称（行文指代）：施组 / 技术方案
    bid: str        # 投标文件里的那一册：技术标 / 技术方案
    #: 在招标文件里认出「本册目录」的关键词（对应 `requirement_agent.TOC_KW`）。
    #: 换域就认不出本册目录——所以它属于这里。空元组＝本域不给，只靠通用词兜。
    keywords: tuple[str, ...] = ()

    @property
    def intro(self) -> str:
        """自我介绍里的体裁串：有别名才摆括号，三词相同时不摆。

        「资深施工组织设计（实施性施组/技术标）编制专家」→ 铁路读着自然，
        但同一句套到低空会变成「资深技术方案（技术方案/技术方案）编制专家」。
        """
        if len({self.full, self.short, self.bid}) == 1:
            return self.full
        return f"{self.full}（{self.short}/{self.bid}）"


@dataclass(frozen=True)
class Domain:
    key: str                     # 入库用的稳定标识（不改）
    name: str                    # 界面显示名
    tags: tuple[str, ...]        # 专业标签词表（提炼提示词候选 + 匹配第一级过滤）
    #: 本领域要编制的这份文件叫什么（见 `DocGenre`）。**必填、无默认**——加一个新领域
    #: 却不声明成稿叫什么，正是 2026-10-15 之前那个 bug 的形状（每个低空项目的提示词
    #: 都把铁路的「施工组织设计/施组/技术标」当成自己的名字）。宁可构造时报错，
    #: 也不要给一个"中性默认词"——那只是把某一个域的说法偷偷变成所有人的默认。
    genre: DocGenre
    #: 本章该有哪些**一级章**（招标没规定施组目录时的候选骨架）。
    #: 空元组＝本域不声明，交给模型按项目描述自编——**不要**拿别的域来凑。
    #: 这一档只是"招标没规定 + 语料还不够用"时的兜底，写宽写窄都行，可随时改。
    chapters: tuple[str, ...] = ()
    #: 「工程类型」的候选选项（前端建项目/改项目时的 chip，以及项目列表的筛选器）。
    #: 空元组＝不声明，前端退回各领域选项的合集。
    #: 这也是一张**领域词表**：原先写死在前端（`['铁路','公路','房建','市政','水利']`），
    #: 加了低空领域之后那几个选项里根本没有低空——和几张章名表是同一个毛病。
    project_types: tuple[str, ...] = ()
    description: str = ''
    #: 「标段档案」抽哪些字段。**空元组＝本域没有这套口径**：不抽取、不注入正文、
    #: 也不用它的词裁正文（见 `lot_profile_service`）。
    #: 原先这张表写死在服务里，是**整张铁路工程 schema**（里程/路基/桥梁/隧道/
    #: 箱梁预制架设/联络线/铺轨/邻近既有线/弃土渣场），任何项目一建标段档案就要领一份。
    #: 低空与软件测试都是单一交付、根本没有「标段」这个层次（实测：低空那份采购文件
    #: 全文「标段」出现 0 次，采购范围是一个项目、一个交付地点、30 日历天交货），
    #: 所以两边都声明空——这不是"还没填"，是**本域确实没有**。
    lot_fields: tuple[LotField, ...] = ()


DOMAINS: dict[str, Domain] = {
    "lowalt": Domain(
        key="lowalt",
        name="低空 / 无人机",
        # 取自两份低空标书的章节标题用语（"指令验证/机巢部署/起降场调度"等），
        # 第一级过滤是拿「待写章节标题」撞这些词，贴近标题用语命中率最高。
        tags=(
            "实飞测试", "飞行试验", "演示验证", "指令验证",
            "监视能力", "数据治理", "机巢部署", "高密度大容量",
            "起降场调度", "飞控算法", "空域协调", "任务载荷",
            "多机协同", "安全保障",
        ),
        # 取自本项目招标**评审办法**的技术评审项（技术指标响应 20 分 + 技术方案编制 33 分，
        # 后者拆成工程化交付 10 / 测试验收准备 10 / 项目实施方案 5 / 售后服务 8）。
        # 五支都在 lowalt 知识库里有实打实的子节骨架（实测：4/7/12/12/7 个子节，
        # 头几个子节分别被写到 157 / 111 / 86 页），章与子节能接成一份连贯的两级目录。
        #
        # 语料第二层其实还能聚出第六支「技术方案和服务培训」（143 页）——那是另一本参考
        # 标书的并行命名（它底下居然又有一个「测试验收准备方案」子节），收进来会和上面几支
        # 重复。**不收，但这不是"砍掉"**：这一档本来就只是"招标没规定 + 没语料"时的兜底，
        # 一份招标文件要求写什么，由那份招标文件自己说了算（见下面 ⚠）。
        #
        # ⚠ 这五支是**从本项目那份招标文件的评审办法里读出来的**，不是低空领域的定律。
        # 以前它被当成"以它为准落地"，等于拿上一份招标文件的答案去压下一份：新招标要求
        # 的内容被这五支挤掉，表现成"目录被强制收缩成五章"。现在招标要求在场时它降级为
        # 补漏参考（`orchestrate_agent._chapters_block` 的 tender_led 分支 +
        # `TOC_TITLES_SYSTEM` 第 3 条第一种情形）。**要加要减都按招标文件来，别在这里加减。**
        chapters=(
            "技术指标响应情况",
            "工程化交付方案",
            "测试验收准备方案",
            "项目实施方案",
            "项目售后服务与保障能力",
        ),
        # 工程类型落库后会被 `compile_service` 塞进 professions 首位（见 assemble_inputs），
        # 所以这里给的是**本领域的说法**，别再填「铁路」这类别的领域的词。
        project_types=("飞行测试验证", "软件测试", "平台软件开发", "系统集成与部署", "运维与技术保障", "综合服务"),
        description='行业与业务场景归属。涉及实际飞行、无人机、机巢或低空平台的项目，包含软件测试时仍选择本领域。',
        # 来自**本项目的采购文件本身**（`file_section_index` 全文 12891 字，22 章）：
        # 响应文件目录是「一、响应函 二、响应报价表 三、响应情况对照表 **四、技术方案**
        # 五、公司简介 …」，正文写着「## 四、技术方案　按照附件三评审办法的方案要求自行编制」。
        # 而「施工组织设计」在这份文件里出现 **0 次**（「标段」同样 0 次）——体裁词跟着
        # 铁路那套走是彻底错位的。三个位置在本域是同一个词，`intro` 因此不摆括号别名。
        genre=DocGenre(full="技术方案", short="技术方案", bid="技术方案",
                       # 认「本册目录」用：这份文件里「技术方案」是响应文件的一支，
                       # 而且它自己写着"按附件三评审办法自行编制"（没给目录）→
                       # 模型据此答 found=false，走域 chapters 兜底，正是期望的结果。
                       keywords=("技术方案",)),
    ),
    "softtest": Domain(
        key="softtest",
        name="通用 / 未明确行业",
        tags=(
            "测试用例", "功能测试", "性能测试", "接口测试", "自动化测试",
            "测试环境", "缺陷管理", "回归测试", "安全测试", "兼容性测试",
            "验收测试", "测试报告", "测试数据", "质量度量",
        ),
        project_types=("软件测试", "平台软件开发", "系统集成与部署", "运维与技术保障", "综合服务"),
        description='尚未明确行业归属的项目。通用是独立素材领域，不表示素材自动供其他领域使用；软件测试可在各行业作为服务类型选择。',
        # ⚠ 本域**还没有语料证据**（没有项目、没有上传过文件），这三个词是暂定值：
        # 软件测试类投标文件里那一册惯例叫「技术方案」/「测试方案」，先按前者声明。
        # 等真的传进一份本域的招标文件，照 lowalt 的做法（去 `file_section_index`
        # 里数词频、看响应文件目录怎么叫）校正这三个词，别让它一直靠猜。
        genre=DocGenre(full="技术方案", short="技术方案", bid="技术方案",
                       keywords=("技术方案", "测试方案")),
    ),
}


# 行业应用领域共用服务选项，但各自拥有独立素材池；不预设招标目录。
_SERVICE_TYPES = ("软件测试", "平台软件开发", "系统集成与部署", "运维与技术保障", "综合服务")
_TEST_TAGS = DOMAINS["softtest"].tags
for _key, _name, _tags, _description in (
    ("government", "政务", ("政务服务", "行政审批", "数据共享", "电子政务"), "政务服务平台、行政审批、公共服务与政务数据系统。"),
    ("finance", "金融", ("银行业务", "支付结算", "保险业务", "证券交易", "金融风控"), "银行、支付、保险、证券等金融业务系统。"),
    ("healthcare", "医疗卫生", ("医院信息系统", "电子病历", "医疗数据", "互联网医疗"), "医院信息系统、电子病历、医疗健康与互联网医疗平台。"),
    ("education", "教育", ("教务管理", "考试平台", "在线教学", "教育数据"), "教务、考试、在线教学与教育管理系统。"),
    ("manufacturing", "工业制造", ("生产管理", "工业互联网", "设备监控", "制造执行"), "生产管理、制造执行、工业互联网与工业设备相关系统。"),
    ("transport", "交通运输", ("交通调度", "铁路信息系统", "轨道交通", "物流管理", "公路信息系统"), "铁路、公路、轨道交通、物流等领域的信息系统与相关测试服务。"),
    ("energy", "能源电力", ("电网监控", "能源管理", "充电平台", "电力调度"), "能源管理、电网监控、电力调度与充电服务平台。"),
    ("telecom", "通信", ("通信业务", "网络管理", "通信计费", "网络监测"), "通信业务、网络管理、计费与网络监测系统。"),
    ("retail", "商业零售", ("电子商务", "订单管理", "会员管理", "供应链管理"), "电商、零售、订单、会员与供应链业务系统。"),
    ("enterprise", "通用企业管理", ("办公协同", "人力资源", "财务管理", "客户管理"), "办公、人事、财务、客户管理等跨行业企业内部应用。"),
):
    DOMAINS[_key] = Domain(
        key=_key, name=_name, tags=_tags + _TEST_TAGS,
        genre=DocGenre(full="技术方案", short="技术方案", bid="技术方案",
                       keywords=("技术方案", "测试方案")),
        project_types=_SERVICE_TYPES,
        description=_description + " 服务范围与目录以本次招标文件为准；历史素材仅在本领域内检索。",
    )


#: 已经报过警告的未知领域 key（见 `_fallback_domain`）。同 key 只报一次。
_warned_unknown: set[str] = set()


def _fallback_domain(key: str) -> Domain:
    """未知 key 的回退：给默认域，但**必须出声**。

    这里原先是 `DOMAINS.get(key) or DOMAINS[default_domain]`——一道静默的跨域兜底，
    与 2026-10-15 那个 bug（写死的铁路章名表在低空项目上生效）是同一个形状：**不认识
    的领域名悄悄变成了默认域**，下游一路按默认域的知识库取候选，没有任何一处报错。
    `Domain("railway")` 删掉之后，任何 `domain='railway'` 的历史行都会走到这里。

    仍然回退而不抛错：编制链路的既有约定是「宁可退化，不要抛错」，一行脏数据不该让
    整个项目打不开。但退化必须留痕——同一个 key 只报一次，`get_domain` 在匹配链路上
    被反复调用，每次都打等于没打。
    """
    if key and key not in _warned_unknown:
        _warned_unknown.add(key)
        print(f"[领域] 不认识的领域 key {key!r} → 回退默认域 {settings.default_domain!r}"
              f"（合法 key：{'、'.join(DOMAINS)}）。请检查 project.domain /"
              f" knowledge_doc.domain 里是不是有已删除的领域。")
    default = DOMAINS.get(settings.default_domain)
    if default is None:  # 配置本身写错：这个不该退化，直接指出是哪一处
        raise KeyError(f"settings.default_domain={settings.default_domain!r} 不在 DOMAINS 里"
                       f"（合法 key：{'、'.join(DOMAINS)}）")
    return default


def get_domain(key: str | None) -> Domain:
    """按 key 取领域。**没给**和**不认识**是两回事，别再合并成一条路：

    - 没给（None / 空串 / 全空白）：调用方就是没传 → 默认域，合法，不出声；
    - 给了但不认识：**数据有问题** → 仍然回退默认域，但打一条警告（同 key 一次）。
      详见 `_fallback_domain`。
    """
    k = (key or "").strip()
    if k in DOMAINS:
        return DOMAINS[k]
    return _fallback_domain(k)


def tags_of(key: str | None) -> tuple[str, ...]:
    """该领域的专业标签词表。"""
    return get_domain(key).tags


def chapters_of(key: str | None) -> tuple[str, ...]:
    """该领域的**一级章骨架**候选；本域没声明就返回空元组。

    **空不是错误**，调用方据此让模型按项目描述自编（见模块 docstring 的回退顺序）。
    这里刻意不做任何跨域兜底——拿别的域的章名去填，正是 2026-10-15 那个 bug。
    """
    return get_domain(key).chapters


def lot_fields_of(key: str | None) -> tuple[LotField, ...]:
    """该领域的**标段档案字段表**；本域没声明就返回空元组＝本域不适用标段档案。

    同 `chapters_of`：**空不是错误**，调用方据此整条链不参与（不抽取、不注入、不裁剪）。
    这里同样不做任何跨域兜底——`lot_profile_service` 原先那张写死的字段表，
    正是"拿别域的 schema 往本域套"的样本。
    """
    return get_domain(key).lot_fields


def genre_of(key: str | None) -> DocGenre:
    """该领域的**体裁名词**（要编制的这份文件叫什么）。见 `DocGenre`。"""
    return get_domain(key).genre


#: 提示词常量里的体裁占位符。用 `.replace` 填、**不要**用 `str.format`——这些提示词
#: 里满是 JSON 示例的花括号，format 会把它们当成占位符吃掉（同 `tender_agent._system_prompt`）。
G_FULL, G_SHORT, G_BID, G_INTRO = "{G_FULL}", "{G_SHORT}", "{G_BID}", "{G_INTRO}"


def fill_genre(text: str, key: str | None) -> str:
    """把提示词常量里的体裁占位符换成该领域的词。

    每个 LLM 调用点都要走一次：**提示词里不该再出现任何一处写死的体裁名词**
    （「施工组织设计」「施组」「技术标」），否则那个域的模型就会按别的域的体裁写文件。
    """
    g = genre_of(key)
    return (text.replace(G_FULL, g.full).replace(G_SHORT, g.short)
                .replace(G_BID, g.bid).replace(G_INTRO, g.intro))


def domain_of_project(project_id: int | None) -> str:
    """项目的领域 key（编制链路上各处都用它收敛候选池）。

    查不到项目时回退默认域——编制流程的既有约定是「宁可退化，不要抛错」，
    缺行已经由调用方各自报错，这里再抛会把错误信息盖掉。
    """
    if not project_id:
        return get_domain(None).key
    from app.core.database import query_one

    row = query_one("SELECT domain FROM project WHERE id = %s", (project_id,))
    return get_domain((row or {}).get("domain")).key


def list_domains() -> list[dict]:
    """供前端下拉：全部领域 [{key, name, tags, chapters, project_types}]，默认域排最前。

    前端**不该再有**写死的领域词表（章名、专业标签、工程类型都从这里取）。
    """
    items = sorted(DOMAINS.values(), key=lambda d: d.key != settings.default_domain)
    return [{"key": d.key, "name": d.name, "tags": list(d.tags),
             "chapters": list(d.chapters), "project_types": list(d.project_types), "description": d.description,
             # 前端据此决定要不要显示「标段」那套入口（空 = 本域没有标段这个层次）
             "lot_fields": [f.key for f in d.lot_fields],
             # 本域要编制的这份文件叫什么（提示词与界面上都该用它，别写死「施组」）
             "genre": {"full": d.genre.full, "short": d.genre.short, "bid": d.genre.bid}}
            for d in items]

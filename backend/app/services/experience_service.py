"""经验条目服务（M3-4）：沉淀入库 + 生成时匹配注入。

- save_experience：去重（同域同章节同内容）后入 experience_item（待审核 pending）；
- match_experiences：编写/思路生成时按章节标题相似度匹配【已确认】经验，
  全局条目（chapter_type 为空）恒入；优先级高于知识页（用户偏好级，方案 M3-4）。

**按域隔离（2026-10-15）**：经验库是**跨项目复用的写法规则**，所以落的是 `domain`
而不是 `project_id`——按项目隔离等于废掉这个库；按域隔离才和知识库
（`knowledge_doc`/`knowledge_page`）一致：低空项目沉淀的经验不会流进软件测试项目。

原先这张表**没有域这一列**：`match_experiences` 读全表、只按标题相似度筛，于是
任何领域的经验都可能注进任何项目的任何一章。写入侧更冤——`distill_*_task` 手里
**明明有** `project_id`，却在 `save_experience` 那一跳把它丢进了 `source` JSON 里、
没落成可查询的列。现在写入时落域、匹配时按域过滤，`source.project_id` 只作来源留痕。
"""
import json
import re

from app.core.database import execute, query, query_one
from app.services.matching import _dice, normalize_title

# 章节标题的编号前缀：第一章 / 第一节 / 1.1 / 一、 / （一） 等（各项目编号风格不同，都要剥掉）
# 注意：中文数字必须跟顿号/点（"一、"），否则会把"一般规定"这类标题误剥。
_STRIP_NUM_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[一二三四五六七八九十百零〇\d]+\s*[章节篇节]"
    r"|[（(]\s*[\d一二三四五六七八九十百零〇]+\s*[)）]"
    r"|\d+(?:\s*[.．]\s*\d+)*"
    r"|[一二三四五六七八九十百零〇]+\s*[、.．]"
    r")\s*[、.．:：\s]*"
)


def _norm_title(t: str) -> str:
    """章节标题归一：**剥掉编号前缀**后再比对。

    这样目录重建（编号从"1.1"变成"第一节"等）后，历史经验仍能匹配上——
    此前旧目录标题的经验在新目录下一律失配，导致"取材来源"里看不到经验。
    """
    return normalize_title(_STRIP_NUM_RE.sub("", (t or "").strip()))

MAX_INJECT = 10        # 每章最多注入经验条数
SIM_THRESHOLD = 0.35  # 章节标题相似度阈值（与 matching 模糊带一致）


def strip_chapter_no(t: str) -> str:
    """剥掉章节标题的编号前缀，只留标题名（入库与显示统一用它）。

    库里存编号没有意义：匹配本来就按标题名（`_norm_title` 同样剥编号），而编号只是
    目录生成的产物、因项目而异（"1.1 编制依据" / "第一节 编制依据" / "一、编制依据"
    是同一样东西）。存编号还会让同一章节的经验看起来像好几章、界面显示也啰嗦。
    """
    return _STRIP_NUM_RE.sub("", (t or "").strip()).strip()


def save_experience(item: dict, domain: str = "") -> int | None:
    """新提炼经验以 pending 入库，人工确认后才参与生成。
    同域同章节同内容去重，返回 id 或 None。

    `domain` 缺省时从 `item["source"]["project_id"]` 反查——三个提炼入口都会把来源
    项目写进 source，所以正常路径不必显式传；**显式传参优先**，便于后续有别的入口。
    """
    content = (item.get("content") or "").strip()
    if not content:
        return None
    chapter_type = strip_chapter_no(item.get("chapter_type") or "")
    source = item.get("source")
    if not isinstance(source, dict):
        source = {}
    if not domain:
        from app.core.domains import domain_of_project

        domain = domain_of_project(source.get("project_id"))
    # 去重也按域：写得一模一样的"避免口号式表述"，在另一个领域是另一条经验，
    # 不该被这一域的那条挡掉（挡掉之后它永远不会出现在那个域的匹配里）。
    dup = query_one(
        "SELECT id FROM experience_item WHERE domain = %s AND chapter_type = %s"
        " AND content = %s LIMIT 1",
        (domain, chapter_type, content),
    )
    if dup:
        return None
    return execute(
        "INSERT INTO experience_item"
        " (exp_type, chapter_type, tags, content, source, status, domain)"
        " VALUES (%s, %s, %s, %s, %s, 'pending', %s)",
        (
            item.get("exp_type", "编写经验"), chapter_type,
            json.dumps(item.get("tags") or [], ensure_ascii=False),
            content, json.dumps(source, ensure_ascii=False), domain,
        ),
    )


def match_experiences(node_title: str, node_path: list[str] | None = None,
                      domain: str | None = None) -> list[dict]:
    """匹配已确认经验：全局条目恒入 + 章节标题相似度达标条目，按相关度降序。

    `domain` 决定在哪个领域的经验库里找（同 `matching.load_page_summaries` 的约定）：
    **正常匹配务必传**，否则各域经验会互相注入；为空只用于盘点/调试。
    """
    sql = ("SELECT id, exp_type, chapter_type, tags, content FROM experience_item"
           " WHERE status = 'confirmed'")
    params: tuple = ()
    if domain:
        sql += " AND domain = %s"
        params = (domain,)
    rows = query(sql + " ORDER BY id DESC", params)
    if not rows:
        return []
    target = _norm_title(" ".join([*(node_path or []), node_title]))
    global_items, scored = [], []
    for r in rows:
        ct = (r["chapter_type"] or "").strip()
        if not ct:
            global_items.append(r)
            continue
        s = _dice(target, _norm_title(ct))
        if s >= SIM_THRESHOLD:
            scored.append((s, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = global_items + [r for _s, r in scored]
    return [
        {"id": r["id"], "exp_type": r["exp_type"], "chapter_type": r["chapter_type"],
         "content": r["content"]}
        for r in picked[:MAX_INJECT]
    ]


# ---------------------------------------------------------------- 异步提炼任务（触发点用）

def _domain_of(project_id: int | None) -> str:
    """提炼入口统一的域解析：经验落在**来源项目的域**里（不是默认域）。"""
    from app.core.domains import domain_of_project

    return domain_of_project(project_id)


def distill_edit_task(task_id: int, project_id: int, chapter_title: str, before: str, after: str) -> None:
    """用户修改 AI 内容保存 → 提炼（task_runner 入口）。"""
    from app.agents.experience_agent import distill_from_edit

    item = distill_from_edit(chapter_title, before, after, project_id)
    if item:
        save_experience(item, _domain_of(project_id))


def distill_reject_task(task_id: int, project_id: int, chapter_title: str, rejected: str) -> None:
    """用户回退否决 AI 内容 → 提炼（task_runner 入口）。"""
    from app.agents.experience_agent import distill_from_reject

    item = distill_from_reject(chapter_title, rejected, project_id)
    if item:
        save_experience(item, _domain_of(project_id))


def distill_chat_task(task_id: int, project_id: int, chapter_title: str,
                      user_message: str, refs: list[dict]) -> None:
    """对话纠偏指令 → 提炼（task_runner 入口）。"""
    from app.agents.experience_agent import distill_from_chat

    item = distill_from_chat(chapter_title, user_message, refs, project_id)
    if item:
        save_experience(item, _domain_of(project_id))

"""环境自检脚本：验证配置项 / MySQL / 大模型端点 / MinerU token / Python 依赖。

用法：
    python scripts/check_env.py            # 只自检，不改动数据库
    python scripts/check_env.py --init-db   # 自检 + 建表 + 结构迁移
"""
import argparse
import base64
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

# 每一项配置缺失时的可操作提示（自检失败时打印，避免用户面对裸报错不知所措）
HINTS = {
    "LLM_API_KEY": "去 https://bailian.console.aliyun.com → API-KEY 管理 创建一个，填入 .env 的 LLM_API_KEY",
    "MINERU_API_TOKEN": "去 https://mineru.net → API 管理 → 创建 Token，填入 .env 的 MINERU_API_TOKEN",
    "MYSQL_PASSWORD": "填写你的 MySQL 密码；本机 MySQL 无密码则留空",
}


def check_env_file() -> str:
    """确认 .env 存在且有必填项，缺失时给出可操作提示。"""
    from app.core.config import settings

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise RuntimeError(
            "项目根目录没有 .env 文件。\n"
            "        → 执行：cp .env.example .env\n"
            "        → 然后按 .env.example 里的注释填写 3 个必填项"
        )

    problems = []
    if not settings.llm_api_key.strip():
        problems.append(f"LLM_API_KEY 为空 —— {HINTS['LLM_API_KEY']}")
    if not settings.mineru_api_token.strip():
        problems.append(f"MINERU_API_TOKEN 为空 —— {HINTS['MINERU_API_TOKEN']}")
    if not settings.mysql_host.strip():
        problems.append("MYSQL_HOST 为空 —— 填 127.0.0.1（本机）或你的数据库地址")
    if problems:
        raise RuntimeError("配置项不完整：\n        - " + "\n        - ".join(problems))
    return f".env 已加载（MySQL={settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}）"


def check_mysql() -> str:
    from app.core.database import check_connection

    ver = check_connection()
    return f"MySQL 连接成功（{ver}）"


def init_db() -> str:
    import pymysql
    from app.core.config import settings
    from app.core.database import get_conn

    raw = (PROJECT_ROOT / "scripts" / "init_db.sql").read_text(encoding="utf-8")
    # 先去掉注释行，再按分号切分，避免整段被注释行误过滤
    no_comments = "\n".join(
        line for line in raw.splitlines() if not line.strip().startswith("--")
    )
    # 注意：PyMySQL 的 with conn: 退出即关闭连接，所有操作须在块内完成
    with get_conn() as conn, conn.cursor() as cur:
        for stmt in [s.strip() for s in no_comments.split(";") if s.strip()]:
            if stmt.upper().startswith("USE "):
                continue
            cur.execute(stmt)
        cur.execute("SHOW TABLES")
        tables = [next(iter(r.values())) for r in cur.fetchall()]
    return f"建表完成，共 {len(tables)} 张表: {', '.join(tables)}"


def check_llm() -> str:
    import httpx
    from app.core.config import settings

    if not settings.llm_base_url.strip():
        raise RuntimeError("LLM_BASE_URL 为空 —— 填写你的 OpenAI 兼容端点地址（见 .env.example）")
    try:
        r = httpx.get(
            f"{settings.llm_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"无法连接 {settings.llm_base_url} —— 检查网络或地址是否正确（{e}）") from e
    if r.status_code in (401, 403):
        raise RuntimeError(f"鉴权失败（HTTP {r.status_code}）—— {HINTS['LLM_API_KEY']}")
    r.raise_for_status()
    ids = [m.get("id", "") for m in r.json().get("data", [])]
    if not ids:
        raise RuntimeError("端点连通但没返回任何模型 —— 确认该地址是 OpenAI 兼容的 /v1 端点")
    model_ok = settings.text_model in ids
    return (
        f"大模型端点连通（可用模型 {len(ids)} 个，"
        f"{'已包含' if model_ok else '未直接列出'} TEXT_MODEL={settings.text_model}）"
    )


def check_mineru() -> str:
    """校验 MinerU token：老版 JWT 解析 exp；新版 sk- token 无 exp，改用探活请求。"""
    from app.core.config import settings

    token = settings.mineru_api_token

    # ---------- 老版 JWT（eyJxxx.yyy.zzz）：本地解析 exp，不发请求 ----------
    if len(token.split(".")) == 3:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        try:
            exp = json.loads(base64.b64decode(payload))["exp"]
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"无法解析 MinerU Token —— {HINTS['MINERU_API_TOKEN']}") from e
        from datetime import datetime

        exp_dt = datetime.fromtimestamp(exp)
        days = (exp_dt - datetime.now()).days
        if days < 0:
            raise RuntimeError(
                f"MinerU Token 已于 {exp_dt:%Y-%m-%d %H:%M} 过期 —— {HINTS['MINERU_API_TOKEN']}"
            )
        warn = "  ⚠ 即将到期，请提前续期！" if days < 7 else ""
        return f"MinerU token 有效期至 {exp_dt:%Y-%m-%d %H:%M}（剩 {days} 天）{warn}"

    # ---------- 新版 sk- token（无 exp 字段）：查一个不存在的批次探活 ----------
    # 该请求不创建任务、不消耗解析配额；鉴权失败返回 401/403。
    import httpx

    base = (settings.mineru_api_base or "https://mineru.net/api/v4").rstrip("/")
    try:
        r = httpx.get(
            f"{base}/extract-results/batch/0",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"无法连接 MinerU（{base}）—— 检查网络（{e}）") from e
    if r.status_code in (401, 403):
        raise RuntimeError(
            f"MinerU Token 鉴权失败（HTTP {r.status_code}）—— {HINTS['MINERU_API_TOKEN']}"
        )
    return f"MinerU token 鉴权通过（{token[:8]}… 新版 sk- 格式，无 exp 字段，有效期无法本地判定）"


def check_packages() -> str:
    import importlib.util

    names = ["fastapi", "uvicorn", "pymysql", "httpx", "langgraph", "docx2pdf", "docx"]
    missing = [n for n in names if importlib.util.find_spec(n.replace("-", "_")) is None]
    if missing:
        raise RuntimeError(
            f"缺少依赖 {missing} —— 执行：pip install -r backend/requirements.txt"
        )
    return "依赖齐全"


def migrate_db() -> str:
    """幂等迁移：给已有库补新增列/表（CREATE TABLE IF NOT EXISTS 不会改旧表）。

    每次有表结构变更时在此追加一条检查。
    """
    from app.core.database import get_conn

    applied = []
    with get_conn() as conn, conn.cursor() as cur:
        # 公司资料第一版新表；既有档案迁移由 migrate_company_v1.py 执行并备份。
        company_sql = (Path(__file__).parent / 'company_v1.sql').read_text(encoding='utf-8')
        for statement in company_sql.split(';'):
            if statement.strip():
                cur.execute(statement)
        # chapter_node.outline（编写思路落库，2026-09-03 新增）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chapter_node'"
            " AND COLUMN_NAME = 'outline'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE chapter_node ADD COLUMN outline JSON NULL"
                        " COMMENT '编写思路（确认思路后落库，正文生成的输入）'")
            applied.append("chapter_node.outline")
        # lot.selected（标段选定标记，M1-2 新增）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lot'"
            " AND COLUMN_NAME = 'selected'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE lot ADD COLUMN selected TINYINT DEFAULT 0"
                        " COMMENT '用户选定的标段（M1-2：多选一/单标段确认）'")
            applied.append("lot.selected")
        # project.toc_hint（招标文件规定的施组目录，2026-09-03 新增）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'"
            " AND COLUMN_NAME = 'toc_hint'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE project ADD COLUMN toc_hint JSON NULL"
                        " COMMENT '招标文件规定的施组目录（目录生成优先采用）'")
            applied.append("project.toc_hint")
        # project.image_policy（项目级配图偏好，2026-10-23 新增）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'"
            " AND COLUMN_NAME = 'image_policy'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE project ADD COLUMN image_policy JSON NULL"
                        " COMMENT '配图规划偏好（如涉及现场/实物时优先写实场景图）'")
            applied.append("project.image_policy")
        # knowledge_doc.file_md5 / project_file.file_md5（同文件跳过重复解析，2026-09-04 新增）
        for table in ("knowledge_doc", "project_file"):
            cur.execute(
                "SELECT COUNT(*) c FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                " AND COLUMN_NAME = 'file_md5'",
                (table,),
            )
            if cur.fetchone()["c"] == 0:
                cur.execute(
                    f"ALTER TABLE {table} ADD COLUMN file_md5 VARCHAR(32) NULL DEFAULT NULL,"
                    f" ADD KEY idx_md5 (file_md5)"
                    f" COMMENT '文件内容 MD5（同文件复用已解析结果，跳过 MinerU）'"
                )
                applied.append(f"{table}.file_md5")
        # global_fact.applicable_lots（事实按标段感知，2026-09-04 新增）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'global_fact'"
            " AND COLUMN_NAME = 'applicable_lots'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE global_fact ADD COLUMN applicable_lots JSON NULL"
                        " COMMENT '适用标段代码列表：[\"all\"]=全线通用，NULL=旧数据视为通用'")
            applied.append("global_fact.applicable_lots")
        # compile_session.partial_json（目录流式生成进度，2026-09-04 新增）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'compile_session'"
            " AND COLUMN_NAME = 'partial_json'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE compile_session ADD COLUMN partial_json JSON NULL"
                        " COMMENT '生成中的部分产物（目录流式：章骨架/已完成的子节）'")
            applied.append("compile_session.partial_json")
        # file_section_index（项目文件章节索引，2026-09-04 新增——skill 式按需加载）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.TABLES"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'file_section_index'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS file_section_index ("
                " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
                " file_id BIGINT NOT NULL COMMENT 'project_file.id',"
                " project_id BIGINT NOT NULL,"
                " sec_level INT NOT NULL DEFAULT 1 COMMENT '标题层级（1-3 级入索引）',"
                " sec_path VARCHAR(500) NOT NULL COMMENT '章节全路径标题（父 > 子）',"
                " summary VARCHAR(500) DEFAULT '' COMMENT 'AI 章节概要（该节讲了什么）',"
                " applies_to VARCHAR(300) DEFAULT '' COMMENT '适用范围（编写什么样的问题/章节时参考本节）',"
                " start_line INT NOT NULL DEFAULT 0 COMMENT 'full.md 起始行号（0 起）',"
                " end_line INT NOT NULL DEFAULT 0 COMMENT 'full.md 结束行号（不含）',"
                " char_count INT DEFAULT 0 COMMENT '章节字符数（含子节）',"
                " created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " KEY idx_file (file_id), KEY idx_project (project_id)"
                " ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='项目文件章节索引（按需加载原文）'"
            )
            applied.append("file_section_index")
        # method_index（工艺工法概况索引，一工法一条，2026-09-04 新增）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.TABLES"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'method_index'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS method_index ("
                " id BIGINT AUTO_INCREMENT PRIMARY KEY,"
                " doc_id BIGINT NOT NULL COMMENT 'knowledge_doc.id',"
                " name VARCHAR(300) NOT NULL COMMENT '工法名称',"
                " summary VARCHAR(600) DEFAULT '' COMMENT '工法概况',"
                " applies_to VARCHAR(400) DEFAULT '' COMMENT '适用范围',"
                " created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                " updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
                " KEY idx_doc (doc_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工艺工法概况索引'"
            )
            applied.append("method_index")
        # 标段档案列（lot_profile_service 依赖；补齐早期建库遗漏的三列）
        for col, ddl, comment in [
            ("profile", "ALTER TABLE lot ADD COLUMN profile JSON NULL",
             "标段档案：里程范围/工程量/主要构造物/大型临时设施/过渡工程/弃土渣场/关键里程碑（JSON）"),
            ("profile_source", "ALTER TABLE lot ADD COLUMN profile_source VARCHAR(300) NULL",
             "档案数据来源（文件名·章节，多来源分号分隔）"),
            ("profile_updated_at", "ALTER TABLE lot ADD COLUMN profile_updated_at DATETIME NULL",
             "档案最后更新时间"),
        ]:
            cur.execute(
                "SELECT COUNT(*) c FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'lot' AND COLUMN_NAME = %s",
                (col,),
            )
            if cur.fetchone()["c"] == 0:
                cur.execute(f"{ddl} COMMENT '{comment}'")
                applied.append(f"lot.{col}")
        # chat_message.trace（对话工具调用轨迹回显，2026-09-15 新增）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_message'"
            " AND COLUMN_NAME = 'trace'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE chat_message ADD COLUMN trace JSON NULL"
                        " COMMENT '本助手消息的工具调用轨迹(名称/参数摘要/成败)'")
            applied.append("chat_message.trace")
        # tender_requirement.applicable_lots（招标要求按标段过滤）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tender_requirement'"
            " AND COLUMN_NAME = 'applicable_lots'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE tender_requirement ADD COLUMN applicable_lots JSON NULL"
                        " COMMENT '该要求适用的标段（同 applies_lots 取值；空/含 all 表示全线通用）'")
            applied.append("tender_requirement.applicable_lots")
        # domain 领域隔离（2026-10-15 新增）：知识文档/知识页/项目各加领域标识，
        # 各域用各自的专业标签词表与知识库（见 app/core/domains.py），互不串味。
        # 已有数据由 DEFAULT 回填为当前默认域——迁移前系统只有一套词表在跑，
        # 存量知识归入默认域即可。
        from app.core.config import settings as _settings

        default_domain = _settings.default_domain
        for table, comment in [
            ("knowledge_doc", "知识领域（domains.py 注册表 key：lowalt=低空/无人机 等）；各域知识页互相隔离"),
            ("knowledge_page", "知识领域（冗余自 knowledge_doc.domain，匹配时按域过滤，免 join）"),
            ("project", "知识领域（决定匹配哪个域的知识库、用哪套专业标签词表）"),
            # 经验库：与知识库同一套隔离口径。落 `domain` 而不是 `project_id`——
            # 经验是**跨项目复用**的写法规则（"这类章节该怎么写"），按项目隔离等于废掉它；
            # 按域隔离才和知识库一致（各域经验互不串）。来源项目仍在 source.project_id 里留痕。
            ("experience_item", "知识领域（domains.py 注册表 key）；生成时按域过滤，各域经验互相隔离"),
        ]:
            cur.execute(
                "SELECT COUNT(*) c FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
                " AND COLUMN_NAME = 'domain'",
                (table,),
            )
            if cur.fetchone()["c"] == 0:
                cur.execute(
                    f"ALTER TABLE {table} ADD COLUMN domain VARCHAR(30) NOT NULL"
                    f" DEFAULT '{default_domain}' COMMENT '{comment}',"
                    f" ADD KEY idx_domain (domain)"
                )
                applied.append(f"{table}.domain")
                # 经验条目是**从项目里沉淀出来的**，默认域只是兜底；能查到来源项目的，
                # 一律回填成那个项目真正的域（否则一个低空项目的经验会被默认域标签
                # 带进别的域——正是这次要修的那个洞）。查不到来源的留默认域，宁可可见。
                if table == "experience_item":
                    try:
                        cur.execute(
                            "UPDATE experience_item e JOIN project p"
                            "  ON p.id = JSON_EXTRACT(e.source, '$.project_id')"
                            "   SET e.domain = p.domain"
                            " WHERE JSON_EXTRACT(e.source, '$.project_id') IS NOT NULL"
                        )
                        if cur.rowcount:
                            applied.append(f"experience_item.domain 回填 {cur.rowcount} 行")
                    except Exception as exc:  # noqa: BLE001 回填失败不该挡住建库
                        applied.append(f"experience_item.domain 回填失败（留默认域）: {exc}")
        # project.docx_style（导出 Word 的排版规格，2026-10-15 新增）
        # 空 = 用内置默认（「工程标书常见」，见 app/core/docx_style.py），
        # 不改结构化约束：加新旋钮只是多一个 JSON 键，无需再迁移。
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'"
            " AND COLUMN_NAME = 'docx_style'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE project ADD COLUMN docx_style JSON NULL"
                        " COMMENT '导出 Word 的排版规格（字体/字号/行距/页边距/页眉页脚；NULL=内置默认）'")
            applied.append("project.docx_style")
        cur.execute("SHOW COLUMNS FROM project LIKE 'service_content'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE project ADD COLUMN service_content TEXT NULL COMMENT '经用户保存的本次服务内容：与服务分类分开'")
            applied.append('project.service_content')
        for column, comment in [('project_types','项目业务类型列表：可多选，仅作生成侧重点，不隔离素材；NULL兼容旧分类'),
                                ('technical_objects','经用户确认的技术对象列表：从招标文件建议，可人工修正')]:
            cur.execute('SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s',('project',column))
            if not cur.fetchone()['n']:
                cur.execute(f"ALTER TABLE project ADD COLUMN {column} JSON NULL COMMENT '{comment}'")
                applied.append('project.'+column)
        # project.page_plan（篇幅控制：目标正文页数，2026-10-15 新增，第 2 轮）
        # 同 docx_style：加新键只是多一个 JSON 键，无需再迁移；NULL = 不设目标
        # （不设目标时生成侧维持原行为，不注入【篇幅要求】）。
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'project'"
            " AND COLUMN_NAME = 'page_plan'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE project ADD COLUMN page_plan JSON NULL"
                        " COMMENT '篇幅控制：目标正文页数与重写阈值（NULL=不设目标）'")
            applied.append("project.page_plan")
        # chapter_node.target_words（单章人工额度，覆盖按权重分摊出来的目标）
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chapter_node'"
            " AND COLUMN_NAME = 'target_words'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute("ALTER TABLE chapter_node ADD COLUMN target_words INT NULL"
                        " COMMENT '该章人工指定的目标字数（NULL=按篇幅计划的章节权重分摊）'")
            applied.append("chapter_node.target_words")
        # knowledge_doc.scope / project_id（知识库「归属墙」，2026-10-16 新增）
        # 领域墙（domain）挡的是别的行业，挡不住**同域别的项目**：实测一份别的项目的
        # 投标文件传进 lowalt 池，池子 1156 → 1353 页，生成正文就可能把那本标书的写法
        # 引进来。默认 'domain'（领域通用）＝ 加这道墙之前的行为，**存量行一个字不用改**。
        # 过滤走 JOIN 而不是冗余到 knowledge_page：归属是会被改的（把传错的标书改成
        # off 是本功能的主要用法），冗余就得同步两份，漏同步就"改完不生效"。
        cur.execute(
            "SELECT COUNT(*) c FROM information_schema.COLUMNS"
            " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'knowledge_doc'"
            " AND COLUMN_NAME = 'scope'"
        )
        if cur.fetchone()["c"] == 0:
            cur.execute(
                "ALTER TABLE knowledge_doc ADD COLUMN scope VARCHAR(20) NOT NULL"
                " DEFAULT 'domain' COMMENT '参与检索的归属：domain=领域通用（同域所有项目可参考）"
                "/project=仅 project_id 指定的项目可见/off=不参与检索（只留档）',"
                " ADD COLUMN project_id BIGINT NULL COMMENT 'scope=project 时归属的项目"
                "（project.id）；其余情形恒为 NULL',"
                " ADD KEY idx_scope (scope, project_id)"
            )
            applied.append("knowledge_doc.scope/project_id")
    return "已应用: " + ", ".join(applied) if applied else "结构已是最新"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BidCraft 环境自检 / 建表")
    parser.add_argument("--init-db", action="store_true", help="执行建表 SQL 与结构迁移")
    args = parser.parse_args()

    print("=== BidCraft 标书匠 · 环境自检 ===\n")

    checks = [
        ("Python 依赖", check_packages),
        ("配置文件", check_env_file),
        ("MySQL", check_mysql),
        ("大模型端点", check_llm),
        ("MinerU", check_mineru),
    ]
    failed = 0
    for name, fn in checks:
        try:
            print(f"[OK]   {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {name}: {e}")

    if args.init_db:
        for name, fn in [("建表", init_db), ("结构迁移", migrate_db)]:
            try:
                print(f"[OK]   {name}: {fn()}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[FAIL] {name}: {e}")

    print()
    if failed:
        print(f"=== 自检结束：{failed} 项需要处理（对照上方提示逐项修复后重跑）===")
        sys.exit(1)
    print("=== 全部通过，可以启动了 ===")
    print("    一键启动：scripts/start_dev.bat（Windows） 或 bash scripts/start_dev.sh（macOS/Linux）")

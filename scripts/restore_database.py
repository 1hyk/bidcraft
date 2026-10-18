"""恢复迁移备份到空库；拒绝覆盖已有表，不在命令行暴露密码。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pymysql
from pymysql.constants import CLIENT
from app.core.config import settings


def restore(path: Path) -> None:
    sql = path.read_text(encoding="utf-8-sig")
    name = settings.mysql_database
    if not name.replace("_", "").isalnum():
        raise ValueError("数据库名仅支持字母、数字与下划线")
    conn = pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port,
        user=settings.mysql_user, password=settings.mysql_password,
        charset="utf8mb4", client_flag=CLIENT.MULTI_STATEMENTS,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (name,))
            if cur.fetchone():
                cur.execute(f"SHOW TABLES FROM `{name}`")
                if cur.fetchone():
                    raise RuntimeError("目标库已有表，拒绝覆盖；请先另行备份并选择空库")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}` DEFAULT CHARACTER SET utf8mb4")
            conn.select_db(name)
            cur.execute(sql)
            while cur.nextset():
                pass
            conn.commit()
            cur.execute("SHOW TABLES")
            print(f"恢复完成：{name}，共 {len(cur.fetchall())} 张表")
    finally:
        conn.close()


if __name__ == "__main__":
    restore(Path(sys.argv[1]))

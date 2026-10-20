"""连接级临时表验证目录替换失败回滚，不写业务表。"""
import sys
import re
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.core.database import execute,query,query_one,get_conn
from app.services import section_service


if __name__=='__main__':
    conn=get_conn()
    try:
        for table in ['chapter_node','section_content']:
            schema=query_one('SHOW CREATE TABLE '+table)['Create Table']
            schema=schema.replace('CREATE TABLE','CREATE TEMPORARY TABLE',1)
            schema=re.sub(r',\n\s*CONSTRAINT[^\n]+','',schema)
            execute(schema)
        old=execute("INSERT INTO chapter_node(project_id,title,sort_order,status) VALUES(%s,%s,0,'generated')",(1,'原目录'))
        execute('INSERT INTO section_content(chapter_node_id,content) VALUES(%s,%s)',(old,'{"format":"md","body":"原正文必须保留"}'))
        before=(query('SELECT * FROM chapter_node'),query('SELECT * FROM section_content'))
        real=section_service._insert_nodes
        def fail_after_insert(pid,chapters,parent,lot):
            real(pid,[{'title':'写入一半的新目录'}],parent,lot)
            raise RuntimeError('模拟中途写库失败')
        with patch.object(section_service,'_insert_nodes',side_effect=fail_after_insert):
            try:section_service.replace_tree(1,[{'title':'新目录'}])
            except RuntimeError:pass
            else:raise AssertionError('预期失败却没有失败')
        assert before==(query('SELECT * FROM chapter_node'),query('SELECT * FROM section_content'))
        print('PASS: 实际DELETE和INSERT后抛错，目录与正文全部回滚；业务表未改动')
    finally:
        execute('DROP TEMPORARY TABLE IF EXISTS section_content')
        execute('DROP TEMPORARY TABLE IF EXISTS chapter_node')

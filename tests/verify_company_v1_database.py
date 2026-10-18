"""真实MySQL事务验证，使用连接级临时表隔离，不改真实表及正文。"""
import sys,json,tempfile,time,re
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.core.database import get_conn,execute,query_one
from app.services import company_materials as m,company_fill as f


def main():
    conn=get_conn()
    tables=('company_profile','company_document','company_fact','section_content','company_fill_revision')
    try:
        with conn.cursor() as cur:
            for table in tables:
                cur.execute('SHOW CREATE TABLE '+table)
                schema=cur.fetchone()['Create Table'].replace('CREATE TABLE','CREATE TEMPORARY TABLE',1)
                schema=re.sub(r',\n\s*CONSTRAINT[^\n]+','',schema)
                cur.execute(schema)
        execute("INSERT INTO company_document (id,name,sha256,path,status) VALUES (1,'合成验证资料',%s,'unused','extracted')",('a'*64,))
        execute("INSERT INTO company_fact (id,doc_id,category,subject,field_name,value,evidence,location,status) VALUES (1,1,'资质','示例公司','证书编号','ZX-4712','证书编号ZX-4712','第1页','pending')")
        payload={'format':'html','body':'<p>编号：[待补充：证书编号]</p>','used':{'pages':[3]}}
        execute('INSERT INTO section_content (chapter_node_id,content,ai_generated) VALUES (999,%s,1)',(json.dumps(payload,ensure_ascii=False),))
        marker=f.MARKER.search(payload['body'])
        item={'created':time.time(),'project':999,'node':999,'hash':f.digest(payload),'start':marker.start(),'end':marker.end(),'placeholder':marker.group(),'fact':1,'revision':1,'company_hash':f.digest({})}
        with tempfile.TemporaryDirectory() as tmp,patch.object(m,'storage',return_value=Path(tmp)),patch.object(f,'allowed'):
            token=f.sign(item)
            try:f.apply(999,999,[token]);raise AssertionError('候选事实被错误应用')
            except ValueError:pass
            assert m.decode(query_one('SELECT content FROM section_content WHERE chapter_node_id=999')['content'],{})==payload
            execute("UPDATE company_fact SET status='confirmed' WHERE id=1")
            result=f.apply(999,999,[token]);revision=result['revision_id']
            after=m.decode(query_one('SELECT content FROM section_content WHERE chapter_node_id=999')['content'],{})
            assert 'ZX-4712' in after['body'] and after['used']==payload['used']
            changed={**after,'body':after['body']+'<p>后续人工修改</p>'}
            execute('UPDATE section_content SET content=%s WHERE chapter_node_id=999',(json.dumps(changed,ensure_ascii=False),))
            try:f.undo(999,revision);raise AssertionError('撤回覆盖了人工修改')
            except ValueError:pass
            assert m.decode(query_one('SELECT content FROM section_content WHERE chapter_node_id=999')['content'],{})==changed
            execute('UPDATE section_content SET content=%s WHERE chapter_node_id=999',(json.dumps(after,ensure_ascii=False),))
            f.undo(999,revision)
            row=query_one('SELECT content,ai_generated FROM section_content WHERE chapter_node_id=999')
            assert m.decode(row['content'],{})==payload and row['ai_generated']==1
            assert query_one('SELECT status FROM company_fill_revision WHERE id=%s',(revision,))['status']=='undone'
            execute("UPDATE company_fact SET field_name='统一社会信用代码' WHERE id=1")
            execute("INSERT INTO company_fact (category,subject,field_name,value,evidence,location,status) VALUES ('公司介绍','示例公司','统一社会信用代码','冲突编号','冲突编号','手工','confirmed')")
            try:f.apply(999,999,[token]);raise AssertionError('冲突事实被错误应用')
            except ValueError:pass
        print('数据库隔离验证通过：候选不应用、应用回读、元数据保留、人工修改保护、安全撤回、冲突阻断')
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            for table in reversed(tables):cur.execute('DROP TEMPORARY TABLE IF EXISTS '+table)


if __name__=='__main__':main()

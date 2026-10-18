"""幂等创建公司资料表，备份并迁移本机旧档案；不修改项目正文。"""
import json
import sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.core.database import execute,query_one
from app.core.config import settings
from app.services.company_materials import storage


def main():
    sql=Path(__file__).with_name('company_v1.sql').read_text(encoding='utf-8')
    for statement in sql.split(';'):
        if statement.strip():execute(statement)
    if query_one('SELECT id FROM company_profile WHERE id=1'):
        print('公司资料结构已就绪，已有档案不覆盖');return
    legacy=Path(settings.uploads_dir)/'company_workspace'/'profile.json'
    value=json.loads(legacy.read_text(encoding='utf-8')) if legacy.exists() else {'name':'','positioning':'','entries':[]}
    if legacy.exists():
        backup=storage()/('legacy_profile_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
        backup.write_bytes(legacy.read_bytes())
    # 档案迁移标记最后写；迁移来源定位用于防止中断后重复。
    for i,item in enumerate(value.get('entries',[])):
        location=f'旧手工档案条目 {i+1}'
        if query_one('SELECT id FROM company_fact WHERE doc_id IS NULL AND location=%s',(location,)):continue
        execute('INSERT INTO company_fact (category,value,evidence,location,applicability,status) VALUES (%s,%s,%s,%s,%s,%s)',
                (item.get('category') or '其他',item.get('content') or '',item.get('content') or '',location,
                 '旧资料来源：'+item.get('source',''),'confirmed' if item.get('confirmed') is True else 'pending'))
    execute('INSERT INTO company_profile (id,content) VALUES (1,%s)',(json.dumps({'name':value.get('name',''),'positioning':value.get('positioning','')},ensure_ascii=False),))
    print('公司资料迁移完成；旧原件及项目正文未修改')


if __name__=='__main__':main()

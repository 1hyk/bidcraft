"""真实验证篇幅反馈与证据核验，仅改测试项目 6，保护项目 4。"""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from verify_full_proposal import digest
from app.core.config import settings
from app.core.database import query
from app.services.pagination_service import adjust_to_target
from app.services.proposal_quality import check, review


def main():
    before=digest(4)
    out=settings.uploads_dir/'_verification'/'priority_repairs'
    out.mkdir(parents=True,exist_ok=True)
    stamp=__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')
    backup=query('SELECT cn.id,cn.outline,sc.content FROM chapter_node cn LEFT JOIN section_content sc ON sc.chapter_node_id=cn.id WHERE cn.project_id=6 ORDER BY cn.id')
    (out/f'before_{stamp}.json').write_text(json.dumps(backup,ensure_ascii=False,default=str),encoding='utf-8')
    try:
        actual=adjust_to_target(0,6)
        result={'pagination':actual,'original_unchanged':digest(4)==before}
    except Exception as exc:
        result={'pagination_error':str(exc),'original_unchanged':digest(4)==before}
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True),flush=True)
    quality=check(6)
    (out/'quality.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
    print('quality',quality['pending_facts'],quality['pending_images'],len(quality['issues']),flush=True)
    evidence=review(6)
    (out/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print('evidence',[(r['requirement_id'],r['status'],r['evidence_verified']) for r in evidence['items']],flush=True)


if __name__=='__main__':main()

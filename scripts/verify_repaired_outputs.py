"""本轮修复的真实分页、历史素材规划与生图质量探针。"""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.core.config import settings
from app.services.pagination_service import measure
from app.services.proposal_quality import review,check
from app.services.image_retrieval import catalog
from app.agents.orchestrate_agent import generate_outline
from app.services.generated_images import generate


def main():
    out=settings.uploads_dir/'_verification'/'priority_repairs'
    result={'pagination':measure(6)}
    (out/'final_layout.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('measured_body_pages',result['pagination']['body_pages'],flush=True)
    assets=[a for a in catalog(6) if not a.get('generated') and a.get('kind')=='photo']
    if assets:
        sample=assets[0]
        outline=generate_outline('历史案例参考：'+sample['caption'],'',reqs_text='本节需要使用历史图片“'+sample['caption']+'”说明参考场景。必须保留历史原图名，明确是历史参考，不得作为本项目实拍或业绩证明。',domain='lowalt',project_id=6)
        (out/'historical_outline.json').write_text(json.dumps(outline,ensure_ascii=False,indent=2),encoding='utf-8')
        actual={a['asset_id']:a for a in catalog(6)}
        chosen=[n for n in outline.get('image_needs',[]) if n.get('source')=='history' and n.get('asset_id') in actual]
        result['historical_planning']={'chosen':chosen,'canonical_names':all(n['name']==actual[n['asset_id']]['caption'] for n in chosen),'matched':bool(chosen)}
        print('historical_planning',result['historical_planning'],flush=True)
    asset=generate(6,0,{'source':'illustration','name':'测试作业空间分区概念插画',
                        'purpose':'只用画面表达操作区、设备柜和独立通道，不展示任何指标、表格或文字',
                        'prompt':'等距三维插画，白底，蓝灰配色，几个操作台、设备柜、地面分区和独立通道。不要文字、标题、段落或数值。'},
                   '拟将操作台与设备柜分区布置，通道独立，保持人员通行空间。')
    result['illustration']={k:asset.get(k) for k in ('caption','url','asset_id')}
    print('illustration',result['illustration'],flush=True)
    evidence=review(6)
    (out/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    quality=check(6)
    (out/'quality.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
    result['evidence']={'requirements':len(evidence['items']),'verified':sum(x['evidence_verified'] for x in evidence['items']),
                        'max_references':max([len(x['evidence']) for x in evidence['items']] or [0])}
    (out/'final_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True),flush=True)


if __name__=='__main__':main()

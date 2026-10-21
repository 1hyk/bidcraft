"""图像风格契约、旧图提示与问题清单回归；不调用外部模型。"""
import io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from PIL import Image
from app.services.image_prompt import build_prompt
from app.services import generated_images as g

class Tests(unittest.TestCase):
 def test_physical_scene_does_not_inherit_old_nonphotographic_style(self):
  p=build_prompt({'name':'场景示意','prompt':'跑道与地面站，风格为工程技术示意图，非写实照片'},'qwen-image-2.0')
  self.assertIn('Photorealistic',p);self.assertIn('跑道与地面站',p)
  self.assertNotIn('非写实照片',p);self.assertNotIn('工程技术示意图',p)
  self.assertLessEqual(len(p.encode()),1200)
 def test_explicit_spatial_style_is_preserved(self):
  p=build_prompt({'name':'设备空间布置','visual_style':'spatial_illustration','prompt':'分区布局'},'qwen-image-2.0')
  self.assertIn('Technical spatial illustration',p);self.assertNotIn('Photorealistic',p)
 def test_legacy_auto_pass_is_still_pending_human_review(self):
  self.assertIn('未检查',g.review_reason({'generated':True,'validated':True,'review_pending':False}))
 def test_automatic_pass_does_not_claim_human_acceptance(self):
  self.assertIn('不等于人工验收',g.review_reason({'generated':True,'validated':True,'visual_check_version':g.VISUAL_CHECK_VERSION}))
  self.assertEqual(g.review_reason({'generated':False}),'')
 def test_visual_gate_requires_style_and_physical_plausibility(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'test.png';Image.new('RGB',(20,20),'blue').save(p)
   base={'relevant':True,'has_text':False,'style_matches':True,'physical_plausible':True}
   for field in ['style_matches','physical_plausible']:
    with patch('app.services.llm.llm.chat',return_value=json.dumps({**base,field:False})):
     with self.assertRaises(g.ImageGenerationError):g.validate_visual(p,{'name':'设备场景'})
   with patch('app.services.llm.llm.chat',return_value=json.dumps({'relevant':True,'has_text':False})):
    with self.assertRaises(g.ImageGenerationError):g.validate_visual(p,{'name':'设备场景'})
 def test_quality_reports_auto_passed_generated_picture(self):
  from app.services.proposal_quality import check
  from app.services.image_ownership import ProjectImages
  asset_id='IMG-'+'1'*24
  body='[图片占位：场景示意|素材ID='+asset_id+']'
  asset={'asset_id':asset_id,'generated':True,'validated':True,'review_pending':False,'caption':'场景示意'}
  row={'id':1,'title':'方案','body':body,'payload':{'body':body},'has_children':False}
  with patch('app.services.proposal_quality.chapters',return_value=iter([row])),patch('app.services.proposal_quality.query',return_value=[]),patch('app.services.image_ownership.project_image_index_flat',return_value=ProjectImages()),patch.object(g,'catalog',return_value=[asset]),patch('app.services.pagination_service.view',return_value={'fresh':True}),patch('app.services.issue_resolution.annotate',side_effect=lambda p,i,c:(i,[],[])):
   result=check(1)
  self.assertTrue(any(i['kind']=='image_review' and i.get('asset_id')==asset_id for i in result['issues']))

if __name__=='__main__':unittest.main()

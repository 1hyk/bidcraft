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
 def test_real_object_feedback_overrides_old_spatial_label(self):
  p=build_prompt({'name':'场景示意图','visual_style':'spatial_illustration','_feedback':'实物图',
                  'prompt':'画面风格为技术蓝图风格，非实景照片质感'},'qwen-image-2.0')
  self.assertIn('Photorealistic',p);self.assertNotIn('Technical spatial illustration',p)
  self.assertNotIn('技术蓝图风格',p);self.assertNotIn('非实景照片',p)
 def test_spatial_feedback_overrides_old_photo_label(self):
  p=build_prompt({'name':'设备布置','visual_style':'photorealistic','_feedback':'改成示意图',
                  'prompt':'写实摄影风格的分区布局'},'qwen-image-2.0')
  self.assertIn('Technical spatial illustration',p);self.assertNotIn('Photorealistic',p)
  self.assertNotIn('写实摄影',p)
 def test_spatial_layout_content_cannot_be_drawn_as_photo(self):
  p=build_prompt({'name':'试验资源布局图','data_structure':'spatial_layout',
                  'visual_style':'photorealistic','_feedback':'要实物图'},'qwen-image-2.0')
  self.assertIn('Technical spatial illustration',p);self.assertNotIn('Photorealistic',p)
 def test_photorealistic_prompt_forbids_schematic_overlays(self):
  p=build_prompt({'name':'场景','prompt':'雷达与无人机之间用虚线连接，箭头指向目标，并画出监视覆盖范围'},'qwen-image-2.0')
  self.assertIn('No schematic overlays',p)
  for word in ['虚线连接','箭头','覆盖范围']:
   self.assertNotIn(word,p)
 def test_photorealistic_prompt_drops_connector_beam(self):
  p=build_prompt({'name':'场景','prompt':'一道橙色激光束从地面雷达连接至无人机，直观展示信号锁定关系。周边有草坪和道路。'},'qwen-image-2.0')
  self.assertNotIn('激光束',p);self.assertNotIn('直观展示',p)
  self.assertIn('周边有草坪和道路',p)
 def test_spatial_prompt_keeps_diagram_elements_and_drops_photo_wording(self):
  p=build_prompt({'name':'空域分区与航线规划示意图','visual_style':'spatial_illustration',
                  'prompt':'写实航拍视角，严禁出现任何虚线、箭头、网格覆盖层或文字标签，展示空域分区和航线，不追求照片质感。'},'qwen-image-2.0')
  self.assertIn('Technical spatial illustration',p)
  self.assertNotIn('写实航拍',p);self.assertNotIn('严禁出现',p);self.assertNotIn('不追求',p)
  self.assertNotIn('，，',p);self.assertNotIn('。。',p)
  self.assertIn('空域分区和航线',p)
 def test_spatial_edit_prompt_allows_diagram_lines(self):
  p=g.build_edit_prompt({'name':'空域分区与航线规划示意图','visual_style':'spatial_illustration',
                         '_feedback':'航线再清楚一些'})
  self.assertIn('空间示意图',p);self.assertIn('航线',p)
  self.assertNotIn('不要添加虚线',p)
  q=g.build_edit_prompt({'name':'设备场景','visual_style':'photorealistic','_feedback':'去掉连线'})
  self.assertIn('不要添加虚线',q)
 def test_redesign_instruction_follows_target_style(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'test.png';Image.new('RGB',(20,20),'blue').save(p)
   texts=[]
   def fake(messages,**kwargs):
    texts.append(messages[0]['content'][0]['text']);return '{"prompt":"俯视平面图，分区色块与航线"}'
   with patch('app.services.llm.llm.chat',side_effect=fake):
    g.describe_redesign(p,'改成示意图','空域分区','用途','spatial_illustration')
    g.describe_redesign(p,'去掉连线','设备场景','用途','photorealistic')
   self.assertIn('空间示意图',texts[0]);self.assertNotIn('写实场景只描述',texts[0])
   self.assertIn('写实场景只描述',texts[1])
 def test_spatial_style_follows_information_carrier_not_name_keywords(self):
  from app.services.image_planning import normalize_outline
  def need(name,structure='spatial_layout',style='photorealistic'):
   return {'image_needs':[{'name':name,'source':'illustration','data_structure':structure,'visual_style':style,'allow_generated':True,'prompt':'x'}]}
  # 图名不含任何行业关键词，仍按“信息靠图示符号承载”走平面示意。
  self.assertEqual(normalize_outline(need('试验资源布局图'))['image_needs'][0]['visual_style'],'spatial_illustration')
  self.assertEqual(normalize_outline(need('分区与走向说明'))['image_needs'][0]['visual_style'],'spatial_illustration')
  # 信息在真实物体外观里的场景默认写实。
  self.assertEqual(normalize_outline(need('设备部署场景','physical_scene'))['image_needs'][0]['visual_style'],'photorealistic')
  # 物理场景允许人工选择示意表达；旧记录只保留原显式风格，不做语义猜测。
  self.assertEqual(normalize_outline(need('设备部署场景','physical_scene','spatial_illustration'))['image_needs'][0]['visual_style'],'spatial_illustration')
  self.assertEqual(normalize_outline(need('旧场景','spatial_scene','photorealistic'))['image_needs'][0]['visual_style'],'photorealistic')

 def test_regeneration_style_uses_semantic_class_not_name(self):
  from app.services.image_planning import regeneration_style
  layout={'name':'试验资源布局图','data_structure':'spatial_layout'}
  scene={'name':'设备场景','data_structure':'physical_scene'}
  legacy={'name':'旧图','data_structure':'spatial_scene'}
  # 旧素材被标成写实也要纠回平面示意；明确要实物图时给出可执行的替代方案。
  self.assertEqual(regeneration_style(layout,'','photorealistic'),'spatial_illustration')
  with self.assertRaisesRegex(ValueError,'图示符号'):
   regeneration_style(layout,'photorealistic','spatial_illustration')
  # 物理场景可以按用户要求改成示意图，反之保持写实。
  self.assertEqual(regeneration_style(scene,'spatial_illustration',''),'spatial_illustration')
  self.assertEqual(regeneration_style(scene,'','photorealistic'),'photorealistic')
  # 没有语义分类的旧记录尊重用户反馈。
  self.assertEqual(regeneration_style(legacy,'photorealistic','spatial_illustration'),'photorealistic')
 def test_negated_real_feedback_means_spatial(self):
  from app.services.image_prompt import style_from_feedback
  self.assertEqual(style_from_feedback('不要写实，改用示意图'),'spatial_illustration')
  self.assertEqual(style_from_feedback('不要照片，要实物图'),'photorealistic')
  self.assertIsNone(style_from_feedback('把无人机往左移'))
 def test_legacy_auto_pass_is_still_pending_human_review(self):
  self.assertIn('未检查',g.review_reason({'generated':True,'validated':True,'review_pending':False}))
 def test_automatic_pass_does_not_claim_human_acceptance(self):
  self.assertIn('不等于人工验收',g.review_reason({'generated':True,'validated':True,'visual_check_version':g.VISUAL_CHECK_VERSION}))
  self.assertEqual(g.review_reason({'generated':False}),'')
 def test_visual_gate_requires_style_and_physical_plausibility(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'test.png';Image.new('RGB',(20,20),'blue').save(p)
   base={'relevant':True,'has_text':False,'style_matches':True,'physical_plausible':True,'has_schematic_overlay':False}
   for field in ['style_matches','physical_plausible','has_schematic_overlay']:
    with patch('app.services.llm.llm.chat',return_value=json.dumps({**base,field:False if field!='has_schematic_overlay' else True})):
     with self.assertRaises(g.ImageGenerationError):g.validate_visual(p,{'name':'设备场景'})
   with patch('app.services.llm.llm.chat',return_value=json.dumps({'relevant':True,'has_text':False})):
    with self.assertRaises(g.ImageGenerationError):g.validate_visual(p,{'name':'设备场景'})
   # 示意图允许分区色块和航线线条，不能拿写实图的“无叠加”标准误杀。
   with patch('app.services.llm.llm.chat',return_value=json.dumps({**base,'has_schematic_overlay':True})):
    g.validate_visual(p,{'name':'空域分区与航线规划示意图','visual_style':'spatial_illustration'})
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

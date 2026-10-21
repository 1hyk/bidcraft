import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.template_completion import has_named_table,remove_redundant_table_markers
class Tests(unittest.TestCase):
 def test_named_table_removes_only_its_marker(self):
  body='方案清单表\n\n| 编号 | 内容 |\n| :--- | --- |\n| 1 | 检查 |\n\n[图片占位：方案清单表]\n[图片占位：其他清单表]'
  self.assertTrue(has_named_table(body,'方案清单表'))
  result=remove_redundant_table_markers(body)
  self.assertNotIn('[图片占位：方案清单表]',result)
  self.assertIn('[图片占位：其他清单表]',result)
  self.assertIn('| 1 | 检查 |',result)
 def test_unrelated_table_is_not_proof(self):
  body='## 性能表\n| A | B |\n| --- | --- |\n| 1 | 2 |\n[图片占位：人员清单表]'
  self.assertEqual(remove_redundant_table_markers(body),body)
 def test_real_material_and_bound_images_untouched(self):
  for name in ['清单表截图','签字清单表原件','清单表|素材ID=IMG-123']:
   body=f'{name}\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n[图片占位：{name}]'
   self.assertEqual(remove_redundant_table_markers(body),body)
if __name__=='__main__':unittest.main()

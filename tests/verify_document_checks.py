import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.document_checks import pending_markers,resolve_requirement_refs

class Checks(unittest.TestCase):
    def test_pending_markers_include_parentheses_but_not_discussion(self):
        self.assertEqual(pending_markers('（待确认） (待补充：型号) 【待确认：人员】 [待补充：参数]'),
                         ['（待确认）','(待补充：型号)','【待确认：人员】','[待补充：参数]'])
        self.assertEqual(pending_markers('（待确认事项的处理流程）以及待确认后的执行安排'),[])

    def test_internal_ids_resolve_only_to_current_sources(self):
        rows=[{'id':71,'title':'性能要求','source_location':'附件一 第三条'},
              {'id':72,'title':'验收要求','source_location':''}]
        text='依据招标要求#71及#72实施；招标要求#99。`招标要求#71`'
        result=resolve_requirement_refs(text,rows)
        self.assertIn('采购文件“附件一 第三条”及采购文件“验收要求”',result)
        self.assertIn('招标要求#99',result)
        self.assertIn('`招标要求#71`',result)

if __name__=='__main__':unittest.main()

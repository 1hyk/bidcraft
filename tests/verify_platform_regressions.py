import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.mermaid_source import normalize
from app.services.image_planning import normalize_outline
from app.services import diagram_diagnostics as diag

class Regression(unittest.TestCase):
    def test_blank_signature_does_not_call_external_model(self):
        from app.services.template_completion import complete
        with patch('app.services.llm.llm.chat',side_effect=AssertionError('external call')):
            name='验收结论签署页版式示意'
            out=complete('[图片占位：'+name+']',table_names=[name])
            self.assertNotIn('[图片占位',out)
            self.assertIn('| 签署日期 | （待填写） |',out)
    def test_final_customer_voice_cleanup_preserves_hierarchy(self):
        from app.services.deliverable_policy import sanitize_text
        body='```mermaid\nmindmap\n  root((根))\n    分组\n      子项\n```'
        self.assertEqual(sanitize_text(body),body)
    def test_only_explicit_report_samples_blank(self):
        from app.services.template_completion import blank_report_examples
        sample='**测试结果汇总表样例**\n\n| 场景 | 实测结果 |\n|---|---|\n| A | 99.8% |\n'
        self.assertNotIn('99.8%',blank_report_examples(sample))
        actual=sample.replace('样例','')
        self.assertEqual(blank_report_examples(actual),actual)
        plan=sample.replace('实测结果','拟投入人数')
        self.assertEqual(blank_report_examples(plan),plan)
    def test_flat_mindmap_and_valid_tree(self):
        self.assertEqual(normalize('mindmap\n root((根))\n 叶一\n 叶二'), 'mindmap\nroot((根))\n  叶一\n  叶二')
        valid='mindmap\n root((根))\n   分组\n     子项'
        self.assertEqual(normalize(valid),valid)
        graph='flowchart TD\n A-->B'
        self.assertEqual(normalize(graph),graph)
    def test_form_and_real_signed_scan_differ(self):
        needs=[{'name':'验收结论签署页版式示意','content_origin':'proposal_design','data_structure':'physical_scene','source':'material'},
               {'name':'已签署验收书扫描件','content_origin':'external_evidence','source':'material'}]
        out=normalize_outline({'image_needs':needs},[])
        self.assertEqual(out['table_needs'][0]['name'],needs[0]['name'])
        self.assertEqual(out['image_needs'][0]['name'],needs[1]['name'])
    def test_comparison_table_does_not_become_photo(self):
        need={'name':'服务时限对照表','content_origin':'external_evidence','data_structure':'comparison','source':'material'}
        self.assertEqual(len(normalize_outline({'image_needs':[need]},[])['table_needs']),1)
    def test_export_failure_only_current_source_and_clears_after_success(self):
        code='flowchart TD\n A-->B'
        payload={'format':'md','body':'```mermaid\n'+code+'\n```'}
        with tempfile.TemporaryDirectory() as folder,patch.object(diag.settings,'uploads_dir',Path(folder)):
            diag.save(999,{1:payload},{})
            self.assertEqual(len(diag.failures(999,1,payload)),1)
            changed={'format':'html','body':'<div data-code="flowchart TD&#10; A--&gt;C"></div>'}
            self.assertFalse(diag.failures(999,1,changed))
            diag.save(999,{1:payload},{code:Path('cached.png')})
            self.assertFalse(diag.failures(999,1,payload))

if __name__=='__main__':unittest.main()

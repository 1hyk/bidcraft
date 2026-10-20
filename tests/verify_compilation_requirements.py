"""回归真实失败形状：无 toc_hint、有评分条目、父章约束、同名子节。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services import compilation_requirements as contract
from app.services import section_service
from app.agents import compile_graph


class ContractTests(unittest.TestCase):
    def rows(self):
        return [dict(id=i, category='评分办法', title=t, suggested_chapter=t,
                     content='必须覆盖开发、初验、联调、试运行、终验、培训六阶段',
                     source_file='招标.docx', source_location='评分表')
                for i,t in enumerate(['技术指标响应情况','工程化交付方案','测试验收准备方案',
                                       '项目实施方案','项目售后服务与保障能力'],1)]

    def test_no_toc_still_has_ordered_required_chapters(self):
        self.assertEqual([x['title'] for x in contract.scope_chapters(None,self.rows(),'技术方案')],
                         [r['title'] for r in self.rows()])

    def test_explicit_directory_takes_priority_without_mutation(self):
        toc={'chapters':[{'title':'四、技术方案','children':[{'title':'实施安排'}]}]}
        result=contract.scope_chapters(toc,self.rows(),'技术方案')
        self.assertEqual(result,[{'title':'实施安排'}])
        result.clear()
        self.assertTrue(toc['chapters'][0]['children'])

    def test_source_and_full_constraint_inherited_by_child(self):
        text=contract.requirements_text(self.rows(),['进度安排','技术方案','项目实施方案'])
        for word in ['初验','联调','试运行','终验','培训','招标.docx','评分表','要求#4']:
            self.assertIn(word,text)
        self.assertNotIn('要求#2',text)

    def test_graph_passes_constraints_to_outline(self):
        state=dict(project_id=1,facts=[],toc={'chapters':[{'title':'技术方案','children':[
            {'title':'项目实施方案','children':[{'title':'进度安排'}]}]}]})
        with patch('app.core.domains.domain_of_project',return_value='lowalt'), \
             patch('app.services.compilation_requirements.load_requirements',return_value=self.rows()), \
             patch('app.services.matching.load_page_summaries',return_value=[]), \
             patch('app.services.matching.match_pages',return_value=[]), \
             patch('app.services.experience_service.match_experiences',return_value=[]), \
             patch('app.services.lot_profile_service.lot_brief',return_value=''), \
             patch.object(compile_graph,'generate_outline',return_value={}) as generate, \
             patch.object(section_service,'save_outline',return_value=True):
            compile_graph.node_generate_outlines(state)
        call=next(c for c in generate.call_args_list if c.kwargs['chapter_title']=='进度安排')
        self.assertIn('初验',call.kwargs['reqs_text'])

    def test_same_title_saved_to_correct_branch(self):
        rows=[dict(id=1,title='技术方案',parent_id=None),dict(id=2,title='方案甲',parent_id=1),
              dict(id=3,title='方案乙',parent_id=1),dict(id=4,title='质量控制',parent_id=2),
              dict(id=5,title='质量控制',parent_id=3)]
        with patch.object(section_service,'query',return_value=rows), \
             patch.object(section_service,'execute') as write:
            self.assertTrue(section_service.save_outline(1,'质量控制',{'_path':['技术方案','方案乙']}))
        self.assertEqual(write.call_args.args[1][-1],5)

    def test_explicit_phase_list_must_be_covered_before_saving(self):
        row=dict(id=4,title='项目实施方案',suggested_chapter='项目实施方案',
                 content='方案内容必须覆盖开发、初验、联调、试运行、终验、培训方案6个阶段。')
        toc={'chapters':[{'title':'项目实施方案','children':[
            {'title':s+'阶段实施方案'} for s in ['开发','初验','联调','试运行','终验','培训']]}]}
        contract.validate_stage_coverage(toc,[row])
        toc['chapters'][0]['children'].pop(1)
        with self.assertRaisesRegex(ValueError,'初验.*原目录未替换'):
            contract.validate_stage_coverage(toc,[row])

    def test_replace_failure_rolls_back_old_tree_and_body(self):
        from unittest.mock import Mock
        conn=Mock()
        with patch('app.core.database.get_conn',return_value=conn), \
             patch.object(section_service,'clear_tree'), \
             patch.object(section_service,'save_tree',side_effect=RuntimeError('写入失败')):
            with self.assertRaisesRegex(RuntimeError,'写入失败'):
                section_service.replace_tree(1,[{'title':'新目录'}])
        conn.begin.assert_called_once()
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()


if __name__=='__main__':
    unittest.main()

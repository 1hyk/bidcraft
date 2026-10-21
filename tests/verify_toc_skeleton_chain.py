"""目录以当前招标为依据；不调用外部模型、不修改业务数据。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.agents import orchestrate_agent as agent
from app.agents.compile_graph import _chapter_skeleton
from app.core.config import settings
from app.core.domains import chapters_of
from app.agents.requirement_agent import suggested_chapters

class TenderTocTests(unittest.TestCase):
    def test_sample_defaults_removed_from_both_paths(self):
        self.assertEqual(chapters_of('lowalt'),())
        self.assertEqual(suggested_chapters('lowalt'),'')
        self.assertIsNone(_chapter_skeleton('lowalt',None))
        self.assertEqual(agent._chapters_block(['旧样本章']), '')

    def test_no_basis_fails_before_llm(self):
        with patch.object(agent.llm,'chat') as chat:
            with self.assertRaisesRegex(ValueError,'招标依据不足'):
                agent.generate_toc_titles('低空项目描述',domain='lowalt',chapters=['旧样本章'])
            chat.assert_not_called()

    def test_empty_basis_cannot_publish_partial_or_replace_existing_tree(self):
        from app.agents.compile_graph import node_generate_toc
        with patch('app.core.domains.domain_of_project',return_value='lowalt'),patch('app.services.compilation_requirements.load_requirements',return_value=[]),patch('app.services.compile_service.save_partial_toc') as partial,patch('app.services.section_service.replace_tree') as replace:
            with self.assertRaisesRegex(ValueError,'招标依据不足'):
                node_generate_toc({'project_id':21,'tender_toc':None,'tender_requirements':'','project_info':'仅有项目描述','professions':[]})
            partial.assert_not_called()
            replace.assert_not_called()

    def test_changed_requirements_reach_prompt_without_domain_defaults(self):
        with patch.object(settings,'generation_root_title',''),patch.object(agent.llm,'chat',return_value='{"chapters":[{"title":"设备巡检方案"}]}') as chat:
            result=agent.generate_toc_titles('项目',tender_requirements='按季度巡检地面设备并培训',chapters=['不应注入的旧章'],domain='lowalt')
            self.assertEqual(result[0]['title'],'设备巡检方案')
            prompt=chat.call_args.args[0][1]['content']
            self.assertIn('按季度巡检地面设备并培训',prompt)
            self.assertNotIn('不应注入的旧章',prompt)
            self.assertNotIn('工程化交付方案',prompt)

    def test_explicit_children_override_history_without_model(self):
        toc={'chapters':[{'title':'技术方案','children':[{'title':'现场巡检'},{'title':'培训交付'}]}]}
        with patch.object(agent.llm,'chat') as chat:
            result=agent.generate_toc_children('技术方案','项目',[],tender_toc=toc,skeleton=[{'name':'旧样本安排','pages':99}],domain='lowalt')
            self.assertEqual([c['title'] for c in result],['现场巡检','培训交付'])
            chat.assert_not_called()

    def test_existing_five_chapters_allowed_when_current_tender_requires_them(self):
        names=['技术指标响应情况','工程化交付方案','测试验收准备方案','项目实施方案','项目售后服务与保障能力']
        toc={'chapters':[{'title':'技术方案','children':[{'title':n} for n in names]}]}
        with patch.object(settings,'generation_root_title','技术方案'),patch.object(agent.llm,'chat') as chat:
            self.assertEqual(agent.generate_toc_titles('项目',tender_toc=toc),[{'title':'技术方案'}])
            children=agent.generate_toc_children('技术方案','项目',[],tender_toc=toc,domain='lowalt')
            self.assertEqual([c['title'] for c in children],names)
            chat.assert_not_called()

if __name__=='__main__':unittest.main()

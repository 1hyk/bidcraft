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
    def test_price_method_uses_technical_requirements_not_price_chapter(self):
        rows=[dict(id=1,category='评分办法',title='最低评审价法',content='满足实质要求后按价格排序'),
              dict(id=2,category='技术要求',title='配件质量',suggested_chapter='配件管理方案'),
              dict(id=3,category='技术要求',title='应急救援',suggested_chapter='应急响应方案'),
              dict(id=4,category='技术要求',title='投标报价',suggested_chapter='报价方案')]
        result=contract.scope_chapters(None,rows,'技术方案')
        self.assertEqual([r['title'] for r in result],['配件管理方案','应急响应方案'])
        toc={'chapters':[{'title':'技术方案','children':result}]}
        contract.validate_scope_coverage(toc,rows,None,'技术方案')
        toc['chapters'][0]['children']=[{'title':'最低评审价法'}]
        with self.assertRaisesRegex(ValueError,'目录缺少.*原目录未替换'):
            contract.validate_scope_coverage(toc,rows,None,'技术方案')

    def test_price_score_is_removed_but_true_technical_scores_keep_priority(self):
        rows=[dict(category='评分办法',title='最低评审价法'),*self.rows(),
              dict(category='评分办法',title='价格评分',suggested_chapter='价格优势'),
              dict(category='技术要求',title='零配件质量要求')]
        self.assertEqual([r['title'] for r in contract.scope_chapters(None,rows,'技术方案')],
                         [r['title'] for r in self.rows()])

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

    def test_explicit_directory_keeps_whole_scope_scoring_item(self):
        children = ['工程化交付方案','测试验收准备方案','项目实施方案','项目售后服务与保障能力']
        toc={'chapters':[{'title':'技术方案',
                          'children':[{'title':t} for t in children]}]}
        rows=[dict(id=639,category='评分办法',title='技术指标响应情况考察',
                   suggested_chapter='技术方案',content='▲项每偏离一项扣4分。'),
              *[dict(id=i,category='评分办法',title=t,suggested_chapter=t)
                for i,t in enumerate(children,640)]]
        result=contract.scope_chapters(toc,rows,'技术方案')
        self.assertEqual([c['title'] for c in result],
                         ['技术指标响应情况考察',*children])
        # 规定子章顺序不变，整册评分项只补一次；覆盖校验必须能通过。
        contract.validate_scope_coverage(
            {'chapters':[{'title':'技术方案','children':result}]},rows,toc,'技术方案')

    def test_whole_scope_scoring_item_with_blank_mapping_survives_explicit_directory(self):
        children = ['工程化交付方案','测试验收准备方案']
        toc={'chapters':[{'title':'技术方案','children':[{'title':t} for t in children]}]}
        rows=[dict(id=654,category='评分办法',title='技术指标响应情况考察',
                   suggested_chapter='',
                   content='对采购文件附件1条款响应情况进行考察，▲项每偏离一项扣4分。'),
              *[dict(id=i,category='评分办法',title=t,suggested_chapter=t)
                for i,t in enumerate(children,655)]]
        result=contract.scope_chapters(toc,rows,'技术方案')
        self.assertEqual([c['title'] for c in result],
                         ['技术指标响应情况考察',*children])
        contract.validate_scope_coverage(
            {'chapters':[{'title':'技术方案','children':result}]},rows,toc,'技术方案')

    def test_unrelated_blank_scoring_item_is_not_forced_into_directory(self):
        toc={'chapters':[{'title':'技术方案','children':[{'title':'工程化交付方案'}]}]}
        rows=[dict(id=1,category='评分办法',title='方案完整性',suggested_chapter='',
                   content='技术方案完整、可行。'),
              dict(id=2,category='评分办法',title='工程化交付方案',
                   suggested_chapter='工程化交付方案')]
        result=contract.scope_chapters(toc,rows,'技术方案')
        self.assertEqual([c['title'] for c in result],['工程化交付方案'])

    def test_response_children_accept_answer_wording_and_avoid_duplicate_suffix(self):
        rows=[dict(id=639,category='评分办法',title='技术指标响应情况考察',
                   suggested_chapter='',
                   content='对附件1条款逐条应答。'),
              dict(id=625,category='技术要求',title='售后响应',
                   suggested_chapter='技术方案',source_location='技术要求',
                   content='附件1 2.5：售后响应时限。')]
        children=contract.required_response_children('技术指标响应情况考察',rows,'技术方案')
        self.assertEqual([c['title'] for c in children],['售后响应'])

    def test_whole_scope_scoring_item_uses_own_title_without_toc(self):
        rows=[dict(id=639,category='评分办法',title='技术指标响应情况考察',
                   suggested_chapter='技术方案',content='▲项每偏离一项扣4分。')]
        self.assertEqual([c['title'] for c in contract.scope_chapters(None,rows,'技术方案')],
                         ['技术指标响应情况考察'])

    def test_response_scoring_item_expands_to_scope_technical_clauses(self):
        rows=[dict(id=639,category='评分办法',title='技术指标响应情况考察',
                   suggested_chapter='技术方案',
                   content='对附件1条款响应情况进行考察，▲项每偏离一项扣4分。'),
              dict(id=625,category='技术要求',title='局点实飞演示场景支撑',
                   suggested_chapter='技术方案',source_location='附件1 2.1.1 功能要求',
                   content='1.实际飞行飞机接入演示平台。'),
              dict(id=626,category='技术要求',title='监视能力全量验证',
                   suggested_chapter='技术方案',source_location='附件一 2.1.1 功能要求',
                   content='1.监视功能全量验证。'),
              dict(id=632,category='技术要求',title='工期要求',
                   suggested_chapter='项目实施方案',source_location='采购公告-2.5',
                   content='30日历天交货。')]
        children=contract.required_response_children('技术指标响应情况考察',rows,'技术方案')
        self.assertEqual([c['title'] for c in children],
                         ['局点实飞演示场景支撑响应','监视能力全量验证响应'])
        # 普通方案章节不能被误判成条款响应章。
        self.assertEqual(contract.required_response_children('项目实施方案',rows,'技术方案'),[])

    def test_response_children_exclude_commercial_clauses(self):
        rows=[dict(id=639,category='评分办法',title='技术指标响应情况考察',
                   suggested_chapter='技术方案',
                   content='对附件1条款响应情况进行考察，▲项每偏离一项扣4分。'),
              dict(id=625,category='技术要求',title='监视能力全量验证',
                   suggested_chapter='技术方案',source_location='附件1 2.1.1 功能要求',
                   content='1.监视功能全量验证。'),
              dict(id=630,category='技术要求',title='最高限价',
                   suggested_chapter='技术方案',source_location='采购公告-2.7',
                   content='最高限价120万元。'),
              dict(id=631,category='技术要求',title='付款方式',
                   suggested_chapter='技术方案',source_location='采购公告-2.8',
                   content='验收后付款。')]
        children=contract.required_response_children('技术指标响应情况考察',rows,'技术方案')
        self.assertEqual([c['title'] for c in children],['监视能力全量验证响应'])

    def test_response_children_skip_clauses_covered_by_other_chapters(self):
        rows=[dict(id=639,category='评分办法',title='技术指标响应情况考察',
                   suggested_chapter='技术方案',
                   content='对附件1条款响应情况进行考察，▲项每偏离一项扣4分。'),
              dict(id=640,category='评分办法',title='工程化交付方案',
                   suggested_chapter='工程化交付方案'),
              dict(id=643,category='评分办法',title='项目售后服务与保障能力',
                   suggested_chapter='售后服务与保障方案'),
              dict(id=625,category='技术要求',title='局点实飞演示场景支撑',
                   suggested_chapter='功能及性能要求',
                   source_location='附件1 2.1.1 功能要求',content='实飞演示。'),
              dict(id=634,category='技术要求',title='交付物清单',
                   suggested_chapter='工程化交付方案',
                   source_location='附件1 2.2 验收交付物',content='交付清单。'),
              dict(id=673,category='技术要求',title='售后服务响应机制',
                   suggested_chapter='售后服务与保障方案',
                   source_location='附件1 2.5 售后服务',content='响应机制。'),
              dict(id=635,category='技术要求',title='工期要求',
                   suggested_chapter='',
                   source_location='采购公告-2.5',content='30日历天。')]
        children=contract.required_response_children('技术指标响应情况考察',rows,'技术方案')
        # 未映射到规定章节的功能条款要展开；已由“工程化交付方案”承载的交付物不重复；
        # 采购公告里的工期条款不属于附件1响应范围。
        self.assertEqual([c['title'] for c in children],['局点实飞演示场景支撑响应'])

    def test_scope_named_scoring_item_is_not_nested_chapter(self):
        toc={'chapters':[{'title':'技术方案','children':[{'title':'工程化交付方案'}]}]}
        rows=[dict(id=1,category='评分办法',title='技术方案',
                   suggested_chapter='技术方案',content='技术方案完整可行。')]
        result=contract.scope_chapters(toc,rows,'技术方案')
        self.assertEqual([c['title'] for c in result],['工程化交付方案'])

    def test_source_and_full_constraint_inherited_by_child(self):
        text=contract.requirements_text(self.rows(),['进度安排','技术方案','项目实施方案'])
        for word in ['初验','联调','试运行','终验','培训','招标.docx','评分表','项目实施方案']:
            self.assertIn(word,text)
        # 内部要求编号只用于系统关联，不能交给模型抄进正文。
        self.assertNotIn('要求#',text)

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

    def test_explicit_stage_structure_comes_from_current_tender(self):
        rows=[dict(id=90,title='实施安排',suggested_chapter='',content='应包括准备、执行、移交3个阶段。')]
        children=contract.required_stage_children('实施安排',rows)
        self.assertEqual([n['title'] for n in children],['准备','执行','移交'])
        contract.validate_stage_coverage({'chapters':[{'title':'实施安排','children':children}]},rows)
        self.assertEqual(contract.required_stage_children('其他方案',rows),[])
        self.assertEqual(contract.required_stage_children('实施安排',[dict(id=91,title='实施安排',content='根据实际情况组织实施')]),[])


if __name__=='__main__':
    unittest.main()

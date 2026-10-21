"""业务摘要行为测试：跨业务、证据、稳定读取、重提取失败、项目/标段隔离。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services import project_brief as brief
from app.services.project_classification import validate_types


def source(project=19, lot=None, content='提供车辆维修和保养服务，维修对象为车辆。'):
    return {'project_id':project,'lot_id':lot,'lot_code':str(lot or ''),'rows':[
        {'id':1,'file_id':16,'file_name':'招标.docx','sec_path':'采购内容','content':content}]}


class BriefTests(unittest.TestCase):
    def test_review_rules_are_not_reported_as_missing_business_chapters(self):
        from app.services.project_understanding import requirement_group, chapter_candidates
        nodes=[{'id':703,'title':'技术指标响应情况考察'},{'id':720,'title':'项目实施方案'}]
        general={'category':'废标风险','title':'实质性条款不满足','content':'响应单位须满足★项。'}
        self.assertEqual(requirement_group(general),'bid_notices')
        self.assertEqual(chapter_candidates(general,nodes),[])
        review={'category':'废标风险','title':'关键指标偏离过多','content':'▲项偏离两项及以上的，技术指标响应情况考察项得0分。'}
        self.assertEqual(chapter_candidates(review,nodes),[nodes[0]])
        review['content']='关键技术指标不满足，影响评审。'
        self.assertEqual(chapter_candidates(review,nodes),[],'无明确依据时不能猜测关联成功')

    def test_understanding_does_not_ask_for_chapters_for_submission_rules(self):
        from app.services.project_understanding import requirement_group
        for title,category,expected in [
            ('最低评审价法','评分办法','bid_notices'),
            ('恶意低价竞争判定','废标风险','bid_notices'),
            ('串通响应情形','废标风险','bid_notices'),
            ('虚假响应情形','废标风险','bid_notices'),
            ('实质性条款响应无效','废标风险','bid_notices'),
            ('响应文件份数与形式','编制要求','document_rules'),
            ('暗标字体与页数','编制要求','document_rules'),
            ('设备配置要求','技术要求','technical'),
            ('应急响应时限不满足','废标风险','technical'),
            ('技术方案评分','评分办法','technical')]:
            with self.subTest(title=title):
                self.assertEqual(requirement_group({'title':title,'category':category}),expected)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=patch('app.services.project_understanding.root',return_value=Path(self.temp.name))
        self.root.start()

    def tearDown(self):
        self.root.stop()
        self.temp.cleanup()

    def test_actual_services_not_limited_by_industry_options(self):
        self.assertEqual(validate_types(['车辆维修和保养服务'],'manufacturing'),['车辆维修和保养服务'])

    def test_cross_business_extraction_and_citations(self):
        for service,obj,category,text in [('车辆维修和保养服务','车辆','维修保养','提供车辆维修和保养服务，维修对象为车辆。'),
                                 ('软件测试','支付平台','测试验证','对支付平台提供软件测试。'),
                                 ('实飞测试验证','无人机','测试验证','对无人机开展实飞测试验证。')]:
            payload={'services':[{'name':service,'category':category,'source_id':1,'excerpt':text}],
                     'objects':[{'name':obj,'source_id':1,'excerpt':text}]}
            with self.subTest(service=service),patch('app.services.llm.llm.chat',return_value=json.dumps(payload)):
                out=brief.extract(source(content=text))
                self.assertEqual(out['project_types'],[category])
                self.assertEqual(out['service_content'],service)
                self.assertEqual(out['technical_objects'],[obj])
                self.assertEqual(out['object_evidence'][0]['source_location'],'采购内容')

    def test_task_titles_are_not_work_objects_across_businesses(self):
        for task in ['支付平台测试','车辆维修','监视能力全量验证','业务系统开发']:
            self.assertTrue(brief.object_repeats_service(task,[]))
        for obj in ['支付平台','车辆','监视功能','开发平台','测试设备']:
            self.assertFalse(brief.object_repeats_service(obj,[]))

    def test_bad_object_does_not_discard_verified_service_or_invent_object(self):
        text='开展实飞测试验证外协。'
        payload={'services':[{'name':'实飞测试验证外协','category':'测试验证','source_id':1,'excerpt':text}],
                 'objects':[{'name':'低空飞行系统','source_id':1,'excerpt':'低空飞行系统'}]}
        with patch('app.services.llm.llm.chat',return_value=json.dumps(payload)):
            result=brief.extract(source(content=text))
        self.assertEqual(result['project_types'],['测试验证'])
        self.assertEqual(result['technical_objects'],[])
        self.assertTrue(result['warnings'])

    def test_repeated_activity_is_not_an_object(self):
        text='本次实飞测试验证外协，对监视平台开展实飞测试验证。'
        payload={'services':[{'name':'实飞测试验证外协','category':'测试验证','source_id':1,'excerpt':text}],
                 'objects':[{'name':'实飞测试验证','source_id':1,'excerpt':text},
                            {'name':'监视平台','source_id':1,'excerpt':text}]}
        with patch('app.services.llm.llm.chat',return_value=json.dumps(payload)):
            result=brief.extract(source(content=text))
        self.assertEqual(result['technical_objects'],['监视平台'])
        self.assertTrue(result['warnings'])

    def test_unknown_category_keeps_business_without_forcing_classification(self):
        text='开展古籍修复，对古籍进行处理。'
        payload={'services':[{'name':'古籍修复','category':'虚构分类','source_id':1,'excerpt':text}],
                 'objects':[{'name':'古籍','source_id':1,'excerpt':text}]}
        with patch('app.services.llm.llm.chat',return_value=json.dumps(payload)):
            result=brief.extract(source(content=text))
        self.assertEqual(result['project_types'],[])
        self.assertEqual(result['service_content'],'古籍修复')
        self.assertEqual(result['technical_objects'],['古籍'])

    def test_reject_wrong_source_and_fabricated_quotes(self):
        for entry in [{'name':'车辆','source_id':99,'excerpt':'车辆'},
                      {'name':'车辆','source_id':1,'excerpt':'不存在的车辆说明'}]:
            with patch('app.services.llm.llm.chat',return_value=json.dumps({'services':[],'objects':[entry]})):
                with self.assertRaises(ValueError):brief.extract(source())

    def test_cached_reads_refresh_failure_and_revision(self):
        answer={'project_types':['车辆维修和保养服务'],'technical_objects':['车辆']}
        with patch.object(brief,'sources',return_value=source()),patch.object(brief,'extract',return_value=answer.copy()) as extract:
            first=brief.suggestions(19)
            for _ in range(3):self.assertEqual(brief.suggestions(19),first)
            self.assertEqual(extract.call_count,1)
            extract.side_effect=RuntimeError('connection failed')
            with self.assertRaises(RuntimeError):brief.suggestions(19,refresh=True)
            self.assertEqual(brief.suggestions(19),first)
            extract.side_effect=None
            extract.return_value=answer.copy()
            second=brief.suggestions(19,refresh=True)
            self.assertEqual(second['revision'],2)

    def test_sources_changed_and_scopes_are_isolated(self):
        with patch.object(brief,'sources',return_value=source()) as read,patch.object(brief,'extract',return_value={'technical_objects':['车辆']}) as extract:
            first=copy.deepcopy(brief.suggestions(19))
            read.return_value=source(lot=2)
            brief.suggestions(19)
            read.return_value=source(project=20)
            brief.suggestions(20)
            read.return_value=source(content='更新后的车辆要求')
            fourth=brief.suggestions(19)
            self.assertEqual(extract.call_count,4)
            self.assertNotEqual(first['source_signature'],fourth['source_signature'])

    def test_no_business_project_update_during_extraction(self):
        with patch.object(brief,'sources',return_value=source()),patch.object(brief,'extract',return_value={'project_types':['维修']}),patch('app.core.database.execute') as write:
            brief.suggestions(19)
            brief.suggestions(19,refresh=True)
            write.assert_not_called()

    def test_generation_reads_saved_values_not_cached_suggestion(self):
        from app.services.project_understanding import generation_context
        with patch('app.services.project_understanding.snapshot',return_value={'project':{'project_types':['人工修正维修服务'],'technical_objects':['车辆'],'service_content':'本次车辆保养'},'confirmed':False,'notes':''}),patch('app.services.project_understanding.company_context',return_value=''):
            text=generation_context(19)
            self.assertIn('人工修正维修服务',text)
            self.assertIn('车辆',text)
            self.assertIn('本次车辆保养',text)

    def test_other_lot_and_non_tender_excluded_at_source(self):
        rows=[dict(source()['rows'][0],applicable_lots='["A"]'),
              dict(source()['rows'][0],id=2,content='其他标段机巢',applicable_lots='["B"]')]
        with patch('app.core.database.query_one',side_effect=[{'id':19,'domain':'manufacturing'},{'id':3}]),patch('app.core.database.query',return_value=rows) as query,patch('app.services.lot_service.selected_lot_code',return_value='A'):
            out=brief.sources(19)
            self.assertEqual(len(out['rows']),1)
            self.assertIn("f.category IN ('tender','clarification')",query.call_args.args[0])
            self.assertEqual(query.call_args.args[1],(19,19,3))


if __name__=='__main__':unittest.main()

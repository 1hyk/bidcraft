"""人员归属、章前引言预算、遗留配图占位和无效素材 ID 的回归。"""
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.evidence_roles import technical_facts, flag_unverified_conclusions
from app.services.image_retrieval import bind_references
from app.services import mermaid_completion
from app.services import template_completion
from app.services.image_planning import normalize_outline
from app.core.page_plan import allocate


class RepairsTests(unittest.TestCase):
    def test_explicit_table_need_reaches_completion_without_name_dictionary(self):
        need='岗位职责表'
        body='拟设置服务岗，负责服务协调。\n\n[图片占位：'+need+']'
        with patch('app.services.llm.llm.chat',return_value=json.dumps({'markdown':'| 岗位 | 职责 |\n| --- | --- |\n| 服务岗 | 服务协调 |'},ensure_ascii=False)):
            result=template_completion.complete(body,table_names=[need])
        self.assertNotIn('[图片占位',result)
        self.assertIn('| 服务岗 | 服务协调 |',result)
    def test_legacy_multi_field_table_does_not_become_raster_image(self):
        from app.services.generated_images import eligible
        need={'name':'设备维护责任分配表','source':'material','purpose':'列明设备、责任组、巡检频次及处理时限'}
        outline=normalize_outline({'image_needs':[need]})
        self.assertEqual(outline['image_needs'],[])
        self.assertEqual(outline['table_needs'][0]['name'],need['name'])
        self.assertFalse(eligible(need))
        real=dict(need,name='设备维护责任分配表扫描件',purpose='客户提供的签字扫描件')
        self.assertEqual(normalize_outline({'image_needs':[real]})['image_needs'][0]['source'],'material')
    def test_scope_intro_ignores_old_index_instructions_and_removes_only_own_title(self):
        from app.services.writer_service import _finish_images
        from app.agents.writer_agent import _build_context
        ctx={'node':{'title':'技术方案'},'outline':{'chapter_title':'技术方案','_writing_role':'scope_introduction','thinking':'旧指令唯一标记：必须编制评分索引','key_points':['索引表']}}
        prompt=_build_context(ctx['outline'],[],[],[])
        self.assertNotIn('旧指令唯一标记',prompt)
        self.assertEqual(_finish_images(ctx,'## 技术方案\n\n本方案说明总体安排。',1,1),'本方案说明总体安排。')
        with self.assertRaises(ValueError):
            _finish_images(ctx,'## 其他业务章节\n\n正文。',1,1)

    def test_index_reference_is_not_missing_real_material(self):
        from app.services.chapter_references import normalize_index
        body='|证明材料所在页码|\n|[待补充：业务实施]|\n[待补充：人员劳动合同]\n[待补充：不存在的章节]'
        out=normalize_index(body,['业务实施'])
        self.assertIn('详见“业务实施”章节（页码见最终目录）',out)
        self.assertIn('[待补充：人员劳动合同]',out)
        self.assertIn('[待补充：不存在的章节]',out)
        self.assertEqual(normalize_index(out,['业务实施']),out)

    def test_scope_intro_does_not_accept_full_duplicate_scheme(self):
        from app.services.writer_service import _finish_images
        from app.agents.writer_agent import _build_context
        ctx={'outline':{'_writing_role':'scope_introduction','_child_titles':['业务实施','质量保障']}}
        prompt=_build_context(ctx['outline'],[],[],[])
        self.assertIn('一至两段',prompt)
        self.assertIn('总标题不等同于单独评分的总体实施方案',prompt)
        self.assertEqual(_finish_images(ctx,'本方案按工作范围组织实施，具体措施见下级章节。',1,1),'本方案按工作范围组织实施，具体措施见下级章节。')
        for body in ['文'*1000,'## 组织方案\n详细内容','| 项目 | 做法 |\n| --- | --- |']:
            with self.subTest(body=body[:20]),self.assertRaisesRegex(ValueError,'总范围导语'):
                _finish_images(ctx,body,1,1)
        # 评分项对应的业务父章仍可承载紧凑索引，不按总标题规则误拒绝。
        ordinary={'outline':{'_writing_role':'introduction'}}
        self.assertIn('实施路径',_finish_images(ordinary,'## 实施路径\n本章索引。',1,1))

    def test_lowalt_report_samples_and_existing_matrix_do_not_generate_bitmap(self):
        from app.services.generated_images import eligible
        for name in ['标准化测试结果汇总表样例','问题清单跟踪表样例']:
            need={'name':name,'source':'illustration','allow_generated':True}
            self.assertFalse(eligible(need))
            result=normalize_outline({'image_needs':[need]})
            self.assertEqual(result['image_needs'],[])
            self.assertEqual(result['table_needs'][0]['name'],name)
        body='#### （四）典型故障场景处置矩阵\n\n| 故障类型 | 处置 |\n| --- | --- |\n| 链路丢失 | 安全隔离 |\n\n[图片占位：实飞测试典型故障场景处置矩阵]'
        with patch('app.services.llm.llm.chat') as call:
            result=template_completion.complete(body)
        call.assert_not_called()
        self.assertNotIn('[图片占位',result)
        self.assertIn('| 链路丢失 | 安全隔离 |',result)
        with patch('app.services.llm.llm.chat',return_value='{"markdown":"| 故障 | 处置 |\\n| --- | --- |\\n| 链路 | 隔离<br>回查 |"}'):
            result=template_completion.complete('[图片占位：典型故障场景处置矩阵]')
        self.assertIn('隔离；回查',result)
        self.assertNotIn('[图片占位',result)
    def test_new_names_route_consistently_and_old_outline_never_requests_bitmap(self):
        from app.services.writer_service import _finish_images
        from app.services.generated_images import eligible
        names=['被测子系统功能点分布图','测试数据安全生命周期示意图','子系统测试批次划分示意图']
        for name in names:
            need={'name':name,'source':'illustration','allow_generated':True}
            self.assertFalse(eligible(need))
            ctx={'outline':{'image_needs':[need]},'image_refs':[]}
            with patch('app.services.llm.llm.chat',return_value='{"code":"flowchart LR\\n A[输入] --> B[输出]"}'), patch('app.services.image_completion.complete') as bitmap:
                out=_finish_images(ctx,'方案按输入、输出执行。\n[图片占位：'+name+']',16,1)
            self.assertIn('```mermaid',out)
            self.assertNotIn('[图片占位',out)
            bitmap.assert_not_called()
        name='数据分类分级与权限映射表'
        ctx={'outline':{'image_needs':[{'name':name,'source':'illustration'}]},'image_refs':[]}
        with patch('app.services.llm.llm.chat',return_value='{"markdown":"| 分类 | 权限 |\\n| --- | --- |\\n| 业务数据 | 按岗位授权 |"}'), patch('app.services.image_completion.complete') as bitmap:
            out=_finish_images(ctx,'按岗位授权业务数据。\n[图片占位：'+name+']',16,1)
        self.assertIn('| 分类 | 权限 |',out)
        ctx={'outline':{'image_needs':[{'name':name,'source':'illustration'}]},'image_refs':[]}
        existing='### 数据分类分级与权限映射策略\n\n| 分类 | 权限 |\n| --- | --- |\n| 业务数据 | 按岗位授权 |'
        with patch('app.services.llm.llm.chat') as call:
            out=_finish_images(ctx,existing,16,1)
            call.assert_not_called()
        self.assertEqual(out,existing)
        o=normalize_outline({'image_needs':[{'name':name,'source':'material','purpose':'拟定数据分类和权限策略'}]})
        self.assertEqual(o['image_needs'],[])
        self.assertEqual(o['table_needs'][0]['name'],name)
        o=normalize_outline({'image_needs':[{'name':name,'source':'material','purpose':'采购人提供的真实材料'}]})
        self.assertEqual(o['image_needs'][0]['source'],'material')
        self.assertNotIn('```mermaid',out)
        bitmap.assert_not_called()
        ctx={'outline':{'image_needs':[{'name':name,'source':'illustration'}]},'image_refs':[]}
        with patch('app.services.llm.llm.chat',return_value='{"markdown":"| 分类 | 权限 |\\n| --- | --- |\\n| 业务数据 | 按岗位授权 |"}'):
            out=_finish_images(ctx,'按岗位授权业务数据。',16,1)
        self.assertIn('| 分类 | 权限 |',out)

    def test_real_material_and_failed_table_are_not_converted_to_diagram(self):
        with patch('app.services.llm.llm.chat') as call:
            for name in ['真实系统权限映射表截图','实测数据分类表','资质证书模板']:
                body='[图片占位：'+name+']'
                self.assertEqual(template_completion.complete(body),body)
                self.assertEqual(mermaid_completion.complete(body),body)
            body='[图片占位：数据分类分级与权限映射表]'
            self.assertEqual(mermaid_completion.complete(body),body)
            call.assert_not_called()

    def test_single_chapter_finishes_without_paid_image_requests(self):
        from contextlib import ExitStack
        from app.services import writer_service as service
        ctx={k:[] for k in ('facts','knowledge','siblings','experiences','methods','file_refs')}
        ctx.update(node={'title':'流程'},outline={'image_needs':[]},lot_info={},reqs='',domain='transport')
        with ExitStack() as stack:
            for name,value in [('query_one',{'title':'流程'}),('_chapter_context',ctx),('_target_for',None),
                               ('write_chapter_stream',iter(['[图片占位：任务分解图]'])),('_used_summary',{})]:
                stack.enter_context(patch.object(service,name,return_value=value))
            finish=stack.enter_context(patch.object(service,'_finish_images',return_value='补绘后的正文'))
            persist=stack.enter_context(patch.object(service,'persist_one'))
            events=list(service.generate_chapter_events(12,494))
        finish.assert_called_once()
        self.assertIn('chapter_replace',[e['type'] for e in events])
        self.assertNotIn('chapter_images',[e['type'] for e in events])
        self.assertEqual(persist.call_args.args[2],'补绘后的正文')

    def test_public_tender_logical_diagrams_never_use_image_generation(self):
        from app.services.generated_images import eligible
        for name,prompt in [('测试任务WBS分解示意图','树状图展示16个子系统的任务层级'),
                            ('被测子系统功能模块分布图','层级架构图，展示业务板块'),
                            ('业务组成示意图','层级架构图，展示业务模块')]:
            o=normalize_outline({'image_needs':[{'name':name,'source':'illustration','prompt':prompt,'allow_generated':True}]})
            self.assertEqual(o['image_needs'][0]['source'],'mermaid')
            self.assertFalse(eligible(o['image_needs'][0]))
        # 实际场地图与真实材料不能一并被转成关系图。
        o=normalize_outline({'image_needs':[{'name':'试飞场地空间分布图','source':'illustration','prompt':'三维场景中的跑道和机位空间分布'}]})
        self.assertEqual(o['image_needs'][0]['source'],'illustration')
        o=normalize_outline({'image_needs':[{'name':'系统架构截图','source':'material','prompt':'架构图'}]})
        self.assertEqual(o['image_needs'][0]['source'],'material')

    def test_procurement_contact_is_not_staff_fact(self):
        rows=[{'fact_key':'技术联系人','fact_value':'甲方联系人'},
              {'fact_key':'投诉受理电话','fact_value':'123'},
              {'fact_key':'总工期','fact_value':'30日'},
              {'fact_key':'项目经理资格要求','fact_value':'资格约束'}]
        self.assertEqual([r['fact_key'] for r in technical_facts(rows)],['总工期','项目经理资格要求'])

    def test_zero_deviation_claim_requires_confirmation(self):
        text='经核查，本方案对上述条款实现零负偏离。'
        result=flag_unverified_conclusions(text)
        self.assertIn('[待确认：',result)
        self.assertEqual(flag_unverified_conclusions(result),result)

    def test_intro_budget_is_bounded_and_small_projects_keep_leaf_budget(self):
        nodes=[dict(id=i,title='父章',level=1,has_children=True,intro_words=300) for i in range(1,7)]
        nodes.append(dict(id=7,title='详细方案',level=2,has_children=False))
        for total in [583,116600]:
            result=allocate(nodes,total)
            self.assertEqual(set(result),set(range(1,8)))
            self.assertGreater(result[7],total*.89)
            self.assertLessEqual(result[1],300)

    def test_invalid_identity_cannot_escape_as_binding(self):
        body='前文[图片占位：现场图（一）|素材ID=IMG-d56ccf1f52cb1b6e94]后文'
        self.assertEqual(bind_references(body,[]),'前文[图片占位：现场图（一）]后文')

    def test_relation_placeholder_gets_code_not_bitmap(self):
        with patch('app.services.llm.llm.chat',return_value='{"code":"flowchart LR\\n A[\\"提出问题\\"] --> B[\\"闭环\\"]"}') as call:
            result=mermaid_completion.complete('本章按提出、闭环处理。\n[图片占位：异地协同交付架构图]')
        self.assertIn('```mermaid',result)
        self.assertNotIn('[图片占位',result)
        call.assert_called_once()

    def test_emergency_switch_is_relation_despite_scene_word(self):
        o=normalize_outline({'image_needs':[{'name':'实飞演示场景应急切换示意图',
            'source':'illustration','prompt':'展示场景中的主实时数据链路与备用缓存自动切换'}]})
        self.assertEqual(o['image_needs'][0]['source'],'mermaid')

    def test_real_evidence_and_bound_history_never_invented(self):
        body='[图片占位：检测报告扫描件]\n[图片占位：处理流程图|素材ID=IMG-1234567890abcdef12345678]'
        with patch('app.services.llm.llm.chat',side_effect=AssertionError('不应合成')):
            self.assertEqual(mermaid_completion.complete(body),body)

    def test_mermaid_failure_keeps_actionable_placeholder(self):
        body='[图片占位：资源协同架构图]'
        with patch('app.services.llm.llm.chat',return_value='{"code":"not a diagram"}'):
            self.assertEqual(mermaid_completion.complete(body),body)

    def test_blank_template_uses_native_table_and_never_scanned_certificate(self):
        table='|字段|内容|\n|---|---|\n|签署人|（待填写）|'
        with patch('app.services.llm.llm.chat',return_value=__import__('json').dumps({'markdown':table})) as call:
            result=template_completion.complete('[图片占位：验收签署页样例]\n[图片占位：证书原件模板扫描件]')
        self.assertIn(table,result)
        self.assertIn('空白模板',result)
        self.assertIn('[图片占位：证书原件模板扫描件]',result)
        call.assert_called_once()


if __name__=='__main__':unittest.main()

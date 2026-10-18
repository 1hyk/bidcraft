import sys
import json
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.image_planning import normalize_outline
from app.services.proposal_quality import validate_review
from app.services import pagination_service as pagination


class Planning(unittest.TestCase):
    def test_outline_gets_real_catalog_before_choosing(self):
        from app.agents.orchestrate_agent import generate_outline
        asset={'asset_id':'IMG-real','caption':'历史参考照片','kind':'photo','content':'真实画面描述'}
        response={'image_needs':[{'name':'模型改的名字','source':'history','asset_id':'IMG-real'}]}
        with patch('app.services.image_planning.outline_assets',return_value=[asset]) as pool,patch('app.agents.orchestrate_agent.llm.chat',return_value=json.dumps(response)) as chat:
            out=generate_outline('场景参考','',project_id=6)
            pool.assert_called_once_with(6,'场景参考','')
            self.assertIn('IMG-real',chat.call_args.args[0][1]['content'])
            self.assertEqual(out['image_needs'][0]['name'],'历史参考照片')
    def test_table_not_image_and_relations_not_raster(self):
        source={'image_needs':[{'name':'双向追溯矩阵','source':'illustration'},
                               {'name':'交付甘特图','source':'illustration'},
                               {'name':'测试联调拓扑图','source':'illustration'},
                               {'name':'测试空间效果图','source':'illustration'}]}
        out=normalize_outline(source)
        self.assertTrue(out['need_table'])
        self.assertEqual(out['table_needs'][0]['name'],'双向追溯矩阵')
        self.assertEqual([x['source'] for x in out['image_needs']],['mermaid','mermaid','illustration'])
        self.assertEqual(source['image_needs'][1]['source'],'illustration')

    def test_material_and_original_caption_protected(self):
        assets=[{'asset_id':'IMG-good','caption':'原始图名'}]
        out=normalize_outline({'image_needs':[{'name':'新名字','source':'illustration','asset_id':'IMG-good'},
                                            {'name':'检测证明对照表','source':'material','allow_generated':True}]},assets)
        self.assertEqual(out['image_needs'][0]['name'],'原始图名')
        self.assertEqual(out['image_needs'][0]['source'],'history')
        self.assertEqual(out['image_needs'][1]['source'],'material')
        self.assertFalse(out['image_needs'][1]['allow_generated'])

    def test_invalid_asset_not_trusted(self):
        out=normalize_outline({'image_needs':[{'name':'场景图','source':'history','asset_id':'forged'}]})
        self.assertNotIn('asset_id',out['image_needs'][0])

    def test_history_name_without_id_binds_only_unique_pixels(self):
        need={'image_needs':[{'name':'历史照片','source':'history'}]}
        assets=[{'caption':'历史照片','asset_id':'IMG-one'}]
        self.assertEqual(normalize_outline(need,assets)['image_needs'][0]['asset_id'],'IMG-one')
        self.assertNotIn('asset_id',normalize_outline(need,assets+[{'caption':'历史照片','asset_id':'IMG-two'}])['image_needs'][0])


class Evidence(unittest.TestCase):
    reqs=[{'id':1,'title':'技术要求'},{'id':2,'title':'评分索引'}]
    fragments={'N1F0':{'text':'服务端取出的原始正文','node_id':1}}

    def test_fabricated_quote_never_used(self):
        out=validate_review([{'requirement_id':1,'status':'covered','evidence_ids':['N1F0'],'evidence':'伪造原文'}],self.reqs,self.fragments)
        self.assertEqual(out[0]['evidence'][0]['text'],'服务端取出的原始正文')
        self.assertEqual(out[1]['status'],'unverified')

    def test_invalid_or_missing_ids_fail_closed(self):
        for ids in [[],['unknown'],['N1F0','unknown'],['N1F0']*7]:
            out=validate_review([{'requirement_id':1,'status':'covered','evidence_ids':ids}],self.reqs,self.fragments)
            self.assertEqual(out[0]['status'],'unverified')

    def test_other_requirement_fragments_rejected_and_pending_not_passed(self):
        item={'requirement_id':1,'status':'covered','evidence_ids':['N1F0']}
        self.assertEqual(validate_review([item],self.reqs,self.fragments,{1:[]})[0]['status'],'unverified')
        fragments={'N1F0':{'text':'具体人数[待补充]','node_id':1}}
        out=validate_review([item],self.reqs,fragments)
        self.assertEqual(out[0]['status'],'partial')
        self.assertTrue(out[0]['evidence_verified'])

    def test_scoring_index_not_exempt(self):
        out=validate_review([{'requirement_id':2,'status':'not_applicable'}],self.reqs,self.fragments)
        self.assertEqual(out[1]['status'],'unverified')

    def test_export_scoring_index_uses_actual_chapter_bookmark(self):
        from docx import Document
        from app.core.docx_style import DocxStyle
        from app.services.docx_export import _add_scoring_index, _walk_nodes
        doc=Document();style=DocxStyle()
        tree=[{'id':123,'title':'技术响应','children':[]}]
        req=[{'title':'技术响应评分','suggested_chapter':'技术响应','status':'confirmed'}]
        with patch('app.services.requirement_service.list_requirements',return_value=req):
            _add_scoring_index(doc,style,6,tree)
        _walk_nodes(doc,style,tree,{123:{'body':'正文','format':'md'}},level=1)
        xml=doc.element.xml
        self.assertIn('PAGEREF bc_node_123',xml)
        self.assertIn('w:name="bc_node_123"',xml)
        self.assertIn('技术评分响应索引',xml)


class Pagination(unittest.TestCase):
    def test_progress_api_can_record_failure_and_clear_old_error(self):
        from app.services.compile_service import update_progress
        with patch('app.services.compile_service._save') as save:
            update_progress(6,stage='failed',progress=1,total=2,error='部分失败')
            self.assertEqual(save.call_args.kwargs['error'],'部分失败')
            update_progress(6,stage='done',progress=2,total=2)
            self.assertIsNone(save.call_args.kwargs['error'])
    def test_adjustment_direction(self):
        nodes=[{'id':1,'deviation':.3},{'id':2,'deviation':-.3},{'id':3,'deviation':.1}]
        self.assertEqual(pagination.adjustment_nodes(nodes,True),[1])
        self.assertEqual(pagination.adjustment_nodes(nodes,False),[2])

    def test_snapshot_includes_heading_and_project_name(self):
        class Style:
            def to_dict(self):return {'body_size_pt':12}
        row={'id':1,'title':'原标题','parent_id':None,'sort_order':0,'content':{'body':'正文','format':'md'}}
        with patch('app.services.section_service.generation_node_ids',return_value={1}),patch.object(pagination,'query',return_value=[row]),patch.object(pagination,'query_one',return_value={'name':'项目'}),patch.object(pagination,'get_project_style',return_value=Style()):
            original=pagination.snapshot(6)
            row['title']='新标题'
            self.assertNotEqual(pagination.snapshot(6)['signature'],original['signature'])
            row['title']='原标题'
            with patch.object(pagination,'query_one',return_value={'name':'新项目名'}):
                self.assertNotEqual(pagination.snapshot(6)['signature'],original['signature'])
    def test_freshness_and_target_range(self):
        from app.core.page_plan import PagePlan
        data={'signature':'one','body_pages':174,'style':{},'value':490}
        with patch.object(pagination,'get_project_plan',return_value=PagePlan(150,.15)):
            self.assertFalse(pagination.view(6,data,{'signature':'one'})['within_tolerance'])
            stale=pagination.view(6,data,{'signature':'two'})
            self.assertFalse(stale['fresh'])
            self.assertIsNone(stale['within_tolerance'])
            data['body_pages']=160
            self.assertTrue(pagination.view(6,data,{'signature':'one'})['within_tolerance'])

    def test_no_write_on_failure_or_edit_during_render(self):
        snap={'signature':'before','style':{},'words':100,'complete':True}
        with patch.object(pagination,'snapshot',side_effect=[snap,{**snap,'signature':'changed'}]), patch.object(pagination,'stored',return_value=None), patch('app.services.docx_export.export_project_docx',return_value=(Path('test.docx'),'test.docx')), patch('app.services.document_layout.render',return_value={'body_pages':2}), patch.object(pagination,'execute') as write:
            with self.assertRaisesRegex(ValueError,'修改'):pagination.measure(999999)
            write.assert_not_called()
        with patch.object(pagination,'snapshot',return_value=snap), patch.object(pagination,'stored',return_value=None), patch('app.services.docx_export.export_project_docx',return_value=(Path('test.docx'),'test.docx')), patch('app.services.document_layout.render',side_effect=ValueError('失败')), patch.object(pagination,'execute') as write:
            with self.assertRaisesRegex(ValueError,'失败'):pagination.measure(999999)
            write.assert_not_called()

    def test_settings_save_preserves_measurement(self):
        from app.core.page_plan import save_project_plan,PagePlan
        old={'measurement':{'value':490,'signature':'test'}}
        with patch('app.core.database.query_one',return_value={'page_plan':json.dumps(old)}),patch('app.core.database.execute') as write:
            save_project_plan(6,PagePlan(160,.15))
            new=json.loads(write.call_args.args[1][0])
            self.assertEqual(new['measurement'],old['measurement'])
            self.assertEqual(new['target_pages'],160)


if __name__=='__main__':unittest.main()

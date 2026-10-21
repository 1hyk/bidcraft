"""人工处理不能篡改规则结论，且依赖变化应重新提示；不调用模型。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services import issue_resolution as service
from app.services.image_planning import normalize_outline
from app.services.generated_images import eligible
from app.services.image_completion import _choose_history


class WorkflowTests(unittest.TestCase):
    def test_objective_gaps_cannot_be_clicked_away(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(service.settings,'uploads_dir',Path(temp)):
            item={'issue_id':'a','fingerprint':'b','kind':'missing_evidence'}
            with patch('app.services.proposal_quality.check',return_value={'issues':[item],'handled_issues':[]}):
                for action in ['confirmed','false_positive','not_applicable']:
                    with self.assertRaises(ValueError):service.resolve(20,'a','b',action,'不应关闭实际占位')

    def test_legacy_not_applicable_reopens_without_erasing_history(self):
        import json
        with tempfile.TemporaryDirectory() as temp,patch.object(service.settings,'uploads_dir',Path(temp)):
            issue={'node_id':1,'kind':'image_review','message':'核对图片','excerpt':'图'}
            active,_,_=service.annotate(20,[issue],{1:'body'})
            record=dict(active[0],action='not_applicable',note='旧操作')
            path=service._path(20);path.parent.mkdir(parents=True);path.write_text(json.dumps([record]),encoding='utf-8')
            active,handled,history=service.annotate(20,[issue],{1:'body'})
            self.assertEqual(len(active),1);self.assertFalse(handled)
            self.assertIn('旧处理',active[0]['workflow_note']);self.assertEqual(len(history),1)

    def test_replace_only_completes_after_saved_reference_changes(self):
        import json
        old='IMG-'+'1'*24;new='IMG-'+'2'*24
        marker=lambda a:f'[图片占位：图|素材ID={a}]'
        with tempfile.TemporaryDirectory() as temp,patch.object(service.settings,'uploads_dir',Path(temp)):
            issue={'node_id':1,'kind':'image_review','message':'核对图片','excerpt':'图','asset_id':old}
            context={1:[{'body':marker(old)}]}
            active,_,_=service.annotate(20,[issue],context)
            record=dict(active[0],action='replace_requested',note='替换图片',before_asset_ids=[old])
            path=service._path(20);path.parent.mkdir(parents=True);path.write_text(json.dumps([record]),encoding='utf-8')
            active,handled,history=service.annotate(20,[issue],context)
            self.assertEqual(history[0]['workflow_status'],'pending');self.assertFalse(handled)
            _,handled,history=service.annotate(20,[],{1:[{'body':marker(new)}]})
            self.assertEqual(history[0]['workflow_status'],'completed')
            self.assertEqual(history[0]['replacement_asset_ids'],[new]);self.assertEqual(len(handled),1)
            _,handled,history=service.annotate(20,[],{1:[{'body':'仅删除，没有新图'}]})
            self.assertEqual(history[0]['workflow_status'],'pending');self.assertFalse(handled)

    def test_same_name_still_requires_content_review(self):
        need={'name':'现场布局图','purpose':'室内服务器机房的拟议布局'}
        refs=[{'asset_id':'IMG-a','caption':'现场布局图','content':'室外车辆维修区域'}]
        with patch('app.services.llm.llm.chat',return_value='{"asset_id":null}') as chat:
            self.assertIsNone(_choose_history(need,refs))
            chat.assert_called_once()

    def test_confirm_reopen_changes_and_project_isolation(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(service.settings,'uploads_dir',Path(temp)):
            issue={'node_id':1,'kind':'image_review','message':'核对图','excerpt':'图A'}
            context={1:'body-v1'}
            def report(project):
                active,handled,history=service.annotate(project,[issue],context)
                return dict(issues=active,handled_issues=handled,resolution_history=history)
            with patch('app.services.proposal_quality.check',side_effect=report):
                first=report(20)['issues'][0]
                done=service.resolve(20,first['issue_id'],first['fingerprint'],'confirmed','已对照源图核对')
                self.assertEqual(len(done['issues']),0)
                self.assertEqual(len(done['handled_issues']),1)
                self.assertEqual(len(report(21)['issues']),1)
                reopened=service.resolve(20,first['issue_id'],first['fingerprint'],'reopen','需要再次核对')
                self.assertEqual(len(reopened['issues']),1)
                service.resolve(20,first['issue_id'],first['fingerprint'],'false_positive','已核对为误报')
                context[1]='body-v2'
                changed=report(20)['issues'][0]
                self.assertTrue(changed['reopened'])
                with self.assertRaisesRegex(ValueError,'已变化'):
                    service.resolve(20,first['issue_id'],first['fingerprint'],'confirmed','旧页面确认')
                self.assertEqual(len(report(20)['resolution_history']),3)

    def test_resolution_requires_explanation(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(service.settings,'uploads_dir',Path(temp)):
            with patch('app.services.proposal_quality.check',return_value={'issues':[{'issue_id':'a','fingerprint':'b','kind':'image_review'}],'handled_issues':[]}):
                with self.assertRaisesRegex(ValueError,'说明'):
                    service.resolve(20,'a','b','confirmed',' ')

    def test_content_structure_routes_unseen_names(self):
        for name,structure in [('资源配置展示','assignment'),('对象与职责','comparison'),('每日工作安排','records'),('结果呈现','measurements')]:
            result=normalize_outline({'image_needs':[{'name':name,'source':'illustration','content_origin':'proposal_design','data_structure':structure}]})
            self.assertFalse(result['image_needs'])
            self.assertEqual(result['table_needs'][0]['name'],name)

    def test_relationship_and_time_are_not_raster(self):
        for structure,mode in [('timeline','gantt'),('hierarchy','mermaid'),('process','mermaid')]:
            result=normalize_outline({'image_needs':[{'name':'未见过的名称','source':'illustration','data_structure':structure}]})
            self.assertEqual(result['image_needs'][0]['diagram_type'],mode)
            self.assertFalse(eligible(result['image_needs'][0]))

    def test_real_evidence_cannot_be_reclassified(self):
        result=normalize_outline({'image_needs':[{'name':'证书关系图','source':'illustration','content_origin':'external_evidence','data_structure':'relationships'}]})
        self.assertEqual(result['image_needs'][0]['source'],'material')
        self.assertFalse(eligible(result['image_needs'][0]))

    def test_spatial_scene_is_not_blocked_by_relationship_word(self):
        need={'name':'现场组织布置场景','source':'illustration','content_origin':'proposal_design','data_structure':'spatial_scene','purpose':'拟议布局'}
        self.assertTrue(eligible(normalize_outline({'image_needs':[need]})['image_needs'][0]))


if __name__=='__main__':unittest.main()

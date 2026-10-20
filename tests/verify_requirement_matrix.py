"""报告持久化、过期及原文证据完整性。使用临时目录，不修改业务数据。"""
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services import requirement_matrix as matrix
from app.services.proposal_quality import validate_review
real_requirement_signature=matrix.requirement_signature
real_source_files=matrix.source_files


class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        folder=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(matrix,'_path',side_effect=lambda pid:folder/f'{pid}.json'))
        self.rows=[{'id':1,'title':'测试方案','category':'评分办法','content':'覆盖功能及性能测试','weight':8,'source_file':'公开招标.pdf','source_location':'第28页','status':'pending','suggested_chapter':'测试方案'}]
        self.stack.enter_context(patch.object(matrix,'requirements',return_value=self.rows))
        self.stack.enter_context(patch.object(matrix,'source_files',return_value={}))
        self.req=self.stack.enter_context(patch.object(matrix,'requirement_signature',return_value='req-v1'))
        self.body=self.stack.enter_context(patch('app.services.pagination_service.snapshot',return_value={'signature':'body-v1'}))
        self.stack.enter_context(patch('app.services.proposal_quality.chapters',return_value=[{'id':10,'title':'测试方案','body':'测试正文'}]))

    def data(self):
        return {'signature':'body-v1','requirements_signature':'req-v1','items':[{'requirement_id':1,'status':'covered','evidence':[{'id':'N10F0','node_id':10,'chapter':'测试方案','text':'实际正文'}],'evidence_verified':True,'gap':''}]}

    def test_before_review_has_sources_but_no_fake_evidence(self):
        result=matrix.matrix(12)
        self.assertFalse(result['fresh']);self.assertFalse(result['has_assessment'])
        item=result['items'][0]
        self.assertEqual(item['source_location'],'第28页');self.assertEqual(item['content'],'覆盖功能及性能测试')
        self.assertEqual(item['status'],'unverified');self.assertFalse(item['evidence_verified'])
        self.assertEqual(item['target_nodes'][0]['node_id'],10)

    def test_saved_report_is_reused_after_reopen_and_is_project_scoped(self):
        saved=matrix.save_assessment(12,self.data())
        self.assertTrue(saved['fresh']);self.assertEqual(saved['summary']['covered'],1)
        self.assertEqual(matrix.matrix(12)['items'][0]['evidence'][0]['text'],'实际正文')
        self.assertFalse(matrix.matrix(13)['has_assessment'])

    def test_body_and_requirement_changes_invalidate_and_hide_evidence(self):
        matrix.save_assessment(12,self.data())
        for field in [self.body,self.req]:
            old=field.return_value
            field.return_value={'signature':'body-v2'} if field is self.body else 'req-v2'
            result=matrix.matrix(12)
            self.assertFalse(result['fresh']);self.assertEqual(result['items'][0]['evidence'],[])
            self.assertEqual(result['summary']['covered'],0)
            field.return_value=old

    def test_changes_during_review_do_not_overwrite_previous_report(self):
        matrix.save_assessment(12,self.data());before=matrix._path(12).read_bytes()
        self.req.return_value='changed'
        with self.assertRaises(ValueError):matrix.save_assessment(12,self.data())
        self.assertEqual(matrix._path(12).read_bytes(),before)

    def test_invalid_or_cross_requirement_ids_never_pass(self):
        fragment={'a':{'id':'a','text':'正文原文','node_id':10}}
        for item in [{'requirement_id':True,'status':'covered','evidence_ids':['a']},
                     {'requirement_id':1,'status':'covered','evidence_ids':['invented']}]:
            result=validate_review([item],self.rows,fragment)[0]
            self.assertEqual(result['status'],'unverified')
        result=validate_review([{'requirement_id':1,'status':'covered','evidence_ids':['a'],'evidence':[{'text':'模型编造'}]}],self.rows,fragment,{1:[]})[0]
        self.assertEqual(result['status'],'unverified');self.assertEqual(result['evidence'],[])
        result=validate_review([{'requirement_id':1,'status':'covered','evidence_ids':['a']}],self.rows,fragment,{})[0]
        self.assertEqual(result['status'],'unverified')

    def test_pending_claim_and_original_evidence(self):
        for text in ['[待确认：能力]','我方具备检验检测资质']:
            result=validate_review([{'requirement_id':1,'status':'covered','evidence_ids':['a']}],self.rows,{'a':{'text':text}})[0]
            self.assertEqual(result['status'],'covered');self.assertEqual(result['evidence'][0]['text'],text)

    def test_model_outage_does_not_replace_saved_report(self):
        from app.services.proposal_quality import review
        matrix.save_assessment(12,self.data());before=matrix._path(12).read_bytes()
        with patch('app.services.llm.llm.chat',side_effect=RuntimeError('模型暂不可用')):
            with self.assertRaisesRegex(ValueError,'已保留上次报告'):review(12)
        self.assertEqual(matrix._path(12).read_bytes(),before)

    def test_lot_change_alters_requirement_signature(self):
        # 调用真实摘要算法，证明标段不靠“要求刚好不同”才能失效。
        with patch('app.services.lot_service.selected_lot_code',side_effect=['A','B']):
            self.assertNotEqual(real_requirement_signature(12,self.rows),real_requirement_signature(12,self.rows))

    def test_source_links_are_project_scoped_and_ambiguous_names_not_linked(self):
        # 调用真实函数，唯一文件给原件链接；同名多版本不能任意挑一份。
        with patch('app.core.database.query',return_value=[{'id':9,'file_name':'文件.pdf','file_path':'project_12/a b.pdf'}]) as query:
            data=real_source_files(12)
            self.assertEqual(query.call_args.args[1],(12,))
            self.assertEqual(data['文件.pdf']['url'],'/uploads/project_12/a%20b.pdf')
        with patch('app.core.database.query',return_value=[{'id':9,'file_name':'文件.pdf','file_path':'a'},{'id':10,'file_name':'文件.pdf','file_path':'b'}]):
            self.assertEqual(real_source_files(12),{})


if __name__=='__main__':unittest.main()

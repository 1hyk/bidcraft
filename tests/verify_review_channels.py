import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.requirement_matrix import response_scope,review_content
class Tests(unittest.TestCase):
 def row(self,content,title='编制要求',category='编制要求'):return {'id':1,'title':title,'category':category,'content':content}
 def test_reference_only_is_separate_not_covered(self):
  row=self.row('响应文件格式详见附件2。技术方案部分应按照附件三评审办法的方案要求自行编制。')
  self.assertEqual(response_scope(row),'compilation_check');self.assertEqual(review_content(row)[0],'')
 def test_mixed_keeps_actual_technical_obligation(self):
  row=self.row('响应文件格式详见附件2。系统并发数量须达到100。')
  self.assertEqual(response_scope(row),'technical');self.assertEqual(review_content(row)[0],'系统并发数量须达到100。')
 def test_protocol_format_not_confused_with_document_format(self):
  row=self.row('接口报文格式按附件二定义并完成编码校验。')
  self.assertEqual(response_scope(row),'technical');self.assertEqual(review_content(row)[1],[])
 def test_explicit_index_requirement_stays_technical(self):
  row=self.row('按附件二格式编制。',title='技术响应逐条索引')
  self.assertEqual(response_scope(row),'technical')
 def test_layout_has_separate_check(self):
  self.assertEqual(response_scope(self.row('正文字体为宋体，页边距为2厘米。')),'compilation_check')
 def test_scoring_requirement_not_exempted_by_reference(self):
  row=self.row('方案按照附件编制。',category='评分办法')
  self.assertEqual(response_scope(row),'technical')
 def test_review_does_not_send_forwarding_clause_to_model(self):
  from app.services.proposal_quality import review
  rows=[self.row('响应文件格式详见附件2。'),dict(self.row('系统并发数量须达到100。'),id=2)]
  with patch('app.services.requirement_matrix.requirements',return_value=rows),patch('app.services.requirement_matrix.requirement_signature',return_value='req'),patch('app.services.requirement_matrix.save_assessment',side_effect=lambda project,data:data),patch('app.services.pagination_service.snapshot',return_value={'signature':'body'}),patch('app.services.proposal_quality.chapters',return_value=[{'id':1,'title':'性能','body':'拟验证100个并发接入。'}]),patch('app.services.llm.llm.chat',return_value='{"items":[{"requirement_id":2,"status":"covered","evidence_ids":["N1F0"]}]}') as chat:
   result=review(21)
   self.assertEqual(result['items'][0]['status'],'covered')
   self.assertNotIn('响应文件格式详见',chat.call_args.args[0][1]['content'])
if __name__=='__main__':unittest.main()

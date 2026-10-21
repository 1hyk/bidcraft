import sys,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.review_artifacts import checks,body_content
from app.services.requirement_matrix import response_scope
class Tests(unittest.TestCase):
 def test_qualification_contract_proof_is_separate_from_technical_work(self):
  self.assertEqual(response_scope({'title':'资格业绩要求','content':'近三年具有类似业绩，需提供合同关键页和对应结算材料。'}),'other_documents')
  self.assertEqual(response_scope({'title':'人员资格与培训安排','content':'说明测试人员职责分工和培训方法。'}),'technical')
  self.assertEqual(response_scope({'title':'资格要求','content':'说明岗位能力与任务安排。'}),'technical')
 def test_contract_work_is_not_final_artifact(self):
  for title,text in [('验证','实际开展测试并跟踪问题。'),('验收','提交验收报告，双方签署验收书。'),('维修','按期提供维修和培训服务。')]:
   row={'title':title,'category':'技术要求','content':text}
   self.assertEqual(response_scope(row),'technical');self.assertEqual(checks(row),[])
 def test_explicit_current_bid_page_field_is_separate(self):
  row={'category':'编制要求','title':'响应索引','content':'编制对应索引（填写对应证明材料的所在页码）。'}
  self.assertEqual(len(checks(row)),1)
  self.assertIn('编制对应索引',body_content(row,row['content']))
  self.assertNotIn('填写',body_content(row,row['content']))
 def test_page_word_alone_or_negative_does_not_trigger(self):
  for text in ['说明报告索引和页码编排方法。','编制索引，无需填写实际页码。']:
   self.assertEqual(checks({'category':'编制要求','title':'方法','content':text}),[])
 def test_review_has_no_decomposition_gate_and_keeps_all_work(self):
  from app.services.proposal_quality import review
  rows=[{'id':i,'title':name,'category':'技术要求','content':text} for i,(name,text) in enumerate([('实飞','实际执行飞行并跟踪问题。'),('验收','交付报告并签署验收书。'),('售后','按时提供维修服务。'),('其他领域','实施车辆巡检并提交维修记录。')],1)]
  seen=[]
  def model(messages,**kwargs):
   tasks=json.loads(messages[1]['content'])['requirements'];seen.extend(tasks)
   return json.dumps({'items':[{'requirement_id':r['id'],'status':'covered','evidence_ids':[r['allowed_evidence_ids'][0]]} for r in tasks]})
  with patch('app.services.review_obligations.decompose',side_effect=AssertionError('不能再调用拆分门槛')),patch('app.services.requirement_matrix.requirements',return_value=rows),patch('app.services.requirement_matrix.requirement_signature',return_value='r'),patch('app.services.pagination_service.snapshot',return_value={'signature':'s'}),patch('app.services.proposal_quality.chapters',return_value=[{'id':1,'title':'实施安排','body':'拟实施并按期提供交付及售后。'}]),patch('app.services.llm.llm.chat',side_effect=model),patch('app.services.requirement_matrix.save_assessment',side_effect=lambda p,d:d):
   result=review(21)
  self.assertEqual(len(result['items']),4);self.assertEqual({r['id'] for r in seen},{1,2,3,4})
  self.assertTrue(all('obligations' not in r for r in seen))
if __name__=='__main__':unittest.main()

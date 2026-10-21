import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.review_obligations import parse_obligations,validate
class Tests(unittest.TestCase):
 def test_source_ids_keep_numbering_without_model_copying(self):
  from app.services.review_obligations import source_units,parse_source_obligations
  req={'id':1,'content':'1. 编制索引（填写实际页码）。'}
  units=source_units(req['content'])
  result=parse_source_obligations([{'requirement_id':1,'obligations':[{'source_ids':[i],'stage':'body'} for i in units]}],req)
  self.assertTrue(result[0]['quote'].startswith('1.'))
  self.assertEqual(len(result),len(units))

 def test_failed_requirement_isolated_from_valid_requirement(self):
  import json
  from unittest.mock import patch
  from app.services.review_obligations import decompose
  reqs=[{'id':1,'title':'维护','content':'执行巡检。提交维修记录。'},{'id':2,'title':'服务','content':'安排服务支持。'}]
  raw={'items':[{'requirement_id':1,'obligations':[{'source_ids':['S1'],'stage':'body'}]},{'requirement_id':2,'obligations':[{'source_ids':['S1'],'stage':'body'}]}]}
  with patch('app.services.llm.llm.chat',return_value=json.dumps(raw)):
   parts=decompose(reqs)
  self.assertIn('S2',parts[1][0]['decomposition_error'])
  result=validate([{'requirement_id':2,'obligations':[{'obligation_id':'R2O1','status':'covered','evidence_ids':['a']}]}],reqs,{'a':{'text':'提供支持'}},{2:['a']},parts)
  self.assertEqual(result[0]['status'],'unverified');self.assertEqual(result[1]['status'],'covered')

 def test_unknown_source_id_never_becomes_evidence(self):
  from app.services.review_obligations import parse_source_obligations
  with self.assertRaisesRegex(ValueError,'编号'):
   parse_source_obligations([{'requirement_id':1,'obligations':[{'source_ids':['S99'],'stage':'body'}]}],{'id':1,'content':'执行巡检。'})
 def test_decomposition_cache_tracks_requirements_not_body(self):
  import tempfile,json
  from unittest.mock import patch
  from app.core.config import settings
  from app.services.review_obligations import decompose
  req={'id':1,'content':'执行巡检。'}
  raw=json.dumps({'items':[{'requirement_id':1,'obligations':[{'source_ids':['S1'],'stage':'body'}]}]})
  with tempfile.TemporaryDirectory() as temp,patch.object(settings,'uploads_dir',Path(temp)),patch('app.services.llm.llm.chat',return_value=raw) as chat:
   first=decompose([req],project_id=21);second=decompose([req],project_id=21)
   self.assertEqual(first,second);chat.assert_called_once()
   changed={'id':1,'content':'执行巡检并交付记录。'}
   chat.return_value=json.dumps({'items':[{'requirement_id':1,'obligations':[{'source_ids':['S1'],'stage':'body'}]}]})
   decompose([changed],project_id=21);self.assertEqual(chat.call_count,2)
 def setUp(self):
  self.req={'id':7,'title':'响应索引','content':'编制对应索引。填写实际所在页码。'}
  self.parts=parse_obligations([{'requirement_id':7,'obligations':[{'quote':'编制对应索引。','stage':'body'},{'quote':'填写实际所在页码。','stage':'export'}]}],[self.req])
  self.fragments={'a':{'id':'a','node_id':1,'chapter':'索引','text':'索引：见实施方法章节'}}
 def test_chapter_reference_does_not_prove_final_pages(self):
  result=validate([{'requirement_id':7,'status':'covered','obligations':[{'obligation_id':'R7O1','status':'covered','evidence_ids':['a']},{'obligation_id':'R7O2','status':'covered','evidence_ids':['a']}]}],[self.req],self.fragments,{7:['a']},self.parts)[0]
  self.assertEqual(result['status'],'pending_stage');self.assertEqual(result['body_status'],'covered')
  self.assertEqual(result['obligations'][1]['status'],'pending_stage')
 def test_omitted_obligation_cannot_return_covered(self):
  with self.assertRaisesRegex(ValueError,'遗漏'):
   parse_obligations([{'requirement_id':7,'obligations':[{'quote':'编制对应索引。','stage':'body'}]}],[self.req])
 def test_invented_quote_or_unknown_stage_rejected(self):
  for part in [{'quote':'必须装订','stage':'body'},{'quote':self.req['content'],'stage':'guess'}]:
   with self.assertRaises(ValueError):parse_obligations([{'requirement_id':7,'obligations':[part]}],[self.req])
 def test_cross_requirement_evidence_not_accepted(self):
  result=validate([{'requirement_id':7,'obligations':[{'obligation_id':'R7O1','status':'covered','evidence_ids':['a']}]}],[self.req],self.fragments,{7:['other']},self.parts)[0]
  self.assertEqual(result['status'],'unverified');self.assertEqual(result['review_state'],'error')
 def test_missing_body_obligation_is_not_masked_by_export_stage(self):
  result=validate([{'requirement_id':7,'obligations':[{'obligation_id':'R7O1','status':'missing','evidence_ids':[],'gap':'没有对应索引'}]}],[self.req],self.fragments,{7:['a']},self.parts)[0]
  self.assertEqual(result['status'],'missing');self.assertIn('export',result['pending_stages'])
 def test_other_business_has_multiple_independent_body_obligations(self):
  req={'id':9,'title':'维护服务','content':'执行巡检。提交维修记录。'}
  parts=parse_obligations([{'requirement_id':9,'obligations':[{'quote':'执行巡检。','stage':'body'},{'quote':'提交维修记录。','stage':'body'}]}],[req])
  result=validate([{'requirement_id':9,'obligations':[{'obligation_id':'R9O1','status':'covered','evidence_ids':['a']},{'obligation_id':'R9O2','status':'missing','gap':'未写记录交付'}]}],[req],self.fragments,{9:['a']},parts)[0]
  self.assertEqual(result['status'],'partial');self.assertEqual(result['missing_points'],['提交维修记录。'])
 def test_word_page_in_proposed_method_does_not_force_export(self):
  req={'id':10,'title':'归档方法','content':'说明测试报告编页码的方法。'}
  parts=parse_obligations([{'requirement_id':10,'obligations':[{'quote':req['content'],'stage':'body'}]}],[req])
  result=validate([{'requirement_id':10,'obligations':[{'obligation_id':'R10O1','status':'covered','evidence_ids':['a']}]}],[req],self.fragments,{10:['a']},parts)[0]
  self.assertEqual(result['status'],'covered')
 def test_number_in_performance_requirement_can_still_be_body(self):
  req={'id':11,'title':'性能','content':'支持100并发接入。'}
  parts=parse_obligations([{'requirement_id':11,'obligations':[{'quote':req['content'],'stage':'body'}]}],[req])
  result=validate([{'requirement_id':11,'obligations':[{'obligation_id':'R11O1','status':'covered','evidence_ids':['a']}]}],[req],self.fragments,{11:['a']},parts)[0]
  self.assertEqual(result['status'],'covered')
if __name__=='__main__':unittest.main()

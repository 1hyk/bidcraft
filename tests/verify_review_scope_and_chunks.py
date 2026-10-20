"""技术正文范围、段落完整性与核验失败状态回归。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.requirement_matrix import response_scope
from app.services.proposal_quality import evidence_chunks,validate_review

for title in ['最高限价超限废标风险','报价要求','响应文件递交要求','串通投标情形','资格业绩不符']:
    # 单独的“报价要求”保守留在技术范围，只有明确词才分流。
    if title!='报价要求':assert response_scope({'title':title})=='other_documents'
for title in ['技术评分索引要求','监视能力全量验证','交付与售后要求']:
    assert response_scope({'title':title})=='technical'
text='完整第一段。\n\n'+'完整长段落。'*600
chunks=list(evidence_chunks(text))
assert len(chunks)>1 and all(c.endswith('。') for c in chunks)
assert ''.join(chunks).replace('\n','')==text.replace('\n','')
req=[{'id':1,'title':'技术要求'}]
bad=validate_review([{'requirement_id':1,'status':'covered','evidence_ids':['wrong']}],req,{})[0]
assert bad['review_state']=='error' and bad['status']=='unverified'
missing=validate_review([{'requirement_id':1,'status':'missing','evidence_ids':[]}],req,{})[0]
assert missing['review_state']=='completed' and missing['status']=='missing'
fragments={'a':{'text':'岗位安排完整。[待补充：人员姓名]'}}
covered=validate_review([{'requirement_id':1,'status':'covered','evidence_ids':['a']}],req,fragments)[0]
assert covered['status']=='covered'
partial_req=[{'id':1,'title':'服务要求','content':'2小时内响应；提交测试报告'}]
partial=validate_review([{'requirement_id':1,'status':'partial','evidence_ids':['a'],'missing_points':['2小时内响应']}],partial_req,fragments)[0]
assert partial['status']=='partial' and partial['missing_points']==['2小时内响应']
invalid=validate_review([{'requirement_id':1,'status':'partial','evidence_ids':['a'],'missing_points':['无关人员姓名']}],partial_req,fragments)[0]
assert invalid['status']=='unverified' and invalid['review_state']=='error'
assert not invalid['evidence_verified']
print('PASS scope, complete chunks, failure distinct from missing')

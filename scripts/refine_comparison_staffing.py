"""只修订对照文件中的排班和承诺一致性，不写回项目。"""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.agents.writer_agent import WRITER_SYSTEM,llm
from app.core.domains import fill_genre
out=Path(__file__).resolve().parents[1]/'uploads/_verification/complete_draft_20261019'
p=out/'555_after.md'
body=llm.chat([{'role':'system','content':fill_genre(WRITER_SYSTEM,'lowalt')},
 {'role':'user','content':'修订以下对照稿，输出完整正文。具体纠正：这是最高限价81.9万元、30日历天交付、12个月质保的测试验证外协项目。删除8人专职售后及全员白班、三班倒的不合理测算；改成项目协调岗、远程支持岗、共享专家及按需现场支援的拟配置机制，可兼岗但不能把兼岗算作重复人头，不虚构公司已有共享资源。未强制人数时不强报总人数，不产生待补标记。轮值承接工单、专家升级和应急调度明确即可，不声称固定少量人员已实现无休满覆盖。不承诺杭州至大安机场4小时交通可行性，按服务地点组织属地或就近支援。原始故障时限严格保留：紧急≤2小时响应，无法远程修复4小时内到场；重大30分钟内响应、2小时内给方案；一般及轻微15分钟响应、当日处置。不得把重大30分钟响应改成30分钟出方案。不要新增自行承诺的资格年限、额外SLA或完成事实。保留有效章节、措施和图名，不写自评满分。\n\n'+p.read_text(encoding='utf-8')}],temperature=.2,max_tokens=18000)
p.write_text(body,encoding='utf-8')
report=json.loads((out/'result.json').read_text(encoding='utf-8'))
for r in report['chapters']:
 if r['node_id']==555:r['characters']=len(body);r['review']='排班与响应时限一致性修订，仍须人工复核'
(out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('saved refined comparison',len(body),flush=True)

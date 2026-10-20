"""完整方案生成契约：允许拟实施设计，保留真实性边界与父章索引。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.agents.writer_agent import WRITER_SYSTEM, _build_context
from app.services.evidence_roles import flag_unverified_conclusions
assert '所有数字（工期/人数' not in WRITER_SYSTEM
for phrase in ['可合理设计','不强报总人数','不得混写','不自行宣称满分']:
    assert phrase in WRITER_SYSTEM, phrase
text=_build_context({'_writing_role':'introduction','_child_titles':['需求响应甲','需求响应乙']},[],[],[])
assert '需求响应甲' in text and '需求响应乙' in text and '紧凑索引表' in text
assert '待确认' not in flag_unverified_conclusions('我方承诺满足要求，无负偏离。')
assert '待确认' in flag_unverified_conclusions('经逐条核查，全部无负偏离。')
print('PASS: complete draft contract, child index, commitment vs verified conclusion')

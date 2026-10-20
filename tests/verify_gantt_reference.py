"""低空真实样本的里程碑引用遗漏 after，保留所有工期与日期。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.mermaid_source import normalize

source='gantt\n dateFormat YYYY-MM-DD\n 文档编制 :d1, 2026-10-01, 3d\n 最终交付 :milestone, d1, 0d\n'
fixed=normalize(source)
assert ':milestone, after d1, 0d' in fixed
assert '2026-10-01, 3d' in fixed
assert normalize(fixed)==fixed
for line in ['里程碑 :milestone, 2026-10-04, 0d','里程碑 :milestone, unknown, 0d']:
    value='gantt\n dateFormat YYYY-MM-DD\n'+line
    assert normalize(value)==value
print('PASS: known gantt task reference repaired; dates and unknown references unchanged')

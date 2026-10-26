"""要求—章节对应只用于发现目录漏项，不做人工安排。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services import project_understanding as service


class Coverage(unittest.TestCase):
    def test_evidence_recommendation(self):
        row = {'title':'工期要求','content':'收到通知后30日历天内交货','category':'技术要求'}
        nodes = [dict(id=1,title='交付批次规划',outline={'thinking':'收到通知后30日历天内交货，分阶段提交成果。'}),
                 dict(id=2,title='人员职责',outline={'thinking':'明确人员岗位职责与分工。'})]
        result = service.chapter_candidates(row,nodes)
        self.assertEqual([n['id'] for n in result], [1])
        self.assertIn('30日历天',result[0]['basis'])
        self.assertNotIn('covered',result[0])

    def test_no_evidence_no_recommendation(self):
        self.assertEqual(service.chapter_candidates({'title':'交付地点','content':'甲方指定地点','category':'技术要求'},
            [dict(id=1,title='团队配置',outline={})]), [])

    def test_coverage_gaps_report_only_unmatched_technical_requirements(self):
        rows = [
            {'id':1,'title':'工期要求','content':'收到通知后30日历天内交货','category':'技术要求'},
            {'id':2,'title':'交付地点','content':'甲方指定地点','category':'技术要求'},
            # 编制要求不承接正文，不参与覆盖检查。
            {'id':3,'title':'投标文件编制要求','content':'字体字号按附件格式','category':'编制要求'},
        ]
        nodes = [dict(id=1,title='交付批次规划',outline={'thinking':'收到通知后30日历天内交货，分阶段提交成果。'})]
        self.assertEqual([row['id'] for row in service.coverage_gaps(rows, nodes)], [2])

    def test_no_candidates_when_directory_is_empty(self):
        row = {'id':1,'title':'工期要求','content':'收到通知后30日历天内交货','category':'技术要求'}
        self.assertEqual(service.coverage_gaps([row], []), [row])

    def test_ip_ownership_is_not_a_directory_gap(self):
        row = {'id':4,'title':'知识产权归属','category':'废标风险',
               'content':'本次采购履行过程中新产生的知识产权归甲方所有。（★项）'}
        self.assertEqual(service.requirement_group(row), 'bid_notices')
        self.assertEqual(service.coverage_gaps([row], []), [])


if __name__ == '__main__':
    unittest.main()

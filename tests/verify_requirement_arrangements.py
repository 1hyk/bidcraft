"""章节安排：推荐不是响应结论，人工关联不跨项目标段。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services import project_understanding as service


class Arrangements(unittest.TestCase):
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

    def test_save_clear_and_reject_foreign_nodes(self):
        row = dict(id=7,title='工期',content='30日历天',display_group='technical')
        current = dict(requirements=[row],directory_nodes=[dict(id=9,title='计划')],lot='A')
        with tempfile.TemporaryDirectory() as tmp, patch.object(service,'root',return_value=Path(tmp)), patch.object(service,'snapshot',return_value=current):
            service.save_arrangement(25,7,[9,9])
            stored = service.read(service._arrangement_name(25,'A'),{})['7']
            self.assertEqual(stored['node_ids'],[9])
            self.assertEqual(stored['signature'],service._requirement_signature(row))
            self.assertNotEqual(stored['signature'],service._requirement_signature({**row,'content':'60日历天'}))
            self.assertEqual(service.read(service._arrangement_name(25,'B'),{}),{})
            with self.assertRaises(ValueError): service.save_arrangement(25,7,[999])
            with self.assertRaises(ValueError): service.save_arrangement(25,8,[9])
            service.save_arrangement(25,7,[])
            self.assertEqual(service.read(service._arrangement_name(25,'A'),{}),{})


if __name__ == '__main__': unittest.main()

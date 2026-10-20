"""原文定位：确定性匹配、消歧、真实上下文及接口范围隔离。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.source_locator import locate
from app.api.files import locate_requirement
from fastapi import HTTPException


class SourceLocatorTests(unittest.TestCase):
    def setUp(self):
        self.req={'id':3,'content':'收到成交通知书之日起，30日历天内交货。（注：原文标记为★）','source_file':'招标.docx'}
        self.text='## 2.采购范围\n\n2.4 项目质量要求。\n\n★2.5 工期：收到成交通知书之日起，30日历天内交货。\n\n2.6 质保期要求。\n\n不相关的最后一段。'
        self.section={'id':4,'content':self.text}

    def test_exact_original_context_and_not_same_number(self):
        result=locate(self.req,[{'id':5,'content':'2.5 售后服务'},self.section])
        self.assertEqual(result['section_id'],4)
        self.assertIn('★2.5 工期：',result['excerpt'])
        self.assertIn('2.4 项目质量',result['excerpt'])
        self.assertNotIn('不相关的最后一段',result['excerpt'])

    def test_parent_child_choose_smallest_but_unrelated_duplicate_refuses(self):
        parent={'id':8,'content':'前言\n\n'+self.text}
        self.assertEqual(locate(self.req,[parent,self.section])['section_id'],4)
        other={'id':9,'content':'另一种上下文。\n\n收到成交通知书之日起，30日历天内交货。'}
        self.assertFalse(locate(self.req,[other,self.section])['matched'])

    def test_summary_and_short_text_do_not_guess(self):
        for text in ['工期要求','从通知开始一个月交货']:
            self.assertFalse(locate({'content':text},[self.section])['matched'])

    def test_merged_table_list_and_partial_match_are_explicit(self):
        req={'content':'需提交：1.飞行试验执行方案设计与测试用例；2.飞行试验任务执行报告。'}
        section={'id':10,'content':'| 序号 | 名称 | 数量 |\n| --- | --- | --- |\n| 1 | 飞行试验执行方案设计与测试用例 | 1 |\n| 2 | 飞行试验任务执行报告 | 1 |'}
        result=locate(req,[section])
        self.assertTrue(result['matched'])
        self.assertEqual(result['match_type'],'partial')
        self.assertIn('摘要全部内容',result['message'])
        self.assertIn('| 1 |',result['excerpt'])
        self.assertIn('| 序号 |',result['excerpt'])

    def test_conflicting_deadline_does_not_match_short_shared_clause(self):
        self.assertFalse(locate({'content':'工期：收到通知后60日历天内交货。'},[self.section])['matched'])

    def test_api_refuses_other_project_file_or_requirement(self):
        for file,reqs in [(None,[self.req]),({'file_name':'其他.docx'},[self.req]),({'file_name':'招标.docx'},[])]:
            with patch('app.api.files.query_one',return_value=file), patch('app.services.requirement_matrix.requirements',return_value=reqs):
                with self.assertRaises(HTTPException) as error:locate_requirement(15,12,3)
                self.assertEqual(error.exception.status_code,404)


if __name__=='__main__':unittest.main()

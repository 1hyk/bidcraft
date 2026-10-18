"""无需模型或数据库，钉住同业跨服务的误用与合理复用。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services.knowledge_applicability import classify, applicable
from app.services.matching import match_pages, chapter_skeleton


class Applicability(unittest.TestCase):
    def test_leaf_overrides_document_business(self):
        self.assertEqual(classify('金融开发方案 > 软件测试验收')['service_types'], ['测试'])

    def test_conflict_and_valid_cross_document_reuse(self):
        self.assertFalse(applicable({'title': '软件开发实施'}, '软件测试实施'))
        self.assertTrue(applicable({'title': '软件开发项目 > 测试验收'}, '软件测试实施'))
        self.assertTrue(applicable({'title': '开发项目 > 进度计划'}, '软件测试进度计划'))
        self.assertTrue(applicable({'title': '实施方案'}, '软件测试实施'))
        self.assertTrue(applicable({'title': '开发与测试验收'}, '软件测试实施'))

    def test_matching_enforces_metadata_even_for_exact_titles(self):
        pages=[{'id': 1, 'title': '实施方案', 'leaf': '实施方案', 'tags': [],
                'usage': '', 'service_types': ['开发'], 'chapter_purposes': ['设计开发']},
               {'id': 2, 'title': '测试实施方案', 'leaf': '测试实施方案', 'tags': [],
                'usage': '', 'service_types': ['测试'], 'chapter_purposes': ['测试验收']}]
        hits=match_pages('测试实施方案', pages=pages, domain='finance', llm_judge=False)
        self.assertEqual([p['id'] for p in hits], [2])

    def test_skeleton_excludes_conflicting_parent_service(self):
        pages=[{'title':'测试方案 > 用例设计', 'service_types':['开发'], 'chapter_purposes':['设计开发']}]
        self.assertEqual(chapter_skeleton('测试方案',pages=pages), [])

    def test_invalid_labels_do_not_override_local_evidence(self):
        self.assertEqual(classify('测试用例', {'service_types':['金融',None]})['service_types'],['测试'])

    def test_vector_fallback_cannot_bypass_scope_or_applicability(self):
        pages=[{'id': 1, 'title': '编码实现', 'leaf': '编码实现', 'tags': [], 'usage': ''}]
        with patch('app.services.matching._vector_fallback', return_value=[{'ref_id':1},{'ref_id':999}]):
            self.assertEqual(match_pages('软件测试',pages=pages,domain='finance',llm_judge=False),[])


if __name__ == '__main__': unittest.main()

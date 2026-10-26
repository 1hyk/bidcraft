"""技术标范围：商务评审（业绩/价格/资格）不得进入技术目录、正文与评分索引。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


class RequirementScope(unittest.TestCase):
    def test_business_scoring_is_not_technical(self):
        from app.services.requirement_matrix import response_scope

        self.assertEqual(response_scope({
            'category': '评分办法', 'title': '业绩',
            'content': '近三年相同或类似业绩，每提供1份合同得2分，需提供合同关键页及结算材料。',
        }), 'other_documents')
        self.assertEqual(response_scope({
            'category': '评分办法', 'title': '价格评分',
            'content': '以有效报价算术平均值为基准价。',
        }), 'other_documents')
        self.assertEqual(response_scope({
            'category': '评分办法', 'title': '工程化交付方案',
            'content': '方案内容必须包括交付批次规划、开发测试验证流程。',
        }), 'technical')

    def test_ip_ownership_is_business_not_technical(self):
        from app.services.requirement_matrix import response_scope
        row = {'category': '废标风险', 'title': '知识产权归属',
               'content': '本次采购履行过程中新产生的知识产权归甲方所有。（★项）'}
        self.assertEqual(response_scope(row), 'other_documents')
        # 同一条被抽成“技术要求”时同样不能进技术标。
        self.assertEqual(response_scope({**row, 'category': '技术要求'}), 'other_documents')
        self.assertEqual(response_scope({
            'category': '技术要求', 'title': '知识产权管理方案',
            'content': '交付过程中建立知识产权台账并定期更新。',
        }), 'other_documents')

    def test_requirements_text_excludes_business_items(self):
        from app.services import requirement_service as service

        rows = [
            dict(category='评分办法', title='业绩', content='需提供合同关键页及结算材料。',
                 weight=12, source_file='招标.docx', source_location='附件3', status='pending',
                 suggested_chapter=''),
            dict(category='评分办法', title='工程化交付方案', content='交付批次规划与验证流程。',
                 weight=10, source_file='招标.docx', source_location='附件3', status='pending',
                 suggested_chapter='工程化交付方案'),
        ]
        with patch.object(service, 'query', return_value=rows), \
             patch.object(service, '_lot_filter', side_effect=lambda rs, _pid: rs):
            text = service.requirements_text(31)
        self.assertIn('工程化交付方案', text)
        self.assertNotIn('业绩', text)

    def test_extraction_drops_business_scoring(self):
        from app.agents.tender_agent import _clean_requirements

        items = [
            {'category': '评分办法', 'title': '业绩',
             'content': '近三年相同或类似业绩，需提供合同关键页及结算材料。', 'weight': 12},
            {'category': '评分办法', 'title': '价格评分',
             'content': '以有效报价算术平均值为基准价。', 'weight': 35},
            {'category': '评分办法', 'title': '工程化交付方案',
             'content': '方案内容必须包括交付批次规划、开发测试验证流程。', 'weight': 10},
        ]
        rows = _clean_requirements(items)
        self.assertEqual([r['title'] for r in rows], ['工程化交付方案'])


if __name__ == '__main__':
    unittest.main()

"""历史素材插入正文后必须出现在检查页，且不能冒充本项目实证。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services import proposal_quality as quality

ASSET_ID = 'IMG-' + 'a' * 24
CAPTION = '无人机实飞测试现场（一）'


class HistoricalImageReview(unittest.TestCase):
    def run_check(self):
        body = f'现场情况如图所示。[图片占位：{CAPTION}]'
        row = {'id': 11, 'title': '现场组织', 'has_children': 0,
               'payload': {'format': 'md', 'body': body}, 'body': body}
        index = type('Index', (), {'_match': lambda self, name: (CAPTION, '/fake/a.png')})()
        with patch.object(quality, 'chapters', return_value=[row]), \
             patch.object(quality, 'query', return_value=[]), \
             patch('app.services.image_ownership.project_image_index_flat', return_value=index), \
             patch('app.services.generated_images.catalog', return_value=[]), \
             patch('app.services.image_retrieval.image_identity', return_value=ASSET_ID), \
             patch('app.services.pagination_service.view',
                   return_value={'fresh': True, 'within_tolerance': True}), \
             patch('app.services.export_dependencies.file_stamp', return_value='stamp'), \
             patch('app.services.issue_resolution.annotate',
                   side_effect=lambda project_id, issues, contexts: (issues, [], [])):
            return quality.check(20)

    def test_historical_image_needs_manual_review(self):
        report = self.run_check()
        reviews = [i for i in report['issues'] if i['kind'] == 'image_review']
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]['asset_id'], ASSET_ID)
        self.assertEqual(reviews[0]['excerpt'], CAPTION)
        self.assertIn('不得作为本项目实证', reviews[0]['message'])
        # 历史图有实体文件，不能被误报成缺图。
        self.assertFalse([i for i in report['issues'] if i['kind'] == 'missing_image'])


if __name__ == '__main__':
    unittest.main()

import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.document_checks import (
    internal_references, pending_markers, resolve_requirement_refs,
)

ROWS=[{'id':71,'title':'性能要求','source_location':'附件一 第三条'},
      {'id':72,'title':'验收要求','source_location':''},
      {'id':625,'title':'飞行试验执行方案','source_location':'附件1 2.1.1 功能要求'},
      {'id':626,'title':'任务执行报告','source_location':'附件1 2.1.1 功能要求'},
      {'id':627,'title':'任务报告','source_location':'附件1 2.1.1 功能要求'},
      {'id':632,'title':'交货期','source_location':'采购公告 2.5'},
      {'id':634,'title':'验收交付物','source_location':'附件1 2.2 验收交付物'}]


class Checks(unittest.TestCase):
    def test_pending_markers_include_parentheses_but_not_discussion(self):
        self.assertEqual(pending_markers('（待确认） (待补充：型号) 【待确认：人员】 [待补充：参数]'),
                         ['（待确认）','(待补充：型号)','【待确认：人员】','[待补充：参数]'])
        self.assertEqual(pending_markers('（待确认事项的处理流程）以及待确认后的执行安排'),[])

    def test_internal_ids_resolve_only_to_current_sources(self):
        text='依据招标要求#71及#72实施；招标要求#99。`招标要求#71`'
        result=resolve_requirement_refs(text,ROWS)
        self.assertEqual(result,
            '依据采购文件“附件一 第三条”及采购文件“验收要求”实施；'
            '【待补充：采购条款】。`招标要求#71`')

    def test_bare_ids_and_table_lists_are_resolved_and_deduplicated(self):
        text='| 1 | 飞行试验执行方案设计与测试用例 | 第一批 | 第5天 | 电子文档 | #625, #626, #627, #632 | 备注 |'
        result=resolve_requirement_refs(text,ROWS)
        self.assertIn('采购文件“附件1 2.1.1 功能要求”、采购文件“采购公告 2.5”',result)
        self.assertIn('| 采购文件“附件1 2.1.1 功能要求”、采购文件“采购公告 2.5” |',result)
        self.assertNotIn('#625',result)
        # 同一来源的七条不应在表格里重复七遍。
        self.assertEqual(result.count('采购文件“附件1 2.1.1 功能要求”'),1)

    def test_unknown_bare_id_becomes_review_marker(self):
        result=resolve_requirement_refs('详见#333与#999。',ROWS)
        self.assertEqual(result,'详见【待补充：采购条款】。')

    def test_code_and_html_attributes_are_untouched(self):
        text=('```mermaid\nstyle A fill:#f9f,stroke:#333,stroke-width:2px\n```\n'
              '```\n招标要求#71 与 #999\n```\n'
              '<span style="color:#333">正文招标要求#72</span>\n'
              '行内 `#625` 保持原样')
        result=resolve_requirement_refs(text,ROWS)
        self.assertIn('style A fill:#f9f,stroke:#333,stroke-width:2px',result)
        self.assertIn('```\n招标要求#71 与 #999\n```',result)
        self.assertIn('color:#333',result)
        self.assertIn('采购文件“验收要求”',result)
        self.assertIn('`#625`',result)

    def test_unfenced_mermaid_colors_are_not_requirement_refs(self):
        text=('flowchart TD\n'
              '    A --> B\n'
              '    style E fill:#f9f,stroke:#333,stroke-width:2px\n'
              '    style H fill:#bbf,stroke:#333,stroke-width:2px')
        self.assertEqual(internal_references(text),[])
        self.assertEqual(resolve_requirement_refs(text,ROWS),text)
        # 只在颜色位置上豁免，正文里真正的裸编号仍要处理。
        self.assertEqual(internal_references('详见#333。'),['#333'])

    def test_html_mermaid_with_raw_arrow_keeps_data_code(self):
        text=('<div data-code="flowchart TD\n'
              '    A --> B\n'
              '    style E fill:#f9f,stroke:#333,stroke-width:2px" '
              'class="mermaid-block"></div>')
        self.assertEqual(internal_references(text),[])
        self.assertEqual(resolve_requirement_refs(text,ROWS),text)

    def test_check_reads_html_body_as_html_even_when_format_says_md(self):
        from app.services import proposal_quality
        rows=[{'id':1,'title':'开发','has_children':0,'content':{
            'format':'md',
            'body':'<div data-code="flowchart TD\n A --> B\n style E fill:#333" '
                   'class="mermaid-block"></div><p>正文要求#625</p>'}}]
        with patch.object(proposal_quality,'query',return_value=rows), \
             patch('app.services.section_service.generation_node_ids',return_value={1}):
            body=next(proposal_quality.chapters(1))['body']
        self.assertNotIn('#333',body)
        self.assertIn('正文要求#625',body)

    def test_markdown_numeric_heading_is_not_a_reference(self):
        text='# 625\n### 630\n### 1. 准备阶段\n正文招标要求#71'
        result=resolve_requirement_refs(text,ROWS)
        self.assertTrue(result.startswith('# 625\n### 630\n### 1. 准备阶段\n'))
        self.assertIn('采购文件“附件一 第三条”',result)

    def test_quoted_source_followed_by_dash_ref(self):
        text='该清单精确对应采购文件“附件1 2.1.1 功能要求”-#634。'
        result=resolve_requirement_refs(text,ROWS)
        self.assertEqual(result,
            '该清单精确对应采购文件“附件1 2.1.1 功能要求”、'
            '采购文件“附件1 2.2 验收交付物”。')

    def test_detection_reports_bare_ids_but_skips_protected_text(self):
        text=('招标要求#71、#625、#333 和 `#999`，'
              '<span style="color:#2e7d32">颜色</span>，清单序号 #1')
        found=internal_references(text)
        self.assertEqual(found,['招标要求#71、#625、#333'])

    def test_resolution_is_idempotent(self):
        text='#625、#626；招标要求#71'
        once=resolve_requirement_refs(text,ROWS)
        self.assertEqual(resolve_requirement_refs(once,ROWS),once)


if __name__=='__main__':unittest.main()

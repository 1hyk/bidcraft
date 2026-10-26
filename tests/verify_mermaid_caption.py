"""图名属于正文：生成/保存/导出时只能落在图后，不能变成图前标题或图内根节点。"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services.mermaid_source import (
    _relocate_md_captions, normalize_body, normalize_html_body,
)


class MarkdownCaptionPlacement(unittest.TestCase):
    def test_caption_before_single_diagram_moves_after(self):
        body = '## 标题\n图：单图\n```mermaid\nflowchart TD\n A-->B\n```\n后文\n'
        out = _relocate_md_captions(body)
        self.assertLess(out.index('```mermaid'), out.index('图：单图'))
        self.assertLess(out.index('图：单图'), out.index('后文'))
        self.assertEqual(_relocate_md_captions(out), out)

    def test_caption_before_and_after_keeps_only_after(self):
        body = '图：前图名\n```mermaid\nflowchart TD\n A-->B\n```\n图：后图名\n后文\n'
        out = _relocate_md_captions(body)
        self.assertNotIn('前图名', out)
        self.assertIn('后图名', out)
        self.assertLess(out.index('```mermaid'), out.index('后图名'))

    def test_two_diagrams_each_with_caption_before_are_both_fixed(self):
        body = ('图：甘特\n```mermaid\ngantt\n title 交付批次\n```\n'
                '图：流程\n```mermaid\nflowchart LR\n A-->B\n```\n')
        out = _relocate_md_captions(body)
        self.assertGreater(out.index('图：甘特'), out.index(' title 交付批次'))
        self.assertGreater(out.index('图：流程'), out.index('flowchart LR'))
        self.assertLess(out.index('图：甘特'), out.index('图：流程'))

    def test_post_caption_before_next_diagram_is_not_stolen(self):
        body = ('```mermaid\nflowchart TD\n A-->B\n```\n图：第一图\n\n'
                '```mermaid\nflowchart TD\n C-->D\n```\n图：第二图\n')
        self.assertEqual(_relocate_md_captions(body), body)

    def test_split_mindmap_caption_goes_after_last_part(self):
        body = ('图：思维导图\n（关系图分图 1/2）\n```mermaid\nmindmap\n  root((r))\n    a\n```\n'
                '（关系图分图 2/2）\n```mermaid\nmindmap\n  root((r))\n    b\n```\n后文\n')
        out = _relocate_md_captions(body)
        self.assertGreater(out.index('图：思维导图'), out.index('    b'))
        self.assertLess(out.index('图：思维导图'), out.index('后文'))

    def test_normalize_body_applies_caption_placement(self):
        body = '图：保存后图名\n```mermaid\nmindmap\n  root((r))\n    a\n```\n'
        out = normalize_body(body)
        self.assertLess(out.index('```mermaid'), out.index('图：保存后图名'))


class HtmlCaptionPlacement(unittest.TestCase):
    def test_heading_before_diagram_moves_after(self):
        body = ('<p>正文</p><h3>图：结构图</h3>'
                '<div data-code="flowchart TD\n A-->B" class="mermaid-block"></div><p>后文</p>')
        out = normalize_html_body(body)
        self.assertNotIn('<h3>图：结构图</h3>', out)
        self.assertGreater(out.index('图：结构图'), out.index('</div>'))
        self.assertIn('data-figure-caption', out)

    def test_paragraph_before_diagram_moves_after(self):
        body = ('<p>正文</p><p>图：结构图</p>'
                '<div data-code="flowchart TD\n A-->B"></div><p>后文</p>')
        out = normalize_html_body(body)
        self.assertNotIn('<p>图：结构图</p>', out)
        self.assertGreater(out.index('图：结构图'), out.index('</div>'))

    def test_duplicate_heading_is_removed_when_post_caption_exists(self):
        body = ('<h3>图：结构图</h3><div data-code="flowchart TD\n A-->B"></div>'
                '<p data-figure-caption="true" style="text-align: center;">结构图</p>')
        out = normalize_html_body(body)
        self.assertEqual(out.count('结构图'), 1)
        self.assertNotIn('<h3>', out)

    def test_empty_heading_before_diagram_is_removed(self):
        body = ('<h3></h3><div data-code="flowchart TD\n A-->B"></div>'
                '<p data-figure-caption="true" style="text-align: center;">图：结构图</p>')
        out = normalize_html_body(body)
        self.assertNotIn('<h3></h3>', out)

    def test_caption_after_previous_diagram_is_not_stolen(self):
        body = ('<div data-code="flowchart TD\n A-->B"></div><p>图：第一图</p>'
                '<div data-code="flowchart TD\n C-->D"></div><p>图：第二图</p>')
        out = normalize_html_body(body)
        self.assertLess(out.index('图：第一图'), out.index('C--&gt;D') if 'C--&gt;D' in out else out.index('C-->D'))
        self.assertGreater(out.index('图：第二图'), out.index('C-->D'))
        self.assertEqual(normalize_html_body(out), out)


if __name__ == '__main__':
    unittest.main()

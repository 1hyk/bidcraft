"""导出目录编号：分级、同级重置、人工编号保护及正文不变。"""
import sys
from pathlib import Path
from io import BytesIO
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services.docx_export import _walk_nodes, _numbered_title
from app.core.docx_style import DocxStyle

for title in ['第四章 技术方案', '一、技术方案', '（二）实施方案', '1.2 测试方案', '3、服务方案']:
    assert _numbered_title(title, 1, 1) == title
assert _numbered_title('“测试”方案', 1, 10) == '十、“测试”方案'
tree = [{'id': 1, 'title': '技术方案', 'children': [
    {'id': 2, 'title': '测试方案', 'children': [{'id': 3, 'title': '功能测试'}]},
    {'id': 4, 'title': '服务方案', 'children': [{'id': 5, 'title': '响应机制'}]},
]}]
contents = {3: {'body': '正文原文', 'format': 'md'}}
doc = Document()
_walk_nodes(doc, DocxStyle(), tree, contents, 1)
buffer = BytesIO()
doc.save(buffer)
buffer.seek(0)
loaded = Document(buffer)
headings = [p.text for p in loaded.paragraphs if p.style.name.startswith('Heading')]
assert headings == ['一、技术方案', '（一）测试方案', '1.功能测试', '（二）服务方案', '1.响应机制'], headings
assert '正文原文' in [p.text for p in loaded.paragraphs]
assert tree[0]['title'] == '技术方案'
print('PASS: exported heading numbers, sibling reset, existing prefixes, unchanged content')

from app.services.docx_export import _render_html
doc = Document()
_render_html(doc, DocxStyle(), '<h2>2.4 阶段性交付物清单</h2><h3>2.4.1 批次规划</h3><h4>2.4.1.1 需求理解</h4><h4>2.4.1.2 响应方案</h4><h3>2.4.2 验收</h3>', base_level=3, drop_title='阶段性交付物清单')
assert [(p.style.name,p.text) for p in doc.paragraphs] == [
    ('Heading 4','（1）批次规划'), ('Heading 5','①需求理解'),
    ('Heading 5','②响应方案'), ('Heading 4','（2）验收')]
print('PASS: duplicate numbered title removed; nested levels retained; legacy numbers replaced')

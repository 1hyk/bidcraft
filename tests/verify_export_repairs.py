"""缓存依赖和提示词预算回归，不调用模型或业务数据库。"""
import sys,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services import export_dependencies as deps,pagination_service as pages
from app.services.image_prompt import build_prompt
from app.core.config import settings


class ExportRepairs(unittest.TestCase):
    def test_table_caption_without_blank_line_still_renders_table(self):
        from app.services import docx_export as dx
        md=('**表：典型用例清单表**\n'
            '| 用例编号 | 用例名称 |\n'
            '| :--- | :--- |\n'
            '| TC-01 | 飞机接入连通性验证 |\n'
            '后续说明文字。')
        html=dx._normalize_body({'format':'md','body':md})
        self.assertEqual(html.count('<table>'),1)
        self.assertNotIn('| 用例编号 |',html)
        self.assertIn('飞机接入连通性验证',html)
        self.assertIn('后续说明文字',html)

    def test_table_without_separator_still_renders(self):
        from app.services import docx_export as dx
        md=('表：典型用例清单表\n'
            '| 用例编号 | 用例名称 |\n'
            '| TC-01 | 飞机接入连通性验证 |\n'
            '后续说明文字。')
        html=dx._normalize_body({'format':'md','body':md})
        self.assertEqual(html.count('<table>'),1)
        self.assertNotIn('| 用例编号 |',html)
        self.assertIn('飞机接入连通性验证',html)
        self.assertIn('后续说明文字',html)

    def test_fullwidth_pipe_table_still_renders(self):
        from app.services import docx_export as dx
        md=('表：典型用例清单表\n'
            '｜用例编号｜用例名称｜\n'
            '｜---｜---｜\n'
            '｜TC-01｜飞机接入连通性验证｜')
        html=dx._normalize_body({'format':'md','body':md})
        self.assertEqual(html.count('<table>'),1)
        self.assertIn('飞机接入连通性验证',html)
        self.assertNotIn('｜用例编号｜',html)

    def test_pipe_in_plain_text_and_fence_is_not_rewritten(self):
        from app.services import docx_export as dx
        md=('一项说明：甲 | 乙。\n'
            '```\n'
            '| 这不是表格 | 也不该补分隔行 |\n'
            '```')
        html=dx._normalize_body({'format':'md','body':md})
        self.assertEqual(html.count('<table>'),0)
        self.assertIn('甲 | 乙',html)
        self.assertIn('这不是表格',html)
        self.assertNotIn('| --- |',html)

    def test_feedback_survives_long_background_and_label_cleanup(self):
        feedback='主体改红色，保留跑道和指挥中心，不要文字'
        result=build_prompt({'name':'场景图','prompt':'背景描述'*1000,'purpose':'用途','_feedback':feedback},'qwen-image-2.0')
        self.assertIn(feedback,result)
        self.assertLessEqual(len(result.encode('utf-8')),1200)
        self.assertLess(result.index(feedback),result.index('背景描述'))
        result=build_prompt({'name':'场景图','prompt':'跑道旁设指挥中心，标注中文区域名称。部署标注设备与雷达。'},'qwen-image-2.0')
        self.assertIn('指挥中心',result);self.assertIn('部署标注设备与雷达',result);self.assertNotIn('标注中文区域名称',result)

    def test_excessive_feedback_is_rejected_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError,'尚未提交付费生图'):
            build_prompt({'name':'图','_feedback':'重要修改'*1000},'qwen-image-2.0')

    def test_image_caption_and_pixels_change_dependency_stamp(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(settings,'uploads_dir',Path(temp)),patch.object(deps,'query',return_value=[]),patch('app.core.domains.domain_of_project',return_value='softtest'),patch('app.services.matching._scope_sql',return_value=('1=1',())),patch.object(deps,'renderer_version',return_value={'v':1}):
            directory=Path(temp)/'generated/project_20';directory.mkdir(parents=True)
            path=directory/'sample.png';path.write_bytes(b'original pixels')
            manifest=directory/'sample.json'
            record={'project_id':20,'asset_id':'IMG-x','caption':'旧图名','url':'/uploads/generated/project_20/sample.png','generated':True}
            manifest.write_text(json.dumps(record),encoding='utf-8')
            a=deps.project_dependencies(20,[])
            record['caption']='新图名';manifest.write_text(json.dumps(record),encoding='utf-8')
            b=deps.project_dependencies(20,[]);self.assertNotEqual(a,b)
            path.write_bytes(b'new pixels with new size')
            c=deps.project_dependencies(20,[]);self.assertNotEqual(b,c)

    def test_package_signature_ignores_core_timestamps_but_not_caption(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.docx'
            def write(title,time):
                with zipfile.ZipFile(path,'w') as z:z.writestr('word/document.xml',title);z.writestr('docProps/core.xml',time)
            write('旧图名','1');a=deps.docx_source_signature(path)
            write('旧图名','2');self.assertEqual(a,deps.docx_source_signature(path))
            write('新图名','2');self.assertNotEqual(a,deps.docx_source_signature(path))

    def test_fresh_input_cannot_be_replaced_by_mismatching_old_layout(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(settings,'uploads_dir',Path(temp)):
            source=Path(temp)/'new.docx'
            with zipfile.ZipFile(source,'w') as z:z.writestr('word/document.xml','新图名')
            directory=Path(temp)/'layout/project_20/sign';directory.mkdir(parents=True)
            (directory/'document.docx').write_bytes(b'old');(directory/'document.pdf').write_bytes(b'old')
            with patch.object(pages,'snapshot',return_value={'signature':'sign','complete':True,'words':100}),patch.object(pages,'stored',return_value={'signature':'sign','source_signature':'old'}),patch('app.services.document_layout.render',return_value={'body_pages':1}) as render,patch.object(pages,'execute'),patch.object(pages,'view',return_value={'ok':True}):
                pages.measure(20,input_path=source)
                render.assert_called_once()

    def test_snapshot_includes_renderer_and_image_metadata(self):
        from app.core.docx_style import DocxStyle
        row={'id':1,'title':'章','parent_id':None,'sort_order':0,'content':{'body':'正文'}}
        with patch('app.services.section_service.generation_node_ids',return_value={1}),patch.object(pages,'query',return_value=[row]),patch.object(pages,'query_one',return_value={'name':'项目'}),patch.object(pages,'get_project_style',return_value=DocxStyle()),patch.object(deps,'project_dependencies',return_value={'renderer':'one','caption':'旧名'}) as dep:
            a=pages.snapshot(20)['signature']
            dep.return_value={'renderer':'two','caption':'旧名'};b=pages.snapshot(20)['signature']
            dep.return_value={'renderer':'two','caption':'新名'};c=pages.snapshot(20)['signature']
            self.assertNotEqual(a,b);self.assertNotEqual(b,c)

if __name__=='__main__':unittest.main()

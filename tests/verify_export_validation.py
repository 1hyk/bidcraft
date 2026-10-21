import sys,unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.export_validation import pagination_from_footers,verify_references,current_validation,digest
from app.core.config import settings

class Validation(unittest.TestCase):
    def test_office_reads_private_input_copy(self):
        from app.services import document_layout
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);exe=root/'office.exe';exe.touch();(root/'python.exe').touch()
            source=root/'source.docx';source.write_bytes(b'original source')
            out=root/'out'
            def run(args,**kwargs):
                private=Path(args[-2])
                self.assertNotEqual(private,source)
                self.assertEqual(private.read_bytes(),b'original source')
                (out/'layout.json').write_text(json.dumps({'body_pages':1}),encoding='utf-8')
                return Mock(returncode=0)
            proc=Mock();proc.poll.return_value=0
            with patch.object(document_layout,'executable',return_value=exe),patch.object(document_layout.subprocess,'Popen',return_value=proc),patch.object(document_layout.subprocess,'run',side_effect=run),patch('pypdf.PdfReader',return_value=Mock(pages=[1])):
                self.assertEqual(document_layout.render(source,out)['total_pages'],1)
    def test_matrix_uses_current_export_but_does_not_upgrade_missing_body(self):
        from app.services import requirement_matrix as matrix
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'review.json'
            row={'id':9,'title':'响应索引','category':'编制要求','content':'须编制索引目录并填写对应证明材料的所在页码。'}
            for body_status,validation,expected in [('covered',None,'pending_stage'),('covered',{'valid':True},'covered'),('missing',{'valid':True},'missing')]:
                path.write_text(json.dumps({'signature':'s','requirements_signature':'r','items':[{'requirement_id':9,'status':body_status}]}),encoding='utf-8')
                with patch.object(matrix,'requirements',return_value=[row]),patch.object(matrix,'requirement_signature',return_value='r'),patch.object(matrix,'source_files',return_value={}),patch.object(matrix,'_path',return_value=path),patch('app.services.pagination_service.snapshot',return_value={'signature':'s'}),patch('app.services.proposal_quality.chapters',return_value=[]),patch('app.services.export_validation.current_validation',return_value=validation):
                    self.assertEqual(matrix.matrix(7)['items'][0]['status'],expected)
    def test_final_pdf_range_ignores_unnumbered_front_matter(self):
        pages,printed=pagination_from_footers(['','','- 1 -','- 2 -'],'- {page} -')
        self.assertEqual(pages,{'body_start':3,'body_end':4,'body_pages':2})
        self.assertEqual(printed,{3:1,4:2})
        self.assertEqual(pagination_from_footers(['第 1 页','第 2 页'],'第{page}页')[0]['body_pages'],2)

    def test_missing_duplicate_or_discontinuous_pages_fail_closed(self):
        for footers in [['- 1 -','- 3 -'],['- 1 -','- 1 -'],['- 1 -','','- 2 -'],['- 1 -\n- 2 -']]:
            with self.assertRaises(ValueError):pagination_from_footers(footers,'- {page} -')

    def test_reference_requires_field_and_heading_at_printed_page(self):
        expected=[(7,'待更新')]
        actual=[(7,'2')]
        headings={7:'实施方法'}
        texts=['目录实施方法','其他内容','实施方法正文']
        self.assertTrue(verify_references(expected,actual,headings,texts,{2:1,3:2})['valid'])
        self.assertFalse(verify_references(expected,[(7,'1')],headings,texts,{2:1,3:2})['valid'])
        self.assertFalse(verify_references(expected,[],headings,texts,{2:1,3:2})['valid'])

    def test_changed_content_signature_or_changed_files_invalidates_verification(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(settings,'uploads_dir',Path(tmp)):
            folder=Path(tmp)/'layout/project_7/current';folder.mkdir(parents=True)
            for kind in ['pdf','docx']:(folder/('document.'+kind)).write_bytes(kind.encode())
            validation={'valid':True,**{kind+'_sha256':digest(folder/('document.'+kind)) for kind in ['pdf','docx']}}
            with patch('app.services.pagination_service.stored',return_value={'signature':'current','export_validation':validation}):
                self.assertTrue(current_validation(7,'current'))
                self.assertIsNone(current_validation(7,'different'))
                (folder/'document.pdf').write_bytes(b'edited')
                self.assertIsNone(current_validation(7,'current'))

if __name__=='__main__':unittest.main()

import sys
import unittest
import json
import subprocess
from pathlib import Path
from unittest.mock import patch
from docx import Document

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.mermaid_source import split_mindmap,normalize_body
from app.services.evidence_roles import flag_capability_claims
from app.services import docx_export as dx
from app.core.docx_style import DocxStyle


class DenseMaps(unittest.TestCase):
    def source(self):
        return 'mindmap\n  root((任务))\n'+''.join(f'    分支{i}\n'+''.join(f'      叶{i}-{j}\n' for j in range(4)) for i in range(4))

    def test_split_preserves_exact_branches_and_parent_paths(self):
        source=self.source();pieces=split_mindmap(source)
        self.assertEqual(len(pieces),2)
        self.assertTrue(all(len(p.strip().splitlines())-1<=14 for p in pieces))
        leaves=lambda code:[line for line in code.splitlines() if line.startswith('    ')]
        self.assertEqual([line for piece in pieces for line in leaves(piece)],leaves(source))
        for piece in pieces:self.assertIn('  root((任务))',piece)
        body=normalize_body('正文\n```mermaid\n'+source+'```\n后文')
        self.assertEqual(body.count('```mermaid'),2)
        self.assertEqual(normalize_body(body),body)
        self.assertIn('后文',body)

    def test_unknown_syntax_and_large_single_branch_are_not_guessed(self):
        for source in [self.source()+'      ::icon(fa fa-book)\n',
                       'mindmap\n  root\n    分支\n'+''.join(f'      叶{i}\n' for i in range(20))]:
            self.assertEqual(split_mindmap(source),[source])

    def test_export_warmup_uses_split_sources(self):
        source=self.source()
        with patch('app.services.mermaid_render.render_batch',return_value={}) as render:
            dx._warm_mermaid({1:{'format':'md','body':'```mermaid\n'+source+'```'}})
        self.assertEqual(render.call_args.args[0],split_mindmap(source))

    def test_editor_and_export_use_same_split_for_existing_source(self):
        root=Path(__file__).resolve().parents[1]
        script="import {splitMindmap} from './frontend/src/mermaidLayout.js'; console.log(JSON.stringify(splitMindmap(JSON.parse(process.argv[1]))));"
        for source in [self.source(),self.source()+'      ::icon(fa fa-book)\n']:
            result=subprocess.run(['node','--input-type=module','-e',script,json.dumps(source)],cwd=root,capture_output=True,check=True)
            self.assertEqual(json.loads(result.stdout),split_mindmap(source))

    def test_capability_assertions_are_marked_without_changing_facts(self):
        for text in ['我方具备检验检测机构资质。','我方承诺具备相关资质，证书编号待补。',
                     '作为具备检验检测机构资质认定证书的服务方，我方承诺合规[待补充：编号]。',
                     '|本单位持有相关证书|编号待补|']:
            after=flag_capability_claims(text)
            self.assertIn('待确认',after)
            self.assertEqual(flag_capability_claims(after),after)
            if text.startswith('|'):self.assertEqual(after.count('|'),text.count('|'))
        for text in ['我方拟配置具备相关资质的人员。','招标要求我方具备相关资质。',
                     '```mermaid\nA[我方具有资质]\n```','我方不具备该资质。']:
            self.assertEqual(flag_capability_claims(text),text)


if __name__=='__main__':unittest.main()

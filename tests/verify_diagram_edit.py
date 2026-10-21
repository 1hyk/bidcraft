"""单图源码修改只返回候选，阻止脚本和模型说明文本作为源码。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services import diagram_edit


class DiagramEdit(unittest.TestCase):
    def test_candidate_uses_original_instruction_and_chapter(self):
        with patch.object(diagram_edit.llm,'chat',return_value='{"code":"flowchart LR\\n A[输入] --> B[输出]"}') as call:
            code=diagram_edit.revise('flowchart TD\n A-->B','改成横向','本章依据')
        self.assertTrue(code.startswith('flowchart LR'))
        prompt=call.call_args.args[0][1]['content']
        for text in ['flowchart TD','改成横向','本章依据']:self.assertIn(text,prompt)

    def test_dangerous_or_non_diagram_response_rejected(self):
        import json
        for code in ['这是修改后的图','flowchart LR\n click A "https://example.com"','flowchart TD\n A["<script>alert(1)</script>"]']:
            with patch.object(diagram_edit.llm,'chat',return_value=json.dumps({'code':code})):
                with self.assertRaises(ValueError):diagram_edit.revise('graph TD\n A-->B','修改')

    def test_valid_bidirectional_edge_is_not_mistaken_for_html(self):
        with patch.object(diagram_edit.llm,'chat',return_value='{"code":"flowchart LR\\n A <--> B"}'):
            self.assertIn('<-->',diagram_edit.revise('graph TD\n A-->B','双向连接'))


if __name__=='__main__':unittest.main()

import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.mermaid_source import normalize, normalize_body


class ReservedIds(unittest.TestCase):
    def test_actual_failure_and_labels(self):
        source='sequenceDiagram\n participant Link as 通信链路\n participant Plane as 新飞机\n Plane->>Link: Link 原文标签\n Note over Plane,Link: Link 注释\n Link-->>Plane: 返回\n'
        fixed=normalize(source)
        self.assertIn('participant actor_Link as 通信链路',fixed)
        self.assertIn('Plane->>actor_Link: Link 原文标签',fixed)
        self.assertIn('Note over Plane,actor_Link: Link 注释',fixed)
        self.assertEqual(normalize(fixed),fixed)

    def test_names_without_alias_and_collision(self):
        source='sequenceDiagram\n participant Link\n participant actor_Link\n activate Link\n Link->>actor_Link: test\n'
        fixed=normalize(source)
        self.assertIn('participant actor_Link_ as Link',fixed)
        self.assertIn('activate actor_Link_',fixed)
        self.assertIn('actor_Link_->>actor_Link: test',fixed)

    def test_other_diagrams_and_prose_unchanged(self):
        source='flowchart TD\n Link[通信链路] --> B[平台]'
        self.assertEqual(normalize(source),source)
        body='Link 正文\n```mermaid\nsequenceDiagram\nparticipant Link as 链路\nLink->>P1: Link 文本\n```\n```text\nLink\n```'
        fixed=normalize_body(body)
        self.assertTrue(fixed.startswith('Link 正文'))
        self.assertIn('```text\nLink\n```',fixed)
        self.assertIn('actor_Link->>P1: Link 文本',fixed)


if __name__=='__main__':unittest.main()

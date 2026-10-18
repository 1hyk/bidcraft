"""篇幅校准与流式替换回归，不调用外部模型、不连接数据库。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.length_control import fit, status
from app.services import writer_service as writer
from app.agents import writer_agent as agent


class LengthTests(unittest.TestCase):
    def test_no_target_or_in_range_has_no_extra_call(self):
        rewrite = Mock()
        self.assertEqual(fit("文" * 110, 100, rewrite), "文" * 110)
        self.assertEqual(fit("文" * 200, None, rewrite), "文" * 200)
        rewrite.assert_not_called()

    def test_over_and_under_budget(self):
        for size in (40, 180):
            self.assertEqual(len(fit("文" * size, 100, Mock(return_value="文" * 100))), 100)

    def test_failed_or_worse_rewrite_preserves_original(self):
        body = "文" * 180
        for fn in (Mock(side_effect=RuntimeError()), Mock(return_value=""),
                   Mock(return_value="文" * 220)):
            self.assertEqual(fit(body, 100, fn), body)

    def test_limited_attempts_and_best_version(self):
        rewrite = Mock(side_effect=["文" * 160, "文" * 140])
        self.assertEqual(len(fit("文" * 200, 100, rewrite)), 140)
        self.assertEqual(rewrite.call_count, 2)
        self.assertFalse(status("文" * 140, 100)["within_tolerance"])

    def test_protect_diagrams_image_names_and_tables(self):
        blocks = ["\n```mermaid\nflowchart TD\nA-->B\n```\n",
                  "\n[图片占位：现场照片（一）]\n", "\n![照片](/uploads/a.png)\n",
                  "\n|要求|响应|\n|--|--|\n|必须覆盖|满足|\n"]
        for block in blocks:
            original = "文" * 200 + block
            self.assertEqual(fit(original, 100, Mock(return_value="文" * 100)), original)
            adjusted = fit(original, 100, Mock(return_value="文" * 80 + block))
            self.assertIn(block, adjusted)
            self.assertLess(len(adjusted), len(original))


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.ctx = {"node": {"id": 1, "title": "章节"}, "outline": {}, "facts": [],
                    "knowledge": [], "siblings": [], "experiences": [], "methods": [],
                    "file_refs": [], "lot_info": "", "reqs": "", "lot_profile": {},
                    "domain": "lowalt", "img_skips": {}}
        patches = [patch.object(writer, "query_one", return_value={"title": "章节"}),
                   patch.object(writer, "_chapter_context", return_value=self.ctx),
                   patch.object(writer, "_target_for", return_value=100),
                   patch.object(writer, "write_chapter_stream", return_value=iter(["文" * 180])),
                   patch.object(writer, "persist_one"),
                   patch.object(agent, "_build_context", return_value="真实上下文"),
                   patch.object(agent, "fit_chapter_length", return_value="文" * 100)]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)

    def test_revision_replaces_editor_and_only_final_version_is_saved(self):
        events = list(writer.generate_chapter_events(4, 1))
        self.assertIn("chapter_delta", [e["type"] for e in events])
        self.assertIn("chapter_adjusting", [e["type"] for e in events])
        replacement = next(e for e in events if e["type"] == "chapter_replace")
        self.assertEqual(replacement["content"], "文" * 100)
        self.mocks[4].assert_called_once()
        self.assertEqual(self.mocks[4].call_args.args[2], replacement["content"])
        self.assertTrue(events[-1]["length"]["within_tolerance"])

    def test_disconnect_during_adjustment_preserves_draft_once(self):
        stream = writer.generate_chapter_events(4, 1)
        for event in stream:
            if event["type"] == "chapter_adjusting":
                break
        stream.close()
        self.mocks[4].assert_called_once()
        self.assertEqual(len(self.mocks[4].call_args.args[2]), 180)


if __name__ == "__main__":
    unittest.main()

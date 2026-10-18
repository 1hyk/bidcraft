"""技术章节范围回归：不连接数据库、不调用模型。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import settings
from app.core.generation_scope import select_roots, node_ids
from app.agents import orchestrate_agent as agent
from app.services import section_service, writer_service, image_ownership
from app.api import sections
from app.agents import compile_graph
from app.services import compile_service


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.scope = patch.object(settings, "generation_root_title", "技术方案")
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.rows = [
            {"id": 1, "title": "一、响应函", "parent_id": None, "sort_order": 0,
             "status": "thought_ready", "outline": {"_page_ids": [11]}},
            {"id": 4, "title": "四、技术方案", "parent_id": None, "sort_order": 3,
             "status": "thought_ready", "outline": {"_page_ids": [44]}},
            {"id": 41, "title": "第一节 总体架构", "parent_id": 4, "sort_order": 0,
             "status": "thought_ready", "outline": {"_page_ids": [45]}},
            {"id": 9, "title": "九、其他材料", "parent_id": None, "sort_order": 8,
             "status": "thought_ready", "outline": {}},
        ]

    def test_tree_preserves_number_and_children(self):
        with patch.object(section_service, "query", return_value=self.rows):
            tree = section_service.get_tree(1)
        self.assertEqual([n["title"] for n in tree], ["四、技术方案"])
        self.assertEqual(node_ids(tree), {4, 41})
        self.assertEqual(len(self.rows), 4)  # 不删除存量记录

    def test_tender_scope_is_deterministic(self):
        toc = {"chapters": [{"title": r["title"]} for r in self.rows if not r["parent_id"]]}
        with patch.object(agent.llm, "chat", side_effect=AssertionError("不应生成其他章")):
            self.assertEqual(agent.generate_toc_titles("项目", tender_toc=toc),
                             [{"title": "四、技术方案"}])
            self.assertEqual(agent.generate_toc_titles("项目"), [{"title": "技术方案"}])

    def test_pending_and_single_write_restricted(self):
        with patch.object(section_service, "query", return_value=self.rows), \
             patch.object(writer_service, "query", return_value=self.rows):
            self.assertEqual([n["id"] for n in writer_service._pending_nodes(1)], [4, 41])
            with self.assertRaisesRegex(ValueError, "编制范围"):
                writer_service._chapter_context(1, 1)

    def test_image_pool_only_uses_scope_pages(self):
        with patch.object(section_service, "query", return_value=self.rows), \
             patch.object(image_ownership, "query", return_value=self.rows), \
             patch("app.services.matching.load_pages_by_ids", return_value=[]) as load:
            nodes, _ = image_ownership._nodes_and_pages(1)
        self.assertEqual({n["id"] for n in nodes}, {4, 41})
        load.assert_called_once_with([44, 45])

    def test_exact_title_not_position_or_substring(self):
        roots = [{"id": 1, "title": "四、商务文件"},
                 {"id": 2, "title": "第五章 技术方案"},
                 {"id": 3, "title": "技术方案响应对照表"}]
        self.assertEqual(node_ids(select_roots(roots, "技术方案")), {2})
        self.assertEqual(select_roots(roots, ""), roots)

    def test_saving_visible_tree_preserves_hidden_chapters(self):
        body = sections.TreeBody(tree=[{"title": "四、技术方案", "children": [
            {"title": "第一节 总体架构", "children": []}]}])
        with patch.object(section_service, "query", return_value=self.rows), \
             patch.object(section_service, "tree_empty", return_value=False), \
             patch.object(sections, "query", return_value=self.rows), \
             patch.object(sections, "execute") as execute:
            result = sections.write_tree(1, body)
        self.assertEqual(result["removed"], 0)
        self.assertFalse(any("DELETE" in c.args[0] for c in execute.call_args_list))
        self.assertTrue(any(c.args[1][-1] == 41 for c in execute.call_args_list))

    def test_deep_sections_are_not_dropped(self):
        flat = compile_graph._flatten_toc({"chapters": [
            {"title": "四、技术方案", "children": [
                {"title": "总体架构", "children": [
                    {"title": "部署设计", "children": []}]}]}]})
        self.assertEqual([n["level"] for n in flat], [1, 2, 3])
        self.assertEqual(flat[-1]["parent"], "总体架构")

    def test_existing_flat_technical_roots_use_tender_evidence(self):
        roots = [{"id": 5, "title": "工程化交付方案"},
                 {"id": 6, "title": "响应报价表"},
                 {"id": 7, "title": "项目实施方案"}]
        self.assertEqual(node_ids(select_roots(roots, "技术方案",
                                              ["工程化交付方案", "项目实施方案"])), {5, 7})

    def test_failed_children_do_not_replace_existing_tree(self):
        state = {"project_id": 1, "project_info": "项目", "professions": [],
                 "tender_requirements": ""}
        with patch("app.core.domains.domain_of_project", return_value="lowalt"), \
             patch.object(agent, "generate_toc_titles", return_value=[{"title": "四、技术方案"}]), \
             patch.object(agent, "generate_toc_children", side_effect=RuntimeError("模型失败")), \
             patch("app.services.matching.load_page_summaries", return_value=[]), \
             patch.object(compile_service, "update_progress"), \
             patch.object(compile_service, "save_partial_toc"), \
             patch.object(section_service, "clear_tree") as clear:
            with self.assertRaisesRegex(ValueError, "原目录未替换"):
                compile_graph.node_generate_toc(state)
            clear.assert_not_called()


if __name__ == "__main__":
    unittest.main()

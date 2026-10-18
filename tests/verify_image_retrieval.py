"""独立图片召回、素材绑定和导出回归：不调用模型、不修改业务数据。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from PIL import Image
from docx import Document
from app.services import image_retrieval as retrieval, section_service
from app.services.image_ownership import ProjectImages
from app.services.docx_export import _render_html
from app.core.docx_style import DocxStyle
from app.agents.writer_agent import _build_context


class ImageRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = Path(self.tmp.name) / "a.png"
        self.b = Path(self.tmp.name) / "b.png"
        self.copy = Path(self.tmp.name) / "copy.png"
        Image.new("RGB", (20, 20), "red").save(self.a)
        Image.new("RGB", (20, 20), "blue").save(self.b)
        Image.new("RGB", (20, 20), "red").save(self.copy, compress_level=0)
        self.id_a, self.id_b = map(retrieval.image_identity, [self.a, self.b])
        self.ref = {"asset_id": self.id_a, "caption": "无人机现场照片（一）",
                    "page_id": 999, "doc_id": 3, "url": "/uploads/a.png",
                    "content": "无人机实飞测试现场照片", "source_title": "历史案例"}

    def test_identity_is_pixel_based(self):
        self.assertEqual(self.id_a, retrieval.image_identity(self.copy))
        self.assertNotEqual(self.id_a, self.id_b)

    def test_independent_recall_and_ownership(self):
        root = {"id": 1, "title": "无人机现场照片", "outline": {"need_image": True, "_page_ids": [5]},
                "children": [{"id": 2, "title": "无人机现场照片", "outline": {"need_image": True}}]}
        with patch.object(retrieval, "catalog", return_value=[self.ref]), \
             patch.object(section_service, "get_tree", return_value=[root]):
            plan = retrieval.project_plan(4)
        self.assertEqual(plan[1], [])
        self.assertEqual(plan[2][0]["page_id"], 999)  # 不在原正文命中的第 5 页中
        self.assertEqual(retrieval.candidates({"title": "无人机", "outline": {"need_image": False}}, [self.ref]), [])

    def test_catalog_scope_and_invalid_materials(self):
        images = [dict(self.ref), {"caption": "凭证", "url": "/uploads/a.png", "kind": "scan"},
                  {"caption": "空壳"}, {"caption": "丢失图片", "url": "/uploads/missing.png"}]
        with patch("app.core.domains.domain_of_project", return_value="test-domain"), \
             patch("app.services.generated_images.catalog", return_value=[]), \
             patch.object(retrieval, "query", return_value=[
                 {"id": 999, "doc_id": 3, "title": "来源", "content": {"images": images}}]) as query, \
             patch("app.services.image_vision.local_path_of", side_effect=lambda u: self.a if u == "/uploads/a.png" else None):
            assets = retrieval.catalog(4)
        self.assertEqual(len(assets), 1)
        sql, params = query.call_args.args
        self.assertIn("p.domain = %s", sql)
        self.assertIn("JOIN knowledge_doc", sql)
        self.assertIn("d.scope", sql)
        self.assertEqual(params, ("test-domain", "domain", "project", 4))

    def test_id_matches_despite_changed_name_and_preserves_original_caption(self):
        index = ProjectImages(assets={self.id_a: {**self.ref, "path": self.a}})
        self.assertTrue(index)
        self.assertEqual(index.take(f"模型改写图名|素材ID={self.id_a}"), (self.ref["caption"], self.a))
        self.assertIsNone(index.take(f"另一图名|素材ID={self.id_a}")[1])
        self.assertEqual(index.fork().take(f"照片|素材ID={self.id_a}")[1], self.a)

    def test_unknown_or_malformed_id_never_falls_back_to_same_name(self):
        index = ProjectImages({"旧图名": self.a})
        for token in ["IMG-" + "0" * 24, "IMG-invalid"]:
            self.assertIsNone(index.take("旧图名|素材ID=" + token)[1])

    def test_distinct_images_with_same_caption_are_distinguished(self):
        index = ProjectImages(assets={self.id_a: {"caption": "同名", "path": self.a},
                                      self.id_b: {"caption": "同名", "path": self.b}})
        self.assertEqual(index.take(f"同名|素材ID={self.id_a}")[1], self.a)
        self.assertEqual(index.take(f"同名|素材ID={self.id_b}")[1], self.b)

    def test_bind_and_export_use_catalog_caption(self):
        body = f"[图片占位：改写后的图名|素材ID={self.id_a}]"
        bound = retrieval.bind_references(body, [self.ref])
        self.assertIn(self.ref["caption"], bound)
        doc = Document()
        _render_html(doc, DocxStyle(), "<p>" + bound + "</p>",
                     images=ProjectImages(assets={self.id_a: {**self.ref, "path": self.a}}))
        self.assertEqual(len(doc.inline_shapes), 1)
        text = "".join(p.text for p in doc.paragraphs)
        self.assertIn(self.ref["caption"], text)
        self.assertNotIn("素材ID", text)

    def test_exact_name_can_be_bound_but_ambiguous_name_cannot(self):
        body = f"[图片占位：{self.ref['caption']}/用途]"
        self.assertIn(self.id_a, retrieval.bind_references(body, [self.ref]))
        ambiguous = [self.ref, {**self.ref, "asset_id": self.id_b}]
        self.assertEqual(retrieval.bind_references(body, ambiguous), body)
        code = "```\n" + body + "\n```"
        self.assertEqual(retrieval.bind_references(code, [self.ref]), code)

    def test_prompt_includes_binding_and_source_without_loading_other_page_text(self):
        text = _build_context({}, [], [], [], image_refs=[self.ref])
        self.assertIn("素材ID=" + self.id_a, text)
        self.assertIn("历史来源：历史案例", text)
        self.assertIn(self.ref["caption"], text)

    def test_explicit_image_needs_route_history_mermaid_and_material(self):
        for source in ["mermaid", "material"]:
            node = {"title": "无人机现场照片", "outline": {"image_needs": [
                {"name": self.ref["caption"], "source": source}]}}
            self.assertEqual(retrieval.candidates(node, [self.ref]), [])
        node["outline"]["image_needs"][0]["source"] = "history"
        self.assertEqual(retrieval.candidates(node, [self.ref])[0]["asset_id"], self.id_a)
        code = f"```\n[图片占位：照片|素材ID={self.id_a}]\n```"
        self.assertEqual(retrieval.selected_ids(code), set())

    def test_persistence_records_binding_and_keeps_previous_version(self):
        body = f"[图片占位：改写图名|素材ID={self.id_a}]"
        with patch.object(section_service, "query_one", side_effect=[{"id": 1},
              {"content": {"body": "原有正文", "format": "md"}}]), \
             patch.object(section_service, "execute") as execute:
            self.assertTrue(section_service.persist_one(4, 1, body, {"images": [self.ref]}))
        payload = json.loads(execute.call_args_list[0].args[1][0])
        self.assertEqual(payload["prev_content"], "原有正文")
        self.assertIn(self.ref["caption"], payload["body"])
        self.assertTrue(payload["used"]["images"][0]["selected"])


if __name__ == "__main__":
    unittest.main()

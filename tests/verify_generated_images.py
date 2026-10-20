"""自动生图路由、缓存、隔离与失败回退，不连接外部模型或数据库。"""
import io
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import httpx
from PIL import Image
from docx import Document
from app.core.config import settings
from app.core.docx_style import DocxStyle
from app.services import generated_images as images, image_completion as completion, image_ownership
from app.services.image_retrieval import selected_ids
from app.services.image_ownership import ProjectImages
from app.services.docx_export import _render_html
_REAL_VISUAL_VALIDATOR = images.validate_visual


class GeneratedImagesTests(unittest.TestCase):
    def setUp(self):
        visual = patch.object(images, 'validate_visual')
        visual.start()
        self.addCleanup(visual.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for field, value in {"uploads_dir": Path(self.tmp.name), "image_generation_enabled": True,
                             "image_generation_endpoint": "https://dashscope.aliyuncs.com/api/v1/test",
                             "image_generation_api_key": "test-secret",
                             "image_generation_max_per_chapter": 2}.items():
            p = patch.object(settings, field, value)
            p.start()
            self.addCleanup(p.stop)
        stream = io.BytesIO()
        Image.new("RGB", (50, 30), "blue").save(stream, format="PNG")
        self.png = stream.getvalue()
        self.need = {"name": "试验区概念效果图", "purpose": "说明空间安排", "source": "illustration"}
        self.ctx = {"outline": {"image_needs": [self.need]}, "image_refs": [], "img_skips": {}, "image_search_ok": True}

    def test_routes_real_material_and_mermaid_away_from_generation(self):
        self.assertTrue(images.eligible(self.need))
        for name in ["现场照片", "系统架构图", "软件截图", "检测报告", "测试流程图", "组织架构"]:
            self.assertFalse(images.eligible({**self.need, "name": name}))
        self.assertFalse(images.eligible({**self.need, "source": "material", "allow_generated": True}))
        self.assertFalse(images.eligible({**self.need, "source": "history"}))
        self.assertTrue(images.eligible({**self.need, "source": "history", "allow_generated": True}))

    def test_native_request_and_download_do_not_forward_credentials(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [
                    {"image": "https://dashscope-test.oss-cn-beijing.aliyuncs.com/result.png"}]}}]}})
            return httpx.Response(200, content=self.png)
        with patch("app.services.llm._client", side_effect=lambda **kw: httpx.Client(transport=httpx.MockTransport(handler))):
            data = images._request_image("画面")
        self.assertEqual(data, self.png)
        self.assertEqual(calls[0].headers["Authorization"], "Bearer test-secret")
        self.assertNotIn("authorization", calls[1].headers)
        self.assertEqual(json.loads(calls[0].content)["parameters"]["n"], 1)
        self.assertFalse(json.loads(calls[0].content)["parameters"]["watermark"])

    def test_rejected_image_is_not_published_or_charged_again(self):
        with patch.object(images,'_request_image',return_value=self.png) as request, \
             patch.object(images,'validate_visual',side_effect=images.ImageGenerationError('场景不符')):
            for _ in range(2):
                with self.assertRaisesRegex(images.ImageGenerationError,'场景不符'):
                    images.generate(4,1,self.need,'正文')
        self.assertEqual(request.call_count,1)
        self.assertEqual(images.catalog(4),[])

    def test_text_instructions_removed_without_erasing_scene(self):
        need={**self.need,'prompt':'无人机测试场地，包含跑道与指挥中心，标注中文区域名称。'}
        with patch.object(images,'_request_image',return_value=self.png) as request:
            images.generate(4,1,need,'正文')
        prompt=request.call_args.args[0]
        self.assertIn('无人机测试场地',prompt)
        self.assertIn('跑道与指挥中心',prompt)
        self.assertNotIn('标注中文',prompt)
        self.assertNotIn('generic workspace',prompt)

    def test_visual_gate_rejects_wrong_scene_text_and_uncertain_json(self):
        path=Path(self.tmp.name)/'probe.png';path.write_bytes(self.png)
        for result in [{'relevant':False,'has_text':False},
                       {'relevant':True,'has_text':True},
                       {'relevant':'true','has_text':False}, {}]:
            with patch('app.services.llm.llm.chat',return_value=json.dumps(result)):
                with self.assertRaises(images.ImageGenerationError):
                    _REAL_VISUAL_VALIDATOR(path,self.need)
        with patch('app.services.llm.llm.chat',return_value='{"relevant":true,"has_text":false}'):
            _REAL_VISUAL_VALIDATOR(path,self.need)

    def test_http_failure_is_not_retried(self):
        handler = Mock(return_value=httpx.Response(403))
        with patch("app.services.llm._client", side_effect=lambda **kw: httpx.Client(transport=httpx.MockTransport(handler))):
            with self.assertRaisesRegex(images.ImageGenerationError, "403"):
                images._request_image("画面")
        self.assertEqual(handler.call_count, 1)

    def test_generate_cache_and_project_isolation(self):
        with patch.object(images, "_request_image", return_value=self.png) as request:
            a = images.generate(4, 1, self.need, "本章内容")
            b = images.generate(4, 1, self.need, "本章内容")
        request.assert_called_once()
        self.assertEqual(a["asset_id"], b["asset_id"])
        self.assertIn("AI生成示意", a["caption"])
        self.assertEqual(len(images.catalog(4)), 1)
        self.assertEqual(images.catalog(5), [])

    def test_concurrent_same_request_only_calls_once(self):
        with patch.object(images, "_request_image", return_value=self.png) as request:
            with ThreadPoolExecutor(2) as pool:
                result = list(pool.map(lambda _: images.generate(4, 1, self.need, "正文"), range(2)))
        request.assert_called_once()
        self.assertEqual(result[0]["asset_id"], result[1]["asset_id"])

    def test_corrupt_output_does_not_create_success_manifest(self):
        with patch.object(images, "_request_image", return_value=b"not an image"):
            with self.assertRaises(images.ImageGenerationError):
                images.generate(4, 1, self.need, "正文")
        self.assertEqual(images.catalog(4), [])

    def test_historical_match_has_priority_and_keeps_existing_binding(self):
        ref = {"asset_id": "IMG-" + "1" * 24, "caption": self.need["name"], "content": "空间安排"}
        self.ctx["image_refs"] = [ref]
        marker = f"[图片占位：{ref['caption']}|素材ID={ref['asset_id']}]"
        with patch.object(completion, "generate") as generate:
            body = completion.complete(self.ctx, marker, 4, 1)
        generate.assert_not_called()
        self.assertEqual(selected_ids(body), {ref["asset_id"]})

    def test_missing_history_generates_and_can_export(self):
        with patch.object(images, "_request_image", return_value=self.png):
            body = completion.complete(self.ctx, "正文\n[图片占位：试验区概念效果图]", 4, 1)
        self.assertEqual(len(selected_ids(body)), 1)
        refs = images.catalog(4)
        doc = Document()
        _render_html(doc, DocxStyle(), "<p>" + body + "</p>",
                     images=ProjectImages(assets={a["asset_id"]: a for a in refs}))
        self.assertEqual(len(doc.inline_shapes), 1)
        self.assertIn("AI生成示意", "".join(p.text for p in doc.paragraphs))

    def test_failures_preserve_text_and_pending_image(self):
        with patch.object(completion, "generate", side_effect=images.ImageGenerationError("额度不足")):
            body = completion.complete(self.ctx, "原有正文", 4, 1)
        self.assertIn("原有正文", body)
        self.assertIn("[图片占位：试验区概念效果图]", body)
        self.assertEqual(self.ctx["image_generation_errors"][0]["reason"], "额度不足")

    def test_failed_retrieval_never_triggers_paid_generation(self):
        self.ctx["image_search_ok"] = False
        with patch.object(completion, "generate") as generate:
            completion.complete(self.ctx, "正文", 4, 1)
        generate.assert_not_called()
        self.assertTrue(self.ctx["image_generation_errors"])

    def test_quantity_cap_is_enforced(self):
        self.ctx["outline"]["image_needs"] = [{**self.need, "name": f"概念效果图{i}"} for i in range(3)]
        with patch.object(completion, "_choose_history", return_value=None), \
             patch.object(completion, "generate", return_value={"asset_id": "IMG-" + "1" * 24, "caption": "示意图"}) as generate:
            completion.complete(self.ctx, "正文", 4, 1)
        self.assertEqual(generate.call_count, 2)
        self.assertIn("数量上限", self.ctx["image_generation_errors"][0]["reason"])

    def test_illustration_ownership_works_without_knowledge_page_ids(self):
        nodes = [{"id": 1, "title": "容器", "parent_id": None, "sort_order": 0, "_page_ids": [], "image_needs": [self.need]},
                 {"id": 2, "title": "子章", "parent_id": 1, "sort_order": 0, "_page_ids": [], "image_needs": [self.need]}]
        with patch.object(image_ownership, "_nodes_and_pages", return_value=(nodes, {})):
            skips = image_ownership.project_image_skips(4)
        self.assertEqual(skips[1], {self.need["name"]: "子章"})


if __name__ == "__main__":
    unittest.main()

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

    def test_edit_sends_original_and_uses_reference_cache(self):
        import base64
        from app.services.image_retrieval import image_identity
        path = Path(self.tmp.name)/'original.png'
        path.write_bytes(self.png)
        need = {**self.need,'_feedback':'去掉黑色虚线，保留其他内容',
                '_reference_image_path':str(path),'_edit_source_asset_id':image_identity(path)}
        with patch.object(settings,'image_generation_model','qwen-image-2.0'), patch.object(images,'_request_image',return_value=self.png) as request:
            result = images.generate(4,1,need,'')
            images.generate(4,1,need,'')
        self.assertEqual(request.call_count,1)
        self.assertEqual(base64.b64decode(request.call_args.kwargs['reference_image'].split(',',1)[1]),self.png)
        self.assertIn('去掉黑色虚线',request.call_args.args[0])
        self.assertIn('不要重新设计整张图',request.call_args.args[0])
        self.assertEqual(result['generation_need']['_edit_source_asset_id'],need['_edit_source_asset_id'])
        self.assertEqual(path.read_bytes(),self.png)

    def test_edit_rejects_changed_or_unsupported_original_without_paid_call(self):
        path = Path(self.tmp.name)/'original.png';path.write_bytes(self.png)
        need = {**self.need,'_feedback':'去除虚线','_reference_image_path':str(path),'_edit_source_asset_id':'wrong'}
        with patch.object(images,'_request_image') as request, patch.object(settings,'image_generation_model','qwen-image-2.0'):
            with self.assertRaises(images.ImageGenerationError):images.generate(4,1,need,'')
            request.assert_not_called()
        with patch.object(images,'_request_image') as request, patch.object(settings,'image_generation_model','unknown'):
            with self.assertRaises(images.ImageGenerationError):images.generate(4,1,need,'')
            request.assert_not_called()

    def test_routes_real_material_and_mermaid_away_from_generation(self):
        self.assertTrue(images.eligible(self.need))
        for name in ["系统架构图", "检测报告", "测试流程图", "组织架构", "人员证书", "实测数据图"]:
            self.assertFalse(images.eligible({**self.need, "name": name}))
        self.assertTrue(images.eligible({**self.need, "source": "material", "allow_generated": True}))
        self.assertTrue(images.eligible({**self.need,"name":"现场照片","source":"material"}))
        self.assertTrue(images.eligible({**self.need,"name":"软件截图","source":"material"}))
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
        calls.clear()
        with patch("app.services.llm._client", side_effect=lambda **kw: httpx.Client(transport=httpx.MockTransport(handler))):
            images._request_image('移除虚线',reference_image='data:image/png;base64,original')
        self.assertEqual(json.loads(calls[0].content)['input']['messages'][0]['content'],
                         [{'image':'data:image/png;base64,original'},{'text':'移除虚线'}])
        self.assertFalse(json.loads(calls[0].content)["parameters"]["watermark"])

    def test_uncertain_image_is_kept_for_review_without_repeat_charge(self):
        with patch.object(images,'_request_image',return_value=self.png) as request, \
             patch.object(images,'validate_visual',side_effect=images.ImageGenerationError('场景不符')):
            a=images.generate(4,1,self.need,'正文')
            b=images.generate(4,1,self.need,'正文')
        self.assertEqual(request.call_count,1)
        self.assertFalse(a['validated'])
        self.assertTrue(a['review_pending'])
        self.assertIn('场景不符',a['review_reason'])
        self.assertNotIn('待人工核对',a['caption'])
        self.assertEqual(a['asset_id'],b['asset_id'])
        self.assertEqual(len(images.catalog(4)),1)
        # 旧版驳回缓存也保留已有像素，不重新付费。
        manifest=next(images._directory(4).glob('*.json'))
        manifest.write_text(json.dumps({'project_id':4,'rejected':True,'reason':'旧版误判'}),encoding='utf-8')
        with patch.object(images,'_request_image',side_effect=AssertionError('不应再次收费')):
            recovered=images.generate(4,1,self.need,'正文')
        self.assertTrue(recovered['review_pending'])
        self.assertEqual(recovered['asset_id'],a['asset_id'])

    def test_real_scene_substitute_is_explicitly_labelled_and_proof_stays_missing(self):
        need={**self.need,'name':'实飞现场照片','source':'material'}
        with patch.object(images,'_request_image',return_value=self.png):
            asset=images.generate(4,1,need,'正文')
        self.assertIn('非实拍',asset['caption'])
        self.assertIn('AI生成示意',asset['caption'])
        self.assertTrue(asset['review_pending'])
        with patch.object(images,'_request_image') as request:
            with self.assertRaises(images.ImageGenerationError):
                images.generate(4,2,{**need,'name':'CNAS检测报告扫描件'},'正文')
            request.assert_not_called()

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
        for result in [{'relevant':True,'has_text':False,'style_matches':True,'physical_plausible':True,'has_schematic_overlay':True},
                       {'relevant':True,'has_text':False,'style_matches':True,'physical_plausible':True}]:
            with patch('app.services.llm.llm.chat',return_value=json.dumps(result)):
                with self.assertRaises(images.ImageGenerationError):
                    _REAL_VISUAL_VALIDATOR(path,self.need)
        with patch('app.services.llm.llm.chat',return_value='{"relevant":true,"has_text":false,"style_matches":true,"physical_plausible":true,"has_schematic_overlay":false}'):
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
        with patch.object(completion, "generate") as generate, patch('app.services.llm.llm.chat',return_value=json.dumps({'asset_id':ref['asset_id']})):
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
        captions = "".join(p.text for p in doc.paragraphs)
        self.assertIn("试验区概念效果图", captions)
        self.assertNotIn("AI生成示意", captions)

    def test_failures_preserve_text_without_empty_placeholder(self):
        with patch.object(completion, "generate", side_effect=images.ImageGenerationError("额度不足")):
            body = completion.complete(self.ctx, "原有正文", 4, 1)
        self.assertIn("原有正文", body)
        self.assertNotIn("[图片占位：试验区概念效果图]", body)
        self.assertEqual(self.ctx["image_generation_errors"][0]["reason"], "额度不足")

    def test_failed_placeholder_cleanup_does_not_remove_real_material_or_bound_image(self):
        body = '已有说明。\n\n[图片占位：场景图]\n\n[图片占位：证书原件]\n\n[图片占位：场景图|素材ID=IMG-123]\n\n```text\n[图片占位：场景图]\n```'
        result, anchor = completion.remove_failed_placeholder(body,'场景图')
        self.assertIn('[图片占位：证书原件]',result)
        self.assertIn('[图片占位：场景图|素材ID=IMG-123]',result)
        self.assertIn('```text\n[图片占位：场景图]\n```',result)
        self.assertEqual(anchor,'已有说明。')

    def test_failed_retrieval_generates_illustration_and_records_review(self):
        self.ctx["image_search_ok"] = False
        with patch.object(completion,"generate",return_value={'asset_id':'IMG-'+'1'*24,'caption':'AI生成示意图'}) as generate:
            body=completion.complete(self.ctx,"正文",4,1)
        generate.assert_called_once()
        self.assertTrue(selected_ids(body))
        self.assertTrue(self.ctx['image_review_warnings'])
        self.assertEqual(self.ctx['image_generation_errors'],[])

    def test_unavailable_explicit_history_does_not_generate_replacement(self):
        asset_id = "IMG-" + "9" * 24
        self.ctx["outline"]["image_needs"] = [{**self.need, "asset_id": asset_id}]
        with patch.object(completion, "generate") as generate:
            body = completion.complete(self.ctx, "[图片占位：试验区概念效果图]", 4, 1)
        generate.assert_not_called()
        self.assertNotIn("图片占位", body)
        self.assertIn("历史素材当前不可用", self.ctx["image_generation_errors"][0]["reason"])
        self.assertEqual(self.ctx["image_review_warnings"], [])

    def test_history_verification_failure_does_not_generate_replacement(self):
        self.ctx["image_refs"] = [{"asset_id": "IMG-" + "1" * 24, "caption": self.need["name"], "content": "空间安排"}]
        with patch("app.services.llm.llm.chat", side_effect=RuntimeError("模型超时")), \
             patch.object(completion, "generate") as generate:
            body = completion.complete(self.ctx, "[图片占位：试验区概念效果图]", 4, 1)
        generate.assert_not_called()
        self.assertNotIn("图片占位", body)
        self.assertIn("历史图片核验失败", self.ctx["image_generation_errors"][0]["reason"])

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

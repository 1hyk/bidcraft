"""真实模型 + 历史图片 ID 绑定验证；不改项目正文。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from docx import Document
from app.agents.writer_agent import write_chapter
from app.core.config import settings
from app.core.docx_style import DocxStyle
from app.core.domains import domain_of_project
from app.services.image_retrieval import catalog, candidates, bind_references, selected_ids
from app.services.image_ownership import ProjectImages
from app.services.docx_export import _normalize_body, _render_html


def main():
    project_id = int(sys.argv[1])
    assets = catalog(project_id)
    photo = next(a for a in assets if a.get("kind") == "photo")
    outline = {"chapter_title": "历史测试现场参考示例", "need_image": True,
               "thinking": "本节只说明历史现场照片中的环境与组织方式作为参考示例；"
                           "必须引用候选中的照片一次，明确是历史资料，不声称本项目已开展活动。",
               "image_needs": [{"name": photo["caption"], "source": "history", "purpose": "历史现场参考"}]}
    refs = candidates({"title": outline["chapter_title"], "outline": outline}, assets, limit=1)
    print("独立召回历史照片，验证模型引用…", flush=True)
    body = write_chapter(outline, knowledge=[], image_refs=refs, target_words=300,
                         domain=domain_of_project(project_id))
    body = bind_references(body, refs)
    chosen = selected_ids(body)
    asset_map = {a["asset_id"]: a for a in assets}
    doc = Document()
    _render_html(doc, DocxStyle(), _normalize_body({"body": body, "format": "md"}),
                 images=ProjectImages(assets=asset_map))
    text = "\n".join(p.text for p in doc.paragraphs)
    report = {"project_id": project_id, "text_knowledge_pages": 0,
              "selected_image_ids": sorted(chosen), "exported_images": len(doc.inline_shapes),
              "original_captions_preserved": all(asset_map[i]["caption"] in text for i in chosen),
              "stored_sections_changed": False}
    out = settings.uploads_dir / "_verification"
    out.mkdir(exist_ok=True)
    (out / "image_binding_smoke.md").write_text(body, encoding="utf-8")
    (out / "image_binding_smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if len(chosen) != 1 or len(doc.inline_shapes) != 1 or not report["original_captions_preserved"]:
        raise RuntimeError("真实图片引用或导出验证失败")


if __name__ == "__main__":
    main()

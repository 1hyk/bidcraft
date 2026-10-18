"""真实缺图补齐验证：历史检索 → 自动生图 → 本地持久化 → 导出插图，不改章节正文。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from docx import Document
from app.core.config import settings
from app.core.docx_style import DocxStyle
from app.services.image_retrieval import catalog, candidates, selected_ids
from app.services.image_completion import complete
from app.services.image_ownership import project_image_index_flat
from app.services.docx_export import _normalize_body, _render_html


def main():
    project_id = int(sys.argv[1])
    need = {"name": "综合测试作业区空间布置概念效果图", "source": "illustration",
            "purpose": "以概念效果图展示作业区、设备区、通道与观摩区的空间分离",
            "prompt": "等距三维概念插画，蓝灰白配色。宽敞的室内测试空间，几张操作台、设备柜、"
                      "地面安全分区和独立通道。无人员、无品牌、不放数值，不绘制流程箭头，不要大量文字。"}
    outline = {"chapter_title": "测试空间布置示意", "need_image": True, "image_needs": [need]}
    refs = candidates({"title": outline["chapter_title"], "outline": outline}, catalog(project_id))
    print(f"已检索 {len(refs)} 张历史候选；核验匹配并自动补图…", flush=True)
    ctx = {"outline": outline, "image_refs": refs, "img_skips": {}, "image_search_ok": True}
    body = "## 测试空间布置示意\n\n本图仅用于展示拟采用的空间组织方式，不代表已建成场地。\n\n"
    body += "操作区、设备区与观摩区分开布置，保留独立通道，避免不同活动相互干扰。\n\n"
    body += "[图片占位：综合测试作业区空间布置概念效果图]\n"
    body = complete(ctx, body, project_id, 0)
    ids = selected_ids(body)
    index = project_image_index_flat(project_id)
    doc = Document()
    _render_html(doc, DocxStyle(), _normalize_body({"body": body, "format": "md"}), images=index)
    chosen = [a for a in ctx["image_refs"] if a["asset_id"] in ids]
    report = {"project_id": project_id, "selected_images": [
        {"caption": a["caption"], "generated": a.get("generated", False), "url": a.get("url")} for a in chosen],
        "exported_images": len(doc.inline_shapes), "errors": ctx.get("image_generation_errors"),
        "stored_sections_changed": False}
    out = settings.uploads_dir / "_verification"
    out.mkdir(exist_ok=True)
    (out / "auto_illustration_smoke.md").write_text(body, encoding="utf-8")
    (out / "auto_illustration_smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not chosen or len(doc.inline_shapes) != 1 or report["errors"]:
        raise RuntimeError("缺图自动生成与导出验证未通过")


if __name__ == "__main__":
    main()

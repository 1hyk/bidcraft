"""用真实项目上下文验证生成，不覆盖原章节。输出仅写 uploads/_verification。"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import settings
from app.core.page_plan import count_words
from app.services.writer_service import _chapter_context
from app.agents.writer_agent import write_chapter
from app.services.image_ownership import project_image_index_flat
from app.services.mermaid_render import render_batch
from app.services.length_control import status


def main():
    project_id, node_id = map(int, sys.argv[1:3])
    print("组装真实章节上下文…", flush=True)
    ctx = _chapter_context(project_id, node_id)
    print("取材完成，验证正文生成…", flush=True)
    body = write_chapter(
        outline=ctx["outline"], facts=ctx["facts"], knowledge=ctx["knowledge"],
        siblings=ctx["siblings"], experiences=ctx["experiences"], methods=ctx["methods"],
        file_refs=ctx["file_refs"], lot_info=ctx["lot_info"], reqs=ctx["reqs"],
        lot_profile=ctx["lot_profile"], target_words=900, domain=ctx["domain"],
        skip_images=ctx["img_skips"],
        image_refs=ctx.get("image_refs"),
    )
    out = settings.uploads_dir / "_verification"
    out.mkdir(exist_ok=True)
    stem = f"project{project_id}_chapter{node_id}_smoke"
    (out / (stem + ".md")).write_text(body, encoding="utf-8")
    sources = re.findall(r"```mermaid\s*\n(.*?)```", body, re.S)
    print(f"正文完成：{count_words(body)} 字，Mermaid {len(sources)} 张，验证渲染…", flush=True)
    rendered = render_batch(sources) if sources else {}
    index = project_image_index_flat(project_id)
    placeholders = re.findall(r"\[图片占位[：:]\s*([^\]]+)\]", body)
    report = {
        "project_id": project_id, "node_id": node_id, "title": ctx["node"]["title"],
        "target_words": 900, "actual_words": count_words(body),
        "within_word_tolerance": status(body, 900)["within_tolerance"],
        "mermaid_count": len(sources), "rendered_count": len(rendered),
        "placeholders": [{"name": name, "matched": index._match(name)[1] is not None}
                         for name in placeholders],
        "original_content_changed": False,
    }
    (out / (stem + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if (not body.strip() or len(rendered) != len(set(sources))
            or not report["within_word_tolerance"]):
        raise RuntimeError("生成、篇幅校准或 Mermaid 渲染验证失败")


if __name__ == "__main__":
    main()

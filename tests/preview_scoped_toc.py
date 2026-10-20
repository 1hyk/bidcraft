"""只读项目输入、调用真实模型预览目录；不替换目录、不写正文或会话。"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.compile_graph import node_generate_toc
from app.services import compile_service, section_service

if __name__ == "__main__":
    project_id = int(sys.argv[1])
    state = compile_service.assemble_inputs(project_id)
    state["project_id"] = project_id
    with patch.object(compile_service, "update_progress"), \
         patch.object(compile_service, "save_partial_toc"), \
         patch.object(section_service, "replace_tree", return_value=0), \
         patch.object(section_service, "clear_tree"), \
         patch.object(section_service, "save_tree", return_value=0):
        result = node_generate_toc(state)
    destination = ROOT / "uploads" / "_verification" / f"toc_preview_{project_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    roots = result["toc"]["chapters"]
    print(json.dumps({"preview": str(destination), "major_chapters": sum(
        len(r.get("children") or []) for r in roots), "sections": sum(
        len(c.get("children") or []) for r in roots for c in r.get("children") or [])}))

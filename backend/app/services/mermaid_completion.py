"""补写遗留的关系图占位，只生成源码；渲染仍在导出期批量进行。"""
import json
import re

from app.services.image_planning import RELATION, TABLE


def complete(body: str, skips=()) -> str:
    from app.services.generated_images import _EVIDENCE
    from app.services.llm import llm
    from app.agents.json_util import extract_json

    names = list(dict.fromkeys(m[1].strip() for m in re.finditer(
        r'\[图片占位[：:]\s*([^\]]+)\]', body)
        if '素材ID' not in m[1] and RELATION.search(m[1])
        and not TABLE.search(m[1]) and not _EVIDENCE.search(m[1]) and m[1].strip() not in skips))
    for name in names[:3]:
        try:
            raw = llm.chat([
                {'role':'system','content':'根据本章已有做法补绘关系图。只输出JSON {"code":"Mermaid源码"}。'
                 '不补充未经正文支持的人员、数字或事实。节点文字用双引号，选择合适图型。'},
                {'role':'user','content':json.dumps({'图名':name,'本章正文':body},ensure_ascii=False)}
            ], temperature=0, max_tokens=3500)
            code = str(extract_json(raw).get('code') or '').strip()
            if not re.match(r'^(?:flowchart|graph|sequenceDiagram|gantt|classDiagram|stateDiagram-v2|erDiagram|mindmap)\b',code):
                continue
            if '```' in code or re.search(r'https?://|javascript:|<script',code,re.I):
                continue
            marker = re.compile(r'\[图片占位[：:]\s*'+re.escape(name)+r'\]')
            body = marker.sub(lambda m: '```mermaid\n'+code+'\n```\n\n图：'+name, body, count=1)
        except Exception as exc:
            print(f'[关系图补绘] {name} 未完成：{type(exc).__name__}')
    return body

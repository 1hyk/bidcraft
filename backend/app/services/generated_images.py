"""缺图自动生成：本项目概念示意图，使用千问原生图像接口。

历史匹配优先；生成件只属于当前项目，独立落盘，不混入领域历史知识库。
"""
import hashlib
import io
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
_slots = threading.BoundedSemaphore(max(1, settings.image_generation_concurrency))
_EVIDENCE = re.compile(r"现场照片|实拍|真实照片|截图|界面|扫描件|证照|证书|资质|检测报告|检测证明|实测|签字|盖章")
from app.services.image_planning import RELATION as _RELATION, TABLE as _TABLE


class ImageGenerationError(RuntimeError):
    pass


def eligible(need: dict) -> bool:
    if not isinstance(need, dict):
        return False
    source = need.get("source")
    if source != "illustration" and not (source == "history" and need.get("allow_generated") is True):
        return False
    text = str(need.get("name") or "") + " " + str(need.get("purpose") or "")
    return bool(str(need.get("name") or "").strip()) and not _EVIDENCE.search(text) and not _RELATION.search(text) and not _TABLE.search(str(need.get('name') or ''))


def _connection() -> tuple[str, str]:
    llm = urlsplit(settings.llm_base_url)
    endpoint = settings.image_generation_endpoint.strip()
    if not endpoint:
        host = llm.hostname or ""
        if not (host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
                or host.endswith(".maas.aliyuncs.com")):
            raise ImageGenerationError("请配置 IMAGE_GENERATION_ENDPOINT 和图像服务 Key")
        endpoint = urlunsplit(("https", llm.netloc, "/api/v1/services/aigc/multimodal-generation/generation", "", ""))
    target = urlsplit(endpoint)
    if target.scheme != "https" or not target.hostname or target.username or target.password or target.query:
        raise ImageGenerationError("图像接口地址必须是无凭据参数的 HTTPS 地址")
    key = settings.image_generation_api_key.strip()
    if not key and target.hostname == llm.hostname:
        key = settings.llm_api_key
    if not key:
        raise ImageGenerationError("缺少 IMAGE_GENERATION_API_KEY")
    return endpoint, key


def _request_image(prompt: str) -> bytes:
    from app.services.llm import _client

    endpoint, key = _connection()
    payload = {"model": settings.image_generation_model,
               "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
               "parameters": {"size": settings.image_generation_size, "n": 1,
                              "prompt_extend": False, "watermark": False}}
    # 付费提交不自动重试，避免网络超时后重复提交、重复计费。
    try:
        with _client(timeout=settings.image_generation_timeout) as client:
            response = client.post(endpoint, headers={"Authorization": "Bearer " + key}, json=payload)
            if response.status_code != 200:
                raise ImageGenerationError(f"图像服务 HTTP {response.status_code}，请检查模型权限、Key 和余额")
            data = response.json()
            parts = data.get("output", {}).get("choices", [{}])[0].get("message", {}).get("content", [])
            url = next((p["image"] for p in parts if p.get("image")), "")
            address = urlsplit(url)
            if (address.scheme != "https" or not (address.hostname or "").endswith(".aliyuncs.com")
                    or address.username or address.password):
                raise ImageGenerationError("图像服务未返回有效的百炼图片地址")
        # 临时下载地址不携带 Authorization，及时保存以避免链接到期。
        with _client(timeout=60) as client, client.stream("GET", url) as response:
            if response.status_code != 200:
                raise ImageGenerationError(f"生成图片下载失败 HTTP {response.status_code}")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise ImageGenerationError("生成图片超过 20 MB")
                chunks.append(chunk)
            return b"".join(chunks)
    except ImageGenerationError:
        raise
    except Exception as exc:
        raise ImageGenerationError(f"图像服务请求失败（{type(exc).__name__}）") from None


def _directory(project_id: int) -> Path:
    if int(project_id) <= 0:
        raise ImageGenerationError("项目编号无效")
    return settings.uploads_dir / "generated" / f"project_{int(project_id)}"


def catalog(project_id: int) -> list[dict]:
    from app.services.image_retrieval import image_identity

    root = _directory(project_id)
    out = []
    for manifest in sorted(root.glob("*.json")):
        try:
            record = json.loads(manifest.read_text(encoding="utf-8"))
            if record.get("project_id") != int(project_id):
                continue
            path = root / (manifest.stem + ".png")
            if not path.is_file() or record.get("asset_id") != image_identity(path):
                continue
            record["path"] = path
            out.append(record)
        except Exception:
            continue
    return out


def validate_visual(path: Path, need: dict) -> None:
    """看实际像素核对主题与文字；失败保留待补，不能只因接口成功就插图。"""
    import base64
    from app.services.llm import llm
    from app.agents.json_util import extract_json
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert('RGB')
        im.thumbnail((1280, 1280))
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=90)
    result = extract_json(llm.chat([{'role':'user','content':[
        {'type':'text','text':'核查这张生成概念插图是否准确表现下述场景，不接受仅色调相似。'
         '同时检查有无任何可见文字、乱码、字母标签或水印。只输出JSON：'
         '{"relevant":true,"has_text":false,"reason":""}。需求：'
         +str(need.get('name') or '')+'；'+str(need.get('purpose') or '')},
        {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+
         base64.b64encode(buf.getvalue()).decode()}}]}], model=settings.vision_model,
         temperature=0, max_tokens=500, timeout=60))
    if result.get('relevant') is not True or result.get('has_text') is not False:
        raise ImageGenerationError('生成图未通过主题/文字核验：'+str(result.get('reason') or '需重新检查')[:150])


def generate(project_id: int, node_id: int, need: dict, body: str) -> dict:
    from PIL import Image
    from app.services.image_retrieval import image_identity

    if not settings.image_generation_enabled:
        raise ImageGenerationError("自动生图已关闭")
    if not eligible(need):
        raise ImageGenerationError("此配图需要真实材料或应由 Mermaid 绘制")
    caption = str(need["name"]).strip()
    visual_prompt = (str(need.get('prompt') or '')+'。'+str(need.get('purpose') or ''))[:1000]
    visual_prompt = re.sub(r"[^，,。；\n]*(?:标注|文字|标签|水印|logo)[^，,。；\n]*(?:[，,。；\n]|$)",
                           "", visual_prompt, flags=re.I).strip()
    # 引号包裹的链路名等常被模型直接写进图；去掉这种标签指令，保留独立场景描述。
    visual_prompt = re.sub(r'[^，,。；\n]*[‘“][^’”]+[’”][^，,。；\n]*(?:[，,。；\n]|$)',
                           '',visual_prompt).strip()
    prompt = (
        'One clean conceptual illustration, white background, blue-gray objects, horizontal composition. '
        'Only depict visible objects and spatial arrangements, not a presentation slide or an infographic. '
        + (visual_prompt or ('Visual subject only, do not print these words: '+caption))
        + '\nOnly the illustration itself. No writing, no title, no subtitles, no paragraphs, no tables, '
        'no letters, no numbers, no charts, no diagrams, no labels, no logos, no real certificates or signatures. '
        'This is a proposed concept, never a photograph or evidence of an existing facility.'
    )
    version = json.dumps({"model": settings.image_generation_model, "size": settings.image_generation_size,
                          "validation_version": 1,
                          "endpoint": _connection()[0], "prompt": prompt, "caption": caption,
                          "context": body[:1800]}, ensure_ascii=False, sort_keys=True)
    cache_key = hashlib.sha256(version.encode()).hexdigest()
    root = _directory(project_id)
    with _guard:
        lock = _locks.setdefault(str(root / cache_key), threading.Lock())
    with lock:
        path, manifest = root / (cache_key + ".png"), root / (cache_key + ".json")
        if path.is_file() and manifest.is_file():
            try:
                record = json.loads(manifest.read_text(encoding="utf-8"))
                if record.get('rejected'):
                    raise ImageGenerationError(record.get('reason') or '该生成图未通过核验，未重复付费提交')
                if record.get("asset_id") == image_identity(path):
                    return record
            except ImageGenerationError:
                raise
            except Exception:
                pass
        with _slots:
            data = _request_image(prompt)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 16_000_000:
                    raise ValueError("图像尺寸过大")
                rgb = Image.new("RGB", image.size, "white")
                rgba = image.convert("RGBA")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
                root.mkdir(parents=True, exist_ok=True)
                temp = root / (cache_key + ".tmp")
                rgb.save(temp, format="PNG")
                temp.replace(path)
        except Exception as exc:
            raise ImageGenerationError(f"生成结果不是有效图片（{type(exc).__name__}）") from None
        try:
            validate_visual(path, need)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ImageGenerationError) else '生成图视觉核验暂不可用，需人工检查'
            manifest.write_text(json.dumps({'project_id':int(project_id), 'rejected':True,
                                           'reason':reason},ensure_ascii=False),encoding='utf-8')
            raise ImageGenerationError(reason) from None
        record = {"project_id": int(project_id), "node_id": int(node_id), "asset_id": image_identity(path),
                  "validated": True,
                  "caption": caption + "（AI生成示意）", "kind": "illustration", "generated": True,
                  "url": "/uploads/" + path.relative_to(settings.uploads_dir).as_posix(),
                  "content": str(need.get("purpose") or "概念示意插图"),
                  "source_title": "AI生成示意图", "page_id": 0, "doc_id": None,
                  "model": settings.image_generation_model, "prompt": prompt}
        temp = root / (cache_key + ".json.tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(manifest)
        return record

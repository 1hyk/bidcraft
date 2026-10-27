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
from app.services.image_prompt import visual_style

VISUAL_CHECK_VERSION = 3

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
_slots = threading.BoundedSemaphore(max(1, settings.image_generation_concurrency))
_EVIDENCE = re.compile(r"现场照片|实拍|真实照片|截图|界面|扫描件|证照|证书|资质|检测报告|检测证明|实测|签字|盖章")
_PROOF = re.compile(r'扫描件|证照|证书|资质|检测报告|检测证明|实测|签字|盖章|原件|真实证明|采购人提供|客户提供')
from app.services.image_planning import RELATION as _RELATION, TABLE as _TABLE, DATA_CHART


class ImageGenerationError(RuntimeError):
    pass


def eligible(need: dict) -> bool:
    if not isinstance(need, dict):
        return False
    from app.services.image_planning import representation, legacy_tabular
    if legacy_tabular(need):
        return False
    mode = representation(need)
    if mode in {'material','table','gantt','mermaid'}:
        return False
    source = need.get("source")
    if source not in {"illustration", "material"} and not (source == "history" and need.get("allow_generated") is True):
        return False
    text = str(need.get("name") or "") + " " + str(need.get("purpose") or "")
    if _PROOF.search(text):
        return False
    # 场景、实物和界面可以先提供明确标识的概念替代，不能冒充实拍或现有系统。
    return bool(str(need.get("name") or "").strip()) and (mode == 'illustration' or (not _RELATION.search(text) and not _TABLE.search(str(need.get('name') or '')) and not DATA_CHART.search(str(need.get('name') or ''))))


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


def _request_image(prompt: str, size: str | None = None, reference_image: str | None = None) -> bytes:
    from app.services.llm import _client

    endpoint, key = _connection()
    content = ([{"image":reference_image}] if reference_image else []) + [{"text":prompt}]
    payload = {"model": settings.image_generation_model,
               "input": {"messages": [{"role": "user", "content": content}]},
               "parameters": {"size": size or settings.image_generation_size, "n": 1,
                              "prompt_extend": False, "watermark": False}}
    # 付费提交不自动重试，避免网络超时后重复提交、重复计费。
    try:
        with _client(timeout=settings.image_generation_timeout) as client:
            response = client.post(endpoint, headers={"Authorization": "Bearer " + key}, json=payload)
            if response.status_code != 200:
                try:
                    code = str(response.json().get('code') or '')
                    code = code if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',code) else ''
                except (ValueError, AttributeError):
                    code = ''
                raise ImageGenerationError(f"图像服务 HTTP {response.status_code}" + (f"（{code}）" if code else '') + '；请按服务错误码核对请求参数、权限或额度')
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


def review_reason(asset):
    """自动主题检查不代替人工验收；老图在读侧补提示，不批改历史文件。"""
    if not asset.get('generated'):return str(asset.get('review_reason') or '')
    if asset.get('review_reason'):return str(asset['review_reason'])
    if asset.get('visual_check_version',0)<VISUAL_CHECK_VERSION:
        return '旧生成图未检查风格、透视和实物合理性，请人工核对；主题文字通过不代表画面合格'
    return 'AI生成图需人工确认风格、透视与实物合理性；自动检查通过不等于人工验收'


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
            if record.get("generated"):
                record.update(review_pending=True,review_reason=review_reason(record))
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
    style = visual_style(need)
    allow_text = bool(need.get('allow_text')) and style != 'photorealistic'
    if allow_text:
        check = ('核查这张AI生成的流程图/示意图是否符合需求。节点上的中文标签是必要内容：'
                 '检查标签是否清晰可读、无乱码和错别字，是否与需求中的步骤名称对应，流程顺序和箭头方向是否符合需求；'
                 '不要因为画面存在文字就判失败。同时检查有无无关文字、字母编号、图题或水印。'
                 '画面风格必须符合：' + style + '。只输出JSON：'
                 '{"relevant":true,"has_text":true,"style_matches":true,"physical_plausible":true,"has_schematic_overlay":false,"reason":""}。需求：')
    else:
        check = ('核查这张AI生成场景是否符合需求。不接受仅色调相似；检查透视、比例、物体结构、摆放与空间关系，拒绝拼贴、不合理悬浮或设备变形。'
                 '画面风格必须符合：' + style + '。'
                 '若为 photorealistic，还要检查有无图解叠加：虚线、箭头、引线、圈注、测量线、半透明覆盖扇区、'
                 '悬浮图标，或只为表示关系而画的连线/光束，有则 has_schematic_overlay=true。'
                 '同时检查有无任何可见文字、乱码、字母标签或水印。只输出JSON：'
                 '{"relevant":true,"has_text":false,"style_matches":true,"physical_plausible":true,"has_schematic_overlay":false,"reason":""}。需求：')
    result = extract_json(llm.chat([{'role':'user','content':[
        {'type':'text','text': check
         +str(need.get('name') or '')+'；'+str(need.get('purpose') or '')},
        {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+
         base64.b64encode(buf.getvalue()).decode()}}]}], model=settings.vision_model,
         temperature=0, max_tokens=500, timeout=60))
    # 图解叠加只对写实图是缺陷；示意图本来就该有分区色块、边界和航线线条。
    overlay_bad = style == 'photorealistic' and result.get('has_schematic_overlay') is not False
    text_bad = result.get('has_text') is not False and not allow_text
    # 流程图不是实物场景，不适用“实物合理性”；写实和其他空间示意图仍然检查。
    physical_bad = not allow_text and result.get('physical_plausible') is not True
    if (result.get('relevant') is not True or text_bad
            or result.get('style_matches') is not True or physical_bad
            or overlay_bad):
        raise ImageGenerationError('生成图未通过主题、文字、风格、图解叠加或实物合理性核验：'+str(result.get('reason') or '需重新检查')[:150])


def generate(project_id: int, node_id: int, need: dict, body: str) -> dict:
    from PIL import Image
    from app.services.image_retrieval import image_identity

    if not settings.image_generation_enabled:
        raise ImageGenerationError("自动生图已关闭")
    if not eligible(need):
        raise ImageGenerationError("此配图需要真实材料或应由 Mermaid 绘制")
    caption = str(need["name"]).strip()
    size = need.get('_generation_size') or settings.image_generation_size
    substitute = need.get('source') == 'material' or bool(_EVIDENCE.search(caption))
    if substitute:
        caption = re.sub(r'真实照片|现场照片|实拍|照片|截图', '概念示意', caption)
        caption += '（非实拍、非真实系统记录）'
    from app.services.image_prompt import build_prompt
    reference_image = None
    reference_hash = None
    if need.get('_reference_image_path'):
        if not settings.image_generation_model.startswith('qwen-image-2.0'):
            raise ImageGenerationError('当前图像模型尚未接入原图编辑，未退回文字重画；请更换支持的编辑模型')
        import base64
        reference_path = Path(need['_reference_image_path']).resolve()
        if not reference_path.is_relative_to(Path(settings.uploads_dir).resolve()):
            raise ImageGenerationError('原图不在素材目录，未发送编辑请求')
        if not reference_path.is_file() or reference_path.stat().st_size > 10*1024*1024:
            raise ImageGenerationError('原图不存在或超过编辑接口10MB限制')
        reference_hash = image_identity(reference_path)
        if reference_hash != need.get('_edit_source_asset_id'):
            raise ImageGenerationError('原图内容已经变化，请刷新素材后重新编辑')
        # 保留原图像素；仅为API传输编码，不另做图像修补。
        with Image.open(reference_path) as reference:
            mime = Image.MIME.get(reference.format)
        if mime not in {'image/png','image/jpeg','image/webp','image/bmp','image/tiff','image/gif'}:
            raise ImageGenerationError('原图格式不支持编辑')
        reference_image = 'data:'+mime+';base64,'+base64.b64encode(reference_path.read_bytes()).decode()
    try: prompt = build_edit_prompt(need) if reference_image else build_prompt(need,settings.image_generation_model)
    except ValueError as exc: raise ImageGenerationError(str(exc)) from None
    version = json.dumps({"model": settings.image_generation_model, "size": size,
                          "validation_version": VISUAL_CHECK_VERSION,
                          "endpoint": _connection()[0], "prompt": prompt, "caption": caption,
                          "context": body[:1800]}, ensure_ascii=False, sort_keys=True)
    if reference_hash:
        version += '|edit-v1|' + reference_hash
    if need.get('_request_id'):
        version += str(need['_request_id'])
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
                    # 已付费且文件有效的旧驳回图转为人工待核对，绝不再次收费生成。
                    record = _record(project_id,node_id,path,caption,need,prompt,False,
                                     record.get('reason') or '旧核验未通过，需人工检查',substitute)
                    manifest.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
                    return record
                if record.get("asset_id") == image_identity(path):
                    return record
            except ImageGenerationError:
                raise
            except Exception:
                pass
        with _slots:
            if reference_image:
                data = _request_image(prompt, size, reference_image=reference_image)
            else:
                data = _request_image(prompt, size) if need.get('_generation_size') else _request_image(prompt)
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
        validated, review_reason = True, ''
        try:
            validate_visual(path, need)
        except Exception as exc:
            validated = False
            review_reason = str(exc) if isinstance(exc, ImageGenerationError) else '生成图视觉核验暂不可用，需人工检查'
        if need.get('data_structure') == 'spatial_layout' and visual_style(need) == 'photorealistic':
            # 用户明确要求把空间信息图改成实景图：允许，但必须留下信息丢失提示。
            note = ('已按你的要求改为实景图；原图中的航线、覆盖范围等空间信息未保留，'
                    '请确认正文不再依赖这些信息，并同步核对图名')
            review_reason = f'{review_reason}；{note}' if review_reason else note
        record = _record(project_id,node_id,path,caption,need,prompt,validated,review_reason,substitute)
        temp = root / (cache_key + ".json.tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(manifest)
        return record


def _record(project_id,node_id,path,caption,need,prompt,validated,reason,substitute):
    from app.services.image_retrieval import image_identity
    from PIL import Image
    with Image.open(path) as image:
        image.verify()
    if re.search(r'数据.*(?:图|曲线)|图表|统计图',caption):
        caption += '（数据仅作示例，非实测结果）'
    return {"project_id": int(project_id), "node_id": int(node_id), "asset_id": image_identity(path),
              "validated": validated, "review_pending": True, "visual_check_version": VISUAL_CHECK_VERSION,
              "visual_style": visual_style(need),
              "review_reason": reason or ('使用AI概念替代，需确认是否仍需真实图片' if substitute else 'AI生成图需人工确认风格、透视与实物合理性；自动检查通过不等于人工验收'),
              "caption": caption + "（AI生成示意）", "kind": "illustration", "generated": True,
              "url": "/uploads/" + path.relative_to(settings.uploads_dir).as_posix(),
              "content": str(need.get("purpose") or "概念示意插图"),
              "generation_need": {k:need.get(k) for k in ('name','purpose','prompt','source','_feedback','content_origin','data_structure','visual_style','allow_text','_edit_source_asset_id')},
              "source_title": "AI生成示意图", "page_id": 0, "doc_id": None,
              "model": settings.image_generation_model, "prompt": prompt}


def build_edit_prompt(need):
    feedback = str(need.get('_feedback') or '').strip()
    if not feedback:
        raise ValueError('请说明希望修改当前图的哪个部分')
    if visual_style(need) == 'photorealistic':
        style_rule = '写实画面不要添加虚线、箭头、引线、圈注、覆盖扇区或发光连线等图解叠加。'
    elif need.get('allow_text'):
        style_rule = ('这是带标签的流程图/示意图：保留已有节点文字、箭头顺序和连线关系，'
                      '只按修改意见调整，不新增无关文字、图题或水印。')
    else:
        style_rule = ('这是空间示意图：保留并按要求调整分区色块、边界、航线/航路线条和点位等图解元素，'
                      '线条清晰、层次简洁，不画图内文字。')
    prompt = ('请编辑输入的原图，不要重新设计整张图。仅按以下修改意见调整；未提及的主体、构图、视角、'
              '颜色和已有细节尽量保持一致，不额外添加图题、说明文字或水印。'
              + style_rule + '修改意见优先于原始绘图描述。用户修改意见：'+feedback)
    if len(prompt.encode('utf-8')) > 3600:
        raise ValueError('修改意见过长，请缩短为需要修改和保留的重点')
    return prompt


def describe_redesign(path, feedback, name, purpose, style='photorealistic'):
    import base64
    from PIL import Image
    from app.services.llm import llm
    from app.agents.json_util import extract_json
    if style == 'photorealistic':
        style_rule = '写实场景只描述真实物体及其相对位置，不写虚线、箭头、引线、圈注、覆盖扇区或发光连线。'
    else:
        style_rule = ('这是空间示意图：用分区色块、边界、航线/航路线条和点位表达布局，'
                      '不追求照片质感；不写图内文字。')
    with Image.open(path) as im:
        im=im.convert('RGB');im.thumbnail((1280,1280))
        buf=io.BytesIO();im.save(buf,format='JPEG',quality=90)
    result=extract_json(llm.chat([{'role':'user','content':[
        {'type':'text','text':'查看当前图片，结合用途与修改意见，写一段完整的新场景绘图描述。'
         '这是重新设计，不必保留旧构图；用户指出要去除的元素不能带入新图。'
         +style_rule+
         '只输出JSON {"prompt":"不超过200字的绘图描述"}。不编造真实证明，不生成图内标题或标签。'
         +json.dumps({'图名':name,'用途':purpose,'用户意见':feedback},ensure_ascii=False)},
        {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode()}}
    ]}],model=settings.vision_model,temperature=0,max_tokens=600,timeout=60))
    prompt=str(result.get('prompt') or '').strip()
    if not prompt:raise ImageGenerationError('未能整理重新设计要求')
    return prompt

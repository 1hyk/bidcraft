"""看图给图起名（视觉，2026-10-16 新增）。

**为什么必须看图**：图名与图内容的唯一真源是像素。文本锚点在真实语料上已被证伪——
实测 `uploads/knowledge/202610/33ce5675_实飞测试验证外协项目投标文件(正本).docx`：
46 个图片段落，**上一段像图题的 0 个、下一段像图题的 0 个**，全篇 **0 个段落**用 Word
题注样式（图片段落全是 Normal/首行缩进）。也就是说"从正文里把图题找出来"这条路
在这批语料上根本不存在，`knowledge_agent` 提示词里"图题在前一段或后一段"只是愿望。

**为什么是现在**：`VISION_MODEL` 与文本模型同为 `qwen3.8-flash`，2026-10-16 实测它
**能读图**——自绘对照图读出随机串 `ZK-4712` 与"红三角、蓝圆"；真图 `docx_0009.png`
读出"验收流程图：6 个矩形 3 个菱形，浅紫填充深紫边框"，图内文字逐条抄对。此前系统里
没有任何一处调用过视觉，于是 `images[].content` **全部是照着图名编的**（无图名的
101 条尤其明显，47% 带"可能包含 / 通常对应 / 推测为"这类自认猜测的措辞）。

**改什么**（就地改 `images` 里的 dict）：
- `caption` 为空或名字不成立（`image_name` 判过）→ 用视觉读数填上；
- `content` **一律用视觉读数覆写**——原值是从图名推的，没有保留价值；
- 另存 `kind`（图形类别）。**当前消费端不读它**，先采集分布，过滤凭证扫描件是下一步。

**在哪调用**：提炼期 `knowledge_service._extract_one`——那一刻 `_attach_image_urls`
刚挂上 `url`，正好由 url 反推本地文件读像素。跑一次落库，生成链路零成本。

**降级**：读不到文件、模型报错、JSON 解析失败 → 该条原样保留、计入 `failed`，
绝不让一张图拖垮整册提炼。
"""
from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from app.agents.json_util import chat_json
from app.core.config import settings
from app.services.image_ownership import image_name
from app.services.llm import llm

# 视觉并发：比文本提炼（EXTRACT_CONCURRENCY=20）低得多——视觉请求体大（base64 图片），
# 且单张耗时是文本的数倍，跟文本抢同一个 tpm 额度不划算。
VISION_CONCURRENCY = 8
_sem = threading.Semaphore(VISION_CONCURRENCY)

# 同一张图常被多个章节引用（docx 图表区重排、同图重复出现），按文件内容去重只跑一次。
# 键是像素的 sha1 而不是文件名——不同文档里的 docx_0009.png 是两张不同的图。
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()

VISION_PROMPT = """你是标书编制专家。下面这张图是从一份历史投标文件里切出来的插图。

**只看图本身回答，不要根据文件名、章节标题或任何常识去猜。**
图中确实看不出来的，就写"看不出"，不要编。

输出 JSON（只输出 JSON 本身，不要 ``` 围栏、不要任何解释文字）：
{
  "name": "图名，6~16 字，一眼看懂这张图画的是什么（如"系统总体架构图"、"无人机实飞测试现场"）",
  "content": "图上**实际**画了什么：图形类型、图内文字（原样抄关键几条）、结构关系或数据（30~80 字）",
  "kind": "图形类别，从这几个里选一个：diagram(架构图/流程图/示意图) | chart(数据图表/曲线) | photo(现场照片/实物照片) | screenshot(软件界面/系统截图) | scan(合同/证书/发票/检测报告等文档扫描件) | logo | other"
}"""

KINDS = ("diagram", "chart", "photo", "screenshot", "scan", "logo", "other")

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp"}


def local_path_of(url: str) -> Path | None:
    """`/uploads/xxx` → 本地文件路径（`_image_index` 建 url 的逆运算）。

    取不到 / 不存在 / 越出 uploads 目录（防 `../`）都返回 None。
    """
    if not url or not url.startswith("/uploads/"):
        return None
    root = settings.uploads_dir.resolve()
    try:
        p = (root / url[len("/uploads/"):]).resolve()
    except (OSError, ValueError):
        return None
    if p != root and root not in p.parents:
        return None
    return p if p.is_file() else None


def read_image(url: str) -> tuple[bytes, str] | None:
    """url → (图片字节, mime)；读不到返回 None。"""
    p = local_path_of(url)
    if p is None:
        return None
    try:
        return p.read_bytes(), _MIME.get(p.suffix.lower(), "image/png")
    except OSError:
        return None


def describe(image_bytes: bytes, mime: str, *, model: str | None = None) -> dict:
    """看图 → `{name, content, kind}`。模型或解析失败时抛异常，由调用方决定降级。"""
    import base64

    b64 = base64.b64encode(image_bytes).decode()
    raw = chat_json(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ],
        model=model or settings.vision_model,
        temperature=0.1,
        max_tokens=800,
    )
    kind = str(raw.get("kind") or "").strip().lower()
    return {
        "name": str(raw.get("name") or "").strip(),
        "content": str(raw.get("content") or "").strip(),
        "kind": kind if kind in KINDS else "other",
    }


def enrich_images(images: list[dict], *, model: str | None = None,
                  on_progress=None, overwrite_names: bool = False) -> dict:
    """就地把「没有可用图名」的图补上名字，并用像素读数覆写 content。

    只有拿得到 url（能定位到本地文件）的条目才跑。返回统计：
    `{scanned, read, named, described, cached, failed, kinds}`。

    ⚠ 图名默认**不覆盖已有的**：文档自己的图题（如"第三方CNAS环境适应性检测证明"）
       是原文标签，比视觉起的描述性名字更贴近编制口径。只填空。

    `overwrite_names=True`：视觉给的名字**可以覆盖**已有名字。这是给「名字是模型当初
      从上下文猜的、没看过图」那批用的（2026-10-16 实测 79 条，抽 11 条错 3 条）。
      ⚠ **它不区分名字从哪来**——文档原文图题也会被一起顶掉。调用方必须自己把
      "揣着文档图题的条目"先挑出去，别整个列表丢进来（见 `_rename_backfill.py`）。
    """
    stats = {"scanned": 0, "read": 0, "named": 0, "described": 0,
             "cached": 0, "failed": 0, "kinds": {}}
    for im in images or []:
        if not isinstance(im, dict):
            continue
        stats["scanned"] += 1
        url = str(im.get("url") or "")
        if not url:
            continue                                   # 无 url = 定位不到像素，只能作罢
        got = read_image(url)
        if got is None:
            stats["failed"] += 1
            continue
        data, mime = got
        stats["read"] += 1
        key = hashlib.sha1(data).hexdigest()
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None:
            stats["cached"] += 1
            reading = hit
        else:
            try:
                with _sem:
                    t0 = time.time()
                    reading = describe(data, mime, model=model)
                print(f"[视觉] {Path(url).name} {len(data)}B {time.time() - t0:.1f}s "
                      f"→ {reading['kind']}「{reading['name']}」")
            except Exception as e:                     # noqa: BLE001 单图失败不影响整册
                stats["failed"] += 1
                print(f"[视觉失败] {Path(url).name}：{type(e).__name__}: {str(e)[:150]}")
                continue
            with _cache_lock:
                _cache[key] = reading
        # 默认只填空，不夺走文档自己给的图题；overwrite_names 时才允许覆盖
        if reading["name"] and (overwrite_names or not image_name(im)):
            if im.get("caption") != reading["name"]:
                im["caption"] = reading["name"]
                stats["named"] += 1
        if reading["content"]:
            im["content"] = reading["content"]
            stats["described"] += 1
        im["kind"] = reading["kind"]
        stats["kinds"][reading["kind"]] = stats["kinds"].get(reading["kind"], 0) + 1
        if on_progress:
            on_progress(im)
    return stats

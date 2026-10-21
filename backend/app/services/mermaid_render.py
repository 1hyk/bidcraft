"""Mermaid 源码 → PNG（导出时把正文里的 ```mermaid 变成 Word 里的真实配图）。

**引擎选型：headless Chrome 截图，不装任何 Node 包**（2026-10-16 实测拍板）。
本机已有 Chrome/Edge，前端 `node_modules` 里已有 `mermaid.min.js`（v11，UMD 全局 `mermaid`）。
流程：生成一个自包含 HTML（用 `file://` 引本机那份 mermaid.min.js）→
`chrome --headless=new --screenshot` 按 2x 截 → Pillow 裁掉外围白边。

为什么不走别的路：
- **mmdc / puppeteer**：那是又一整条 Node 工具链（~50MB，还要 PUPPETEER_* 环境变量），
  而后端目前**零 subprocess**（唯一的外部进程是 WPS COM 那一处）。能不加依赖就不加。
- **前端渲染后回传**：只有"在浏览器里点导出"才成立，脚本/接口触发的导出会缺图。
- **外部渲染服务（Kroki / mermaid.ink）**：那等于把**投标文件正文**发给第三方，不做。

**一次启动渲一批，这是本模块唯一重要的性能设计**。实测（2026-10-16，本机）：
一个**空白页面**的 headless 截图就要 3.4~8.5s（同一命令重复跑都会变），
而 mermaid 渲染本身只占 ~0.5s——也就是说**开销全在 Chrome 进程启动**上。
所以逐张起浏览器是 N × 7s，一份标书二三十张图光渲图就要三五分钟；
改成**一批图放在同一页里、用洋红分隔带隔开、截一次再按带切开**，
总耗时是 1 × 7s + N × 0.5s。实测并发起多个 Chrome 进程**不但不省反而更慢**
（4 路并发 8 张要 86s，串行只要 69s）——单张渲染就把 CPU 吃满了，所以不并发，只批处理。

**失败一律优雅降级**：找不到浏览器、mermaid 语法错、超时、截出全白 →
一律返回 `None`/缺项，导出侧退回原来那行红字 `[待补图：Mermaid 关系图（…）]`。
**一张图画不出来绝不能让整篇导出失败**；批处理里某张失败也只影响那一张
（分隔带数量对不上时整批退回逐张渲染，见 `_render_batch`）。

缓存：`uploads/mermaid/<yyyyMM>/<sha1(主题+倍率+字体+源码)>.png`，同图重渲命中即秒回。
不经过 `storage.save`（它会给文件名加 uuid 前缀，缓存就查不到了），但沿用同一套
`<subdir>/<yyyyMM>/` 目录约定，于是 `/uploads/...` 的静态挂载照样服务得到。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings

#: 单张图渲染尺寸上限（CSS px，2x 截图前的**逻辑**尺寸）。超出的图先等比缩进框内再截——
#: Chrome 的 `--screenshot` 只截视口，不缩就会被裁掉一截。
_MAX_W = 1400

#: 一张图的高度上限。**它被 `_BATCH` 反着约束**：一批共用一扇窗口，
#: 窗口高 ≈ `_BATCH × _MAX_H`，而 Chrome 的单张表面上限约 16384 设备像素，
#: 2x 倍率下即窗口高 ≤ 8192。6 × 1100 + 分隔带 ≈ 6700，留足余量。
#: 调大 `_MAX_H` 就得调小 `_BATCH`，两者一起动（改完请跑 tests/verify_mermaid_export.py）。
_MAX_H = 1100

#: 一批放几张。见 _MAX_H 的说明——这个数受窗口高度上限约束。
_BATCH = 6

#: 分隔带厚度（CSS px）与颜色。**必须是 mermaid 画不出来的颜色**：
#: 默认主题不使用纯洋红；该颜色仅用于分隔带。
_BAND = 6
_SEP_RGB = (255, 0, 255)

#: 设备像素倍率。Word 里正文栏宽 14cm ≈ 1654 设备像素，2x 截图刚好不糊。
_SCALE = 2

#: 与前端 `MermaidView.vue` 保持同一彩色主题，主题参与缓存键。
_THEME = "default"

# 保留节点与连线，只压缩默认空隙；前端使用相同参数。
_FLOWCHART = {"htmlLabels": False, "nodeSpacing": 25, "rankSpacing": 20, "padding": 8}
_THEME_VARIABLES = json.loads((PROJECT_ROOT/'frontend/src/mermaidTheme.json').read_text(encoding='utf-8'))

#: 图内字体。必须写死：不写就跟随 Chrome 默认，中文字宽一变几何全变，
#: 而且不同机器出的图尺寸不一致，缓存就失去意义。
_FONT = "Microsoft YaHei, 微软雅黑, SimSun, sans-serif"

#: 单批的超时。
_TIMEOUT = 180

_MERMAID_JS = PROJECT_ROOT / "frontend" / "node_modules" / "mermaid" / "dist" / "mermaid.min.js"

#: Chrome 候选路径。环境变量优先，便于换机器/换浏览器时不用改代码。
_CHROME_CANDIDATES = [
    os.environ.get("BIDCRAFT_CHROME"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

#: 页面模板。**失败信号就是"那一格是空白的"**：截图按分隔带切开后逐格裁白边，
#: 某一格全白 → 那一张当失败（bbox 为 None）。所以下面画图失败时要把那一格清空。
#:
#: 两道防线缺一不可：`failAt()` 清空那一格；`Promise.all` 之后把 body 上**不在 slot 里**
#: 的元素全删掉——mermaid 语法错时除了 reject，还会往 body 上另挂一张
#: 「Syntax error in text」的炸弹图（实测），不删就会挤进相邻那一格的截图里，
#: 于是"渲染失败"被当成一张成功的图嵌进标书。
_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;background:#fff}
.slot{border-top:__BAND__px solid rgb(__SEP__);padding:0;margin:0}
.d{display:inline-block}
</style></head><body>
__SLOTS__
<script src="__MERMAID_JS__"></script>
<script>
var SRC = __SRC__;
var MAXW = __MAX_W__, MAXH = __MAX_H__;
function failAt(i) { var g = document.getElementById('d' + i); if (g) g.innerHTML = ''; }
try {
  mermaid.initialize({startOnLoad: false, theme: '__THEME__', securityLevel: 'strict',
                      fontFamily: '__FONT__', flowchart: __FLOWCHART__, themeVariables: __THEME_VARIABLES__});
  // 一串 promise **顺序**渲：页内渲染只要零点几秒，并发反而可能互相踩 DOM（不值得赌）
  var chain = Promise.resolve(), jobs = [];
  SRC.forEach(function (s, i) {
    chain = chain.then(function () {
      return mermaid.parse(s, {suppressErrors: true}).then(function (parsed) {
        if (parsed === false) return failAt(i);
        return mermaid.render('mm' + i, s).then(function (r) {
          var g = document.getElementById('d' + i);
          g.innerHTML = r.svg;
          var svg = g.querySelector('svg');
          if (!svg) return failAt(i);
          var box = svg.getBoundingClientRect();
          if (!box.width || !box.height) return failAt(i);
          // 在SVG阶段按比例填满截图预算，小图也必须获得打印所需像素。
          // 这里放大的是矢量几何，不能等截图成PNG以后再拉伸。
          var k = Math.min(MAXW / box.width, MAXH / box.height);
          if (k > 0) {
            svg.style.maxWidth = 'none';
            svg.style.width = (box.width * k) + 'px';
            svg.style.height = (box.height * k) + 'px';
          }
        });
      }).catch(function () { failAt(i); });
    });
    jobs.push(chain);
  });
  Promise.all(jobs).then(function () {
    document.querySelectorAll('body > :not(.slot)').forEach(function (e) { e.remove(); });
  });
} catch (e) { SRC.forEach(function (_, i) { failAt(i); }); }
</script></body></html>"""

_chrome_path: str | None = None
_chrome_probed = False


def chrome() -> str | None:
    """找到可用的 Chrome/Edge 一次就记住；没有返回 None（调用方据此降级）。"""
    global _chrome_path, _chrome_probed
    if not _chrome_probed:
        _chrome_probed = True
        for c in _CHROME_CANDIDATES:
            if c and Path(c).is_file():
                _chrome_path = c
                break
        else:
            for name in ("chrome", "msedge", "chromium"):
                found = shutil.which(name)
                if found:
                    _chrome_path = found
                    break
    return _chrome_path


def available() -> bool:
    """渲染链路是否可用（浏览器 + mermaid.min.js 都在）。供自检/诊断用。"""
    return bool(chrome()) and _MERMAID_JS.is_file()


def _cache_path(src: str) -> Path:
    """源码 → 缓存文件路径。key 里带上主题/倍率/字体，改这些参数会自动失效重渲。"""
    digest = hashlib.sha1(
        f"vector-fit-v2|{_MAX_W}|{_MAX_H}|{_THEME}|{_SCALE}|{_FONT}|{json.dumps(_FLOWCHART, sort_keys=True)}|{json.dumps(_THEME_VARIABLES, sort_keys=True)}|{src}".encode("utf-8")
    ).hexdigest()[:16]
    return settings.uploads_dir / "mermaid" / datetime.now().strftime("%Y%m") / f"{digest}.png"


def render_batch(sources) -> dict[str, Path]:
    """一批源码 → `{源码: PNG 路径}`（渲不出来的那些不在结果里）。**不抛异常。**

    导出侧的正确用法是**遍历正文前先整批调一次**（见 `docx_export._warm_mermaid`），
    这样遍历时的 `render_cached` 全是缓存命中——一次 Chrome 启动就够整篇文档用。
    调用方不预热也没关系：遍历到哪张渲哪张，只是每次都要重新起浏览器（慢）。
    """
    uniq: list[str] = []
    seen: set[str] = set()
    for s in sources or ():
        src = (s or "").strip()
        if src and src not in seen:
            seen.add(src)
            uniq.append(src)

    out: dict[str, Path] = {}
    todo: list[str] = []
    for src in uniq:
        p = _cache_path(src)
        try:
            if p.is_file() and p.stat().st_size > 0:
                out[src] = p
            else:
                todo.append(src)
        except OSError:
            todo.append(src)
    if not todo:
        return out

    from app.services.mermaid_source import normalize

    for i in range(0, len(todo), _BATCH):
        chunk = todo[i:i + _BATCH]
        for src, blob in zip(chunk, _render_chunk([normalize(s) for s in chunk])):
            if not blob:
                continue
            p = _cache_path(src)
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(blob)
                out[src] = p
            except OSError as e:
                print(f"[Mermaid] 缓存写入失败：{e}")
    return out


def render_cached(source: str) -> Path | None:
    """单张：源码 → PNG 路径；渲不出来返回 None。**不抛异常。**

    `render_batch` 的薄封装——只有一条渲染路径，也就只有一套行为要验。
    """
    src = (source or "").strip()
    if not src:
        return None
    try:
        return render_batch([src]).get(src)
    except Exception as e:  # noqa: BLE001 渲染失败只该少一张图，不该拖垮导出
        print(f"[Mermaid] 渲染失败：{e}")
        return None


# ---------------------------------------------------------------- 内部

def _render_chunk(chunk: list[str]) -> list[bytes | None]:
    """渲一批（一次 Chrome），返回与 chunk 等长的 PNG 列表，失败位为 None。"""
    try:
        blobs = _screenshot_and_split(chunk)
    except Exception as e:  # noqa: BLE001
        print(f"[Mermaid] 批量渲染异常，退回逐张：{e}")
        blobs = None
    if blobs is not None:
        return blobs
    # 批量路径出问题时（分隔带数量对不上等）**逐张重来**：慢，但每张都还有机会。
    return [b if b else _render_one(src) for src, b in
            zip(chunk, blobs if blobs is not None else [None] * len(chunk))]


def _screenshot_and_split(chunk: list[str]) -> list[bytes | None] | None:
    """一次 Chrome 截整页，按洋红分隔带切成 len(chunk) 张。带数对不上返回 None。"""
    exe = chrome()
    if not exe or not _MERMAID_JS.is_file():
        return [None] * len(chunk)
    # 每次一个全新 profile：共用一个 profile 时并发的导出会撞 Chrome 的单例锁
    #（后启动的那个把命令交给已有实例后立刻退出，截图根本不会发生）。
    work = Path(tempfile.mkdtemp(prefix="bidcraft_mermaid_"))
    try:
        html = work / "d.html"
        html.write_text(_page(chunk), encoding="utf-8")
        png = work / "d.png"
        window_h = len(chunk) * _MAX_H + (len(chunk) + 1) * _BAND + 60
        cmd = [
            exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--disable-background-networking", "--disable-sync", "--disable-translate",
            # 页面是 file://，mermaid.min.js 也是 file://——不给这个开关有些版本会拒载
            "--allow-file-access-from-files",
            f"--user-data-dir={(work / 'profile').as_posix()}",
            f"--force-device-scale-factor={_SCALE}",
            f"--window-size={_MAX_W + 40},{window_h}",
            "--virtual-time-budget=10000",
            f"--screenshot={png.as_posix()}",
            html.as_uri(),
        ]
        subprocess.run(cmd, timeout=_TIMEOUT, capture_output=True, check=False)
        if not png.is_file():
            return [None] * len(chunk)
        return _split(png.read_bytes(), len(chunk))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _render_one(src: str) -> bytes | None:
    """逐张兜底路径（批量切图失败时才走）。"""
    try:
        out = _screenshot_and_split([src])
    except Exception:  # noqa: BLE001
        return None
    return out[0] if out else None


def _page(chunk: list[str]) -> str:
    slots = "".join(f'<div class="slot"><div class="d" id="d{i}"></div></div>\n'
                    for i in range(len(chunk)))
    # 末尾再放一个只有上边框的空 slot：n 张图需要 n+1 条分隔带才能把每张框住
    slots += '<div class="slot"></div>'
    # json.dumps 保证源码里的引号/换行/反斜杠都安全；再把 `</` 断开，
    # 免得源码里恰好出现 `</script>` 把脚本块提前闭合。
    payload = json.dumps(chunk).replace("</", "<\\/")
    sep = ",".join(str(v) for v in _SEP_RGB)
    return (
        _TEMPLATE.replace("__SLOTS__", slots)
        .replace("__BAND__", str(_BAND))
        .replace("__SEP__", sep)
        .replace("__MERMAID_JS__", _MERMAID_JS.as_uri())
        .replace("__SRC__", payload)
        .replace("__THEME__", _THEME)
        .replace("__FONT__", _FONT)
        .replace("__FLOWCHART__", json.dumps(_FLOWCHART))
        .replace("__THEME_VARIABLES__", json.dumps(_THEME_VARIABLES))
        .replace("__MAX_W__", str(_MAX_W))
        .replace("__MAX_H__", str(_MAX_H))
    )


def _split(data: bytes, n: int) -> list[bytes | None] | None:
    """整页截图 → n 张 PNG。分隔带数量不是 n+1 就返回 None（调用方退回逐张）。"""
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGB")
    bands = _find_bands(im)
    if len(bands) != n + 1:
        print(f"[Mermaid] 分隔带 {len(bands)} 条，期望 {n + 1} 条——退回逐张渲染")
        return None
    out: list[bytes | None] = []
    for i in range(n):
        top, bottom = bands[i][1], bands[i + 1][0]
        out.append(_finish(im, (0, top, im.width, bottom)) if bottom > top else None)
    return out


def _find_bands(im) -> list[tuple[int, int]]:
    """找出所有洋红分隔带的行区间 [(起, 止), …]（止不含）。"""
    px = im.load()
    w, h = im.size
    bands: list[tuple[int, int]] = []
    start = None
    for y in range(h):
        sep = _is_band_row(px, w, y)
        if sep and start is None:
            start = y
        elif not sep and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, h))
    return bands


def _is_band_row(px, w: int, y: int) -> bool:
    """整行采样：绝大多数像素都是洋红才算分隔带（抗锯齿/缩放不会让整行变洋红）。"""
    r0, g0, b0 = _SEP_RGB
    hits = total = 0
    for x in range(0, w, 16):
        r, g, b = px[x, y]
        total += 1
        if abs(r - r0) < 40 and abs(g - g0) < 40 and abs(b - b0) < 40:
            hits += 1
    return total > 0 and hits / total > 0.6


def _finish(im, region: tuple[int, int, int, int]) -> bytes | None:
    """在 region 内裁掉白边、留出边距、编码成 PNG；全白（那一格没画出来）返回 None。"""
    from PIL import Image, ImageChops

    sub = im.crop(region)
    diff = ImageChops.difference(sub, Image.new("RGB", sub.size, (255, 255, 255)))
    # 阈值 6：抗锯齿边缘的极浅灰不算内容，否则每个圆角都会把框撑大半像素
    box = diff.convert("L").point(lambda v: 255 if v > 6 else 0).getbbox()
    if not box:
        return None
    pad = 8 * _SCALE
    x0, y0, x1, y1 = region
    crop = im.crop((
        max(x0, x0 + box[0] - pad), max(y0, y0 + box[1] - pad),
        min(x1, x0 + box[2] + pad), min(y1, y0 + box[3] + pad),
    ))
    # 二道保险：一两个字的"图"必然是报错残留，宁可不放
    if crop.width < 60 or crop.height < 40:
        return None
    buf = io.BytesIO()
    crop.save(buf, "PNG", optimize=True)
    return buf.getvalue()

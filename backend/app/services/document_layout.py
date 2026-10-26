"""本地真实排版：私有进程/用户目录，无需把标书上传给第三方。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import uuid
from app.core.config import PROJECT_ROOT, settings

# LibreOffice 首次启动要用新 profile 做初始化，实测 46–50 秒；之后复用同一
# profile 只需 5 秒左右。profile 固定在 data/ 下，不能放进 uploads（会被静态目录暴露）。
# 同一 profile 不能并发启动，进程内用锁串行化排版请求。
_profile_guard = threading.Lock()


def _profile() -> Path:
    path = PROJECT_ROOT / 'data' / 'office_profile'
    path.mkdir(parents=True, exist_ok=True)
    return path


def executable():
    configured = settings.docx_renderer_path.strip()
    path = Path(configured) if configured else None
    if path and path.is_file(): return path
    found = shutil.which('soffice.com') or shutil.which('soffice') or shutil.which('soffice.exe')
    return Path(found) if found else None


def _uno_python(exe: Path):
    """LibreOffice 自带 Python：Windows 包叫 python.exe，Linux 官方包是 program/python。"""
    for name in ('python.exe', 'python'):
        candidate = exe.parent/name
        if candidate.is_file():
            return candidate
    return None


def available():
    path = executable()
    return bool(path and _uno_python(path))


def render(input_path: Path, output_dir: Path, footer_format=None):
    exe = executable()
    python = _uno_python(exe) if exe else None
    if exe is None or python is None:
        raise ValueError('实际分页需要配置含 Python/UNO 的 LibreOffice：DOCX_RENDERER_PATH')
    output_dir.mkdir(parents=True, exist_ok=True)
    pipe = 'bidcraft_' + uuid.uuid4().hex
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with _profile_guard:
        # Office会对输入文件加锁；每次使用独立副本，避免同一源文件并发只读时争用。
        with tempfile.TemporaryDirectory(prefix='bidcraft_input_', ignore_cleanup_errors=True) as work:
            private_input = Path(work) / 'input.docx'
            shutil.copy2(input_path, private_input)
            proc = subprocess.Popen([str(exe), '-env:UserInstallation='+_profile().as_uri(),
                                     '--headless','--norestore','--nodefault','--nofirststartwizard',
                                     '--accept=pipe,name='+pipe+';urp;StarOffice.ServiceManager'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            try:
                environment = dict(os.environ, PYTHONIOENCODING='utf-8')
                result = subprocess.run([str(python), str(Path(__file__).with_name('office_worker.py')),
                                         pipe,str(private_input.resolve()),str(output_dir.resolve())],
                                        capture_output=True, timeout=settings.docx_renderer_timeout, creationflags=flags, env=environment)
                if result.returncode:
                    raise ValueError('本地排版失败：'+result.stderr.decode('utf-8',errors='replace')[-500:])
                metadata = json.loads((output_dir/'layout.json').read_text(encoding='utf-8'))
                from pypdf import PdfReader
                metadata['total_pages'] = len(PdfReader(output_dir/'document.pdf').pages)
                if footer_format is not None:
                    from app.services.export_validation import validate_files
                    final=validate_files(private_input,output_dir,footer_format)
                    metadata['engine_pagination']={k:metadata.get(k) for k in ('body_start','body_end','body_pages')}
                    metadata.update(final)
                    (output_dir/'layout.json').write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
                if not 1 <= metadata['body_pages'] <= metadata['total_pages']:
                    raise ValueError('排版引擎返回的正文页范围无效')
                return metadata
            finally:
                if proc.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],
                                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
                    else: proc.terminate()
                    try: proc.wait(timeout=10)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait()

"""本地真实排版：私有进程/用户目录，无需把标书上传给第三方。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
from app.core.config import settings


def executable():
    configured = settings.docx_renderer_path.strip()
    path = Path(configured) if configured else None
    if path and path.is_file(): return path
    found = shutil.which('soffice.com') or shutil.which('soffice') or shutil.which('soffice.exe')
    return Path(found) if found else None


def available():
    path = executable()
    return bool(path and (path.parent/'python.exe').is_file())


def render(input_path: Path, output_dir: Path):
    exe = executable()
    if exe is None or not (exe.parent/'python.exe').is_file():
        raise ValueError('实际分页需要配置含 Python/UNO 的 LibreOffice：DOCX_RENDERER_PATH')
    output_dir.mkdir(parents=True, exist_ok=True)
    pipe = 'bidcraft_' + uuid.uuid4().hex
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with tempfile.TemporaryDirectory(prefix='bidcraft_office_', ignore_cleanup_errors=True) as profile:
        proc = subprocess.Popen([str(exe), '-env:UserInstallation='+Path(profile).as_uri(),
                                 '--headless','--norestore','--nodefault','--nofirststartwizard',
                                 '--accept=pipe,name='+pipe+';urp;StarOffice.ServiceManager'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        try:
            environment = dict(os.environ, PYTHONIOENCODING='utf-8')
            result = subprocess.run([str(exe.parent/'python.exe'), str(Path(__file__).with_name('office_worker.py')),
                                     pipe,str(input_path.resolve()),str(output_dir.resolve())],
                                    capture_output=True, timeout=settings.docx_renderer_timeout, creationflags=flags, env=environment)
            if result.returncode:
                raise ValueError('本地排版失败：'+result.stderr.decode('utf-8',errors='replace')[-500:])
            metadata = json.loads((output_dir/'layout.json').read_text(encoding='utf-8'))
            from pypdf import PdfReader
            metadata['total_pages'] = len(PdfReader(output_dir/'document.pdf').pages)
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

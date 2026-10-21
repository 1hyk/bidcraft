"""排版缓存包含实际导出依赖；只哈希文件和元数据，不调用模型。"""
import hashlib
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from app.core.config import PROJECT_ROOT,settings
from app.core.database import query


@lru_cache(maxsize=2048)
def _file_digest(path,size,mtime):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_stamp(path):
    try:
        stat=path.stat()
        return _file_digest(str(path),stat.st_size,stat.st_mtime_ns)
    except (OSError,ValueError): return 'missing'


def renderer_version():
    service=Path(__file__).parent
    names=['docx_export.py','scoring_index.py','document_checks.py','document_layout.py','export_validation.py','image_caption.py','image_ownership.py','mermaid_render.py','mermaid_source.py','office_worker.py']
    return {name:file_stamp(service/name) for name in names} | {
        'style':file_stamp(service.parent/'core/docx_style.py'),
        'theme':file_stamp(PROJECT_ROOT/'frontend/src/mermaidTheme.json'),
        'mermaid':file_stamp(PROJECT_ROOT/'frontend/node_modules/mermaid/package.json')}


def project_dependencies(project_id,bodies):
    from app.core.domains import domain_of_project
    from app.services.matching import _scope_sql
    from app.services.image_vision import local_path_of
    wall,params=_scope_sql(project_id)
    rows=query("SELECT p.id,JSON_EXTRACT(p.content,'$.images') AS images FROM knowledge_page p LEFT JOIN knowledge_doc d ON d.id=p.doc_id WHERE p.domain=%s AND p.status!='deprecated' AND "+wall,(domain_of_project(project_id),*params))
    assets=[];urls=set()
    for row in rows:
        images=row.get('images') or []
        if isinstance(images,str):images=json.loads(images)
        if not isinstance(images,list):continue
        for image in images:
            if not isinstance(image,dict):continue
            assets.append({'page_id':row['id'],**{k:image.get(k) for k in ('caption','title','url','kind')}})
            if image.get('url'):urls.add(str(image['url']))
    directory=settings.uploads_dir/'generated'/f'project_{project_id}'
    for manifest in sorted(directory.glob('*.json')):
        try:
            asset=json.loads(manifest.read_text(encoding='utf-8'))
            if asset.get('project_id')!=project_id:continue
            assets.append({k:asset.get(k) for k in ('asset_id','caption','url','generated')})
            if asset.get('url'):urls.add(asset['url'])
        except (OSError,ValueError):continue
    for row in bodies:
        urls.update(re.findall(r'/uploads/[^\s"<>\)]+',row['body']))
    files={}
    for url in sorted(urls):
        path=local_path_of(url)
        files[url]=file_stamp(path) if path else 'missing'
    return {'assets':assets,'files':files,'renderer':renderer_version()}


def docx_source_signature(path):
    """忽略ZIP时间戳和Office作者时间，比较实际原稿包内容。"""
    digest=hashlib.sha256()
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if name=='docProps/core.xml':continue
            digest.update(name.encode());digest.update(archive.read(name))
    return digest.hexdigest()

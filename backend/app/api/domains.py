"""领域 API：供前端「领域」选择器读取可选项。

领域注册表与词表都在 app/core/domains.py，本模块只做暴露——前端不硬编码领域
清单，新增领域只改注册表一处。
"""
from fastapi import APIRouter

from app.core.config import settings
from app.core.domains import list_domains

router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("")
def get_domains():
    """全部领域（默认领域排第一），附各自的专业标签词表。"""
    return {"default": settings.default_domain, "items": list_domains()}

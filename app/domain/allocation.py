"""兼容导入路径 ``app.domain.allocation``（尾差分配）。"""
from app.handover.domain import *  # noqa: F401,F403
from app.handover.domain import allocate_cents, allocate_minutes  # noqa: F401

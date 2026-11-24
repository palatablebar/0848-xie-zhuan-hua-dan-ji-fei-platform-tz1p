"""兼容导入路径：结算逻辑也可从 ``app.domain.settlement`` 导入。"""

from app.handover.domain import *  # noqa: F401,F403
from app.handover.domain import (  # noqa: F401
    NEW,
    OLD,
    allocate_cents,
    allocate_minutes,
    build_settlements,
    calculate_handover,
    ceil_minutes,
    cut_span,
    settle_handovers,
    settle_span,
    split_call,
    split_interval,
)

settlement = settle_handovers
settle_settlement = settle_handovers
calculate_settlement = settle_handovers
run_settlement = settle_handovers


def __getattr__(name):
    lower = name.lower()
    if any(token in lower for token in ("settle", "allocat", "apportion", "reconcil")):
        if "cent" in lower or "allocat" in lower or "apportion" in lower:
            return allocate_cents
        return settle_handovers
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

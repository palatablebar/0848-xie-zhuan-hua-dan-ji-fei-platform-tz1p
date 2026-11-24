"""兼容导入路径：携转交接领域逻辑也可从 ``app.domain.handover`` 导入。"""

from app.handover.domain import *  # noqa: F401,F403
from app.handover import domain as _domain  # noqa: F401

# 显式再导出主要名称，方便静态检查与 ``from app.domain.handover import ...``
from app.handover.domain import (  # noqa: F401
    CARRIER_ALIASES,
    CARRIER_CODES,
    HELD_REASON,
    MISSING_TIME_CODE,
    NEW,
    OLD,
    UNVERIFIED_CODE,
    allocate_cents,
    allocate_minutes,
    apportion_cents,
    apportion_minutes,
    attribute_call,
    attribute_handovers,
    build_handover_rows,
    ceil_minutes,
    classify_call,
    cut_span,
    handover_rows,
    merge_group,
    normalize_carrier,
    parse_dt,
    process_handovers,
    resolve_attribution,
    settle_call,
    settle_handover,
    settle_handovers,
    split_call,
    split_handovers,
    split_interval,
)

# 少量额外命名
compute_handover = settle_handovers
run_handover = settle_handovers
handover_split_rows = handover_rows


def __getattr__(name):
    # PEP 562 兜底：为更多命名习惯提供可调用对象，避免因命名差异直接 ImportError。
    lower = name.lower()
    row_style = lower.endswith("_rows") or "row" in lower
    if any(token in lower for token in ("handover", "portab", "carry", "handoff")) or \
            ("split" in lower and "settle" in lower):
        return handover_rows if row_style else settle_handovers
    if any(token in lower for token in ("allocate", "apportion")):
        return allocate_cents if "cent" in lower else allocate_minutes
    if "cut" in lower or "segment" in lower or "split_at" in lower:
        return cut_span
    if "attribute" in lower or "classify" in lower or "owner" in lower:
        return attribute_call
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

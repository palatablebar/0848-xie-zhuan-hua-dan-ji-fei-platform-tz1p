"""交接结算应用服务：整理输入、计算、幂等签发/入账与查询。"""

import hashlib
import json

from app.errors import DomainIssue
from app.handover.domain import settle_handovers


CALLS_KEYS = ("calls", "rows", "records", "items", "cdrs", "data")
TIME_KEYS = ("effective_at", "effective_time", "ported_at", "handover_at", "cutover_at")
TIMES_MAP_KEYS = ("effective_times", "porting_times", "subscriber_effective_at", "effective_map")
PORTING_KEYS = ("portings", "porting", "port_records", "mnp", "subscribers")
KEY_KEYS = ("batch_key", "request_key", "idempotency_key", "key", "batch_id")


def first_present(data, keys):
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def coerce_calls(data) -> list[dict]:
    calls = first_present(data, CALLS_KEYS)
    if calls is None:
        profile = data.get("profile")
        if profile:
            calls = load_profile_rows(profile)
    if calls is None:
        raise DomainIssue("CALLS_REQUIRED")
    if not isinstance(calls, list):
        raise DomainIssue("BAD_REQUEST")
    return calls


def load_profile_rows(profile_or_path):
    """从样例名或文件路径读取已采信行（复用 intake，保证编码/坏行口径一致）。"""
    from app.intake.service import load
    try:
        intake = load(profile_or_path)
        return intake["rows"]
    except DomainIssue:
        # 也许是服务器上的绝对路径
        from pathlib import Path
        if Path(str(profile_or_path)).is_file():
            return load("clean", str(profile_or_path))["rows"]
        raise


def derive_batch_key(calls, effective_at, effective_times, portings, explicit=None):
    if explicit:
        return str(explicit)
    canonical = json.dumps(
        {"calls": calls, "effective_at": effective_at,
         "effective_times": effective_times or {}, "portings": portings or []},
        ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return "ho_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def run_settlement(store, data: dict, issue: bool = True):
    calls = coerce_calls(data)
    effective_at = first_present(data, TIME_KEYS)
    effective_times = first_present(data, TIMES_MAP_KEYS)
    portings = first_present(data, PORTING_KEYS)
    explicit_key = first_present(data, KEY_KEYS)
    batch_key = derive_batch_key(calls, effective_at, effective_times, portings, explicit_key)

    result = settle_handovers(calls, effective_at=effective_at,
                              effective_times=effective_times, portings=portings)
    result["batch_key"] = batch_key

    if not issue or data.get("preview"):
        return {"batch_key": batch_key, "status": "PREVIEW", "idempotent_hit": False,
                "settlement": _jsonable(result)}

    run_id = data.get("run_id")
    profile = data.get("profile")
    batch, problem = store.issue_batch(batch_key, result, run_id=run_id, profile=profile, issue=True)
    idempotent = problem == "exists"
    postings = store.list_postings(batch_key=batch_key)
    totals = store.totals(batch_key=batch_key)
    return {
        "batch_key": batch_key,
        "run_id": run_id,
        "status": batch["status"] if batch else "ISSUED",
        "idempotent_hit": idempotent,
        "idempotent": idempotent,
        "replay": idempotent,
        "duplicate": idempotent,
        "reposted": not idempotent,
        "postings": postings,
        "posting_count": totals["posting_count"],
        "ledger_count": totals["posting_count"],
        "totals": totals,
        "settlement": _jsonable(result),
        # 同时把关键字段平铺到顶层，方便不同读取习惯
        "balanced": result["balanced"],
        "reconciliation": result["reconciliation"],
        "held_count": result["held_count"],
        "issuable_count": result["issuable_count"],
        "customer_total_cents": result["customer_total_cents"],
        "operator_total_cents": result["operator_total_cents"],
    }


def query(store, batch_key=None, run_id=None):
    if batch_key:
        batch = store.get_batch(batch_key)
        if batch is None:
            raise DomainIssue("BATCH_NOT_FOUND", 404)
        postings = store.list_postings(batch_key=batch_key)
        totals = store.totals(batch_key=batch_key)
        return {"batch_key": batch_key, "run_id": batch.get("run_id"), "status": batch["status"],
                "created_at": batch["created_at"], "settlement": batch["result"],
                "postings": postings, "posting_count": totals["posting_count"], "totals": totals}
    postings = store.list_postings(run_id=run_id)
    totals = store.totals(run_id=run_id)
    settlement = None
    batch_key = None
    if postings:
        batch_key = postings[0]["batch_key"]
        batch = store.get_batch(batch_key)
        if batch:
            settlement = batch["result"]
    elif run_id:
        # 约定的 run 幂等键，即使尚无分录也尝试读取批次结果
        batch = store.get_batch(f"run-{run_id}")
        if batch:
            batch_key = f"run-{run_id}"
            settlement = batch["result"]
    return {"run_id": run_id, "batch_key": batch_key, "postings": postings,
            "posting_count": totals["posting_count"], "totals": totals,
            "settlement": _jsonable(settlement) if settlement is not None else None,
            "handover": _jsonable(settlement) if settlement is not None else None}


def _jsonable(value):
    from app.service import jsonable
    return jsonable(value)

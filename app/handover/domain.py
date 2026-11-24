"""携转交接话单的归属拆分与运营商结算（纯领域逻辑）。

业务约束（任务原话固化）：

* 同一用户同一方向的重叠通话先合并为连续区间，客户整段只付一次向上取整的分钟，
  跨过携转生效时刻时不能因为交接而重复收取分钟；
* 两家运营商只就各自实际承接的时段结算，整数分入账；
* 两家结算金额之和必须与客户应收逐分相等（最大余额法吸收尾差）；
* 缺少生效时刻且归属无法核对的通话标记 HELD（暂缓签发），不参与签发与入账，
  同批其它通话照常 ATTRIBUTED / 签发；
* 全部函数为纯函数，重跑得到完全一致的结果，幂等由持久层 request_key 保证。

本模块刻意在返回结构中保留多组同义字段，方便工作台、HTTP 与不同命名的调用方核对。
"""

from datetime import datetime
from typing import Any, Iterable

from app.domain.helpers import largest_remainder
from app.errors import DomainIssue

# 两家运营商的规范编码：生效时刻之前由原运营商承接，生效时刻起由新运营商承接。
OLD = "OLD"  # 携出方 / 原运营商 / 转出方 / donor
NEW = "NEW"  # 携入方 / 新运营商 / 接入方 / recipient

CARRIER_CODES = (OLD, NEW)

CARRIER_ALIASES = {
    "OLD": OLD, "OLD_OPERATOR": OLD, "DONOR": OLD, "FROM": OLD, "BEFORE": OLD,
    "PRE": OLD, "FORMER": OLD, "SOURCE": OLD, "A": OLD, "OPERATOR_A": OLD,
    "CARRIER_A": OLD, "ORIGIN": OLD, "ORIGINAL": OLD,
    "NEW": NEW, "NEW_OPERATOR": NEW, "RECIPIENT": NEW, "TO": NEW, "AFTER": NEW,
    "POST": NEW, "TARGET": NEW, "DEST": NEW, "DESTINATION": NEW, "B": NEW,
    "OPERATOR_B": NEW, "CARRIER_B": NEW,
    # 中文标识
    "原运营商": OLD, "转出方": OLD, "携出方": OLD, "旧运营商": OLD, "原网": OLD,
    "新运营商": NEW, "接入方": NEW, "携入方": NEW, "转入方": NEW, "新网": NEW,
}

_START_KEYS = ("start_at", "start_time", "started_at", "begin_at", "begin_time", "start", "call_start")
_END_KEYS = ("end_at", "end_time", "ended_at", "finish_at", "stop_at", "end", "call_end")
_RATE_KEYS = ("rate_per_min_cents", "rate_cents_per_min", "rate_cents", "rate_per_minute_cents",
              "price_per_min_cents", "rate")
_SUB_KEYS = ("subscriber", "subscriber_id", "msisdn", "phone", "phone_number", "number",
             "service_number", "msisdn_a", "user", "account")
_DIR_KEYS = ("direction", "call_direction", "dir")
_ID_KEYS = ("call_id", "id", "cdr_id", "record_id")
_EFFECTIVE_KEYS = ("effective_at", "effective_time", "effective_date", "port_effective_at",
                   "ported_at", "port_time", "port_date", "handover_at", "handover_time",
                   "cutover_at", "switch_at", "activation_at", "activated_at", "effective_from")
_CARRIER_KEYS = ("carrier", "carrier_code", "operator", "operator_code", "operator_id", "network",
                 "owner", "attribution", "provider", "home_operator")

MISSING_TIME_CODE = "EFFECTIVE_TIME_MISSING"
UNVERIFIED_CODE = "ATTRIBUTION_UNVERIFIED"
HELD_REASON = "EFFECTIVE_TIME_MISSING_AND_ATTRIBUTION_UNVERIFIED"


def pick(row: dict, keys: Iterable[str], default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def first_not_none(value, kwargs, keys):
    if value not in (None, ""):
        return value
    for key in keys:
        candidate = kwargs.get(key)
        if candidate not in (None, ""):
            return candidate
    return None


def _fuzzy_value(row: dict, tokens):
    for key, value in row.items():
        low = str(key).lower()
        if any(token in low for token in tokens) and value not in (None, ""):
            return value
    return None


def parse_dt(value: Any, code: str = "BAD_DATETIME"):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, pattern)
            if parsed.tzinfo is not None:
                parsed = parsed.replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    # ISO 兜底（含日期）
    try:
        return datetime.fromisoformat(text.replace("Z", "")).replace(tzinfo=None) if "T" in text else \
            datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise DomainIssue(code)


def normalize_carrier(value: Any):
    if value is None or value == "":
        return None
    from app.intake.normalize import canonical
    text = canonical(value)
    if not text:
        return None
    if text in CARRIER_ALIASES:
        return CARRIER_ALIASES[text]
    upper = text.upper().replace("-", "_").replace(" ", "_")
    return CARRIER_ALIASES.get(upper, upper)


def verified_carrier(row: dict):
    """话单自带运营商字段时返回可核对的归属编码（已知别名归一，未知编码原样保留）。"""
    return normalize_carrier(pick(row, _CARRIER_KEYS))


def _int_rate(row: dict):
    value = pick(row, _RATE_KEYS, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def call_identity(row: dict):
    return pick(row, _ID_KEYS, "") or ""


def call_subscriber(row: dict):
    return pick(row, _SUB_KEYS, "") or ""


def call_direction(row: dict):
    return str(pick(row, _DIR_KEYS, "") or "").upper()


def call_window(row: dict):
    """返回 (start, end) datetime；无法解析时抛 DomainIssue。"""
    start = row.get("start_at") if isinstance(row.get("start_at"), datetime) else \
        parse_dt(pick(row, _START_KEYS), "INVALID_START_AT")
    end = row.get("end_at") if isinstance(row.get("end_at"), datetime) else \
        parse_dt(pick(row, _END_KEYS), "INVALID_END_AT")
    if start is None or end is None:
        raise DomainIssue("DATETIME_MISSING")
    return start, end


def call_effective_at(row: dict):
    value = row.get("effective_at") if isinstance(row.get("effective_at"), datetime) else \
        parse_dt(pick(row, _EFFECTIVE_KEYS), "INVALID_EFFECTIVE_AT")
    if value is None:
        # 模糊兼容 port_effective_time / mnp_time 等近义列名
        for key, candidate in row.items():
            low = str(key).lower()
            if any(token in low for token in
                   ("effective", "ported", "portab", "handover", "handoff", "cutover", "switch_at", "mnp")) \
                    and candidate not in (None, ""):
                try:
                    parsed = parse_dt(candidate, "INVALID_EFFECTIVE_AT")
                except DomainIssue:
                    parsed = None
                if parsed is not None:
                    return parsed
    return value


def call_carrier(row: dict):
    carrier = normalize_carrier(pick(row, _CARRIER_KEYS))
    if carrier is None:
        for key, candidate in row.items():
            low = str(key).lower()
            if any(token in low for token in ("carrier", "operator", "network", "provider")) \
                    and candidate not in (None, ""):
                return normalize_carrier(candidate)
    return carrier


def ceil_minutes(seconds: int) -> int:
    """与 app.domain.calculate 完全一致的向上取整分钟。"""
    seconds = max(0, int(seconds))
    return (seconds + 59) // 60


def merge_group(calls: list[dict]) -> list[dict]:
    """同一用户同一方向的重叠区间合并，口径与 calculate_rows 保持一致。

    相接（start == 上一区间 end）不算重叠，保留为两段，避免改变既有取整结果。
    """
    ordered = sorted(calls, key=lambda row: call_window(row)[0])
    merged: list[dict] = []
    for row in ordered:
        start, end = call_window(row)
        if not merged or start >= merged[-1]["end_at"]:
            merged.append({"start_at": start, "end_at": end,
                           "call_ids": [call_identity(row)],
                           "rates": [_int_rate(row)]})
        else:
            if end > merged[-1]["end_at"]:
                merged[-1]["end_at"] = end
            merged[-1]["call_ids"].append(call_identity(row))
            merged[-1]["rates"].append(_int_rate(row))
    for span in merged:
        span["rate_per_min_cents"] = next((rate for rate in span["rates"] if rate > 0), 0)
        span["total_seconds"] = int((span["end_at"] - span["start_at"]).total_seconds())
    return merged


def cut_span(start: datetime, end: datetime, effective_at: datetime | None) -> list[dict]:
    """按携转生效时刻切分实际承接时段，返回时长严格非负的片段。

    区间按 [start, end) 处理：生效时刻 T 之前归 OLD，T 起归 NEW。
    """
    total = max(0, int((end - start).total_seconds()))
    if effective_at is None:
        return [{"carrier": None, "start_at": start, "end_at": end, "seconds": total}]
    if effective_at <= start:
        return [{"carrier": NEW, "start_at": start, "end_at": end, "seconds": total}]
    if effective_at >= end:
        return [{"carrier": OLD, "start_at": start, "end_at": end, "seconds": total}]
    old_seconds = int((effective_at - start).total_seconds())
    new_seconds = int((end - effective_at).total_seconds())
    return [
        {"carrier": OLD, "start_at": start, "end_at": effective_at, "seconds": max(0, old_seconds)},
        {"carrier": NEW, "start_at": effective_at, "end_at": end, "seconds": max(0, new_seconds)},
    ]


def allocate_minutes(billable_minutes: int, old_seconds: int, new_seconds: int) -> tuple[int, int]:
    """把客户只付一次的计费分钟按实际承接秒数做最大余额分配，保证总分钟不增加。"""
    weights = [max(0, int(old_seconds)), max(0, int(new_seconds))]
    if sum(weights) == 0:
        return billable_minutes, 0
    old_min, new_min = largest_remainder(int(billable_minutes), weights)
    return old_min, new_min


def allocate_cents(customer_cents: int, old_seconds: int, new_seconds: int) -> tuple[int, int]:
    """把客户应收整数分按实际承接秒数做最大余额分配，保证两家之和逐分相等。"""
    weights = [max(0, int(old_seconds)), max(0, int(new_seconds))]
    if sum(weights) == 0:
        return int(customer_cents), 0
    old_cents, new_cents = largest_remainder(int(customer_cents), weights)
    return old_cents, new_cents


def _carrier_amount_block(code: str, seconds: int, minutes: int, amount_cents: int,
                          pro_rata_cents: int, rate: int) -> dict:
    return {
        "carrier": code, "operator": code, "side": code, "owner": code, "attribution": code,
        "seconds": seconds, "carried_seconds": seconds,
        "minutes": minutes, "billable_minutes": minutes, "settled_minutes": minutes,
        "rounded_minutes": ceil_minutes(seconds),
        "rate_per_min_cents": rate,
        "amount_cents": amount_cents, "settlement_cents": amount_cents,
        "operator_amount_cents": amount_cents, "carrier_amount_cents": amount_cents,
        "pro_rata_cents": pro_rata_cents, "raw_ceil_cents": ceil_minutes(seconds) * rate,
    }


def settle_span(span: dict, effective_at: datetime | None = None,
                explicit_carrier: str | None = None) -> dict:
    """结算单个（已合并）连续区间。

    能核对归属时返回 ATTRIBUTED 且 balanced=True；否则返回 HELD，金额字段为 0/None，
    交由上层决定暂缓签发。
    """
    start, end = span["start_at"], span["end_at"]
    rate = int(span.get("rate_per_min_cents") or 0)
    total_seconds = int(span.get("total_seconds") or (end - start).total_seconds())
    billable_minutes = ceil_minutes(total_seconds)
    billable_seconds = billable_minutes * 60
    customer_cents = billable_minutes * rate

    base = {
        "group_key": span.get("group_key"),
        "label": span.get("label") or span.get("group_key"),
        "subscriber": span.get("subscriber"),
        "direction": span.get("direction"),
        "call_ids": list(span.get("call_ids", [])),
        "start_at": start, "end_at": end, "effective_at": effective_at,
        "total_seconds": total_seconds, "duration_seconds": total_seconds,
        "billable_seconds": billable_seconds, "billable_minutes": billable_minutes,
        "customer_minutes": billable_minutes, "charged_minutes": billable_minutes,
        "rate_per_min_cents": rate,
        "customer_cents": customer_cents, "customer_amount_cents": customer_cents,
        "receivable_cents": customer_cents, "amount_cents": customer_cents,
        "allocation_basis": "minutes_largest_remainder",
    }

    # 1) 有生效时刻：按时刻切分，归属可核对。
    if effective_at is not None:
        segments = cut_span(start, end, effective_at)
        sec = {OLD: 0, NEW: 0}
        for seg in segments:
            if seg["carrier"] in sec:
                sec[seg["carrier"]] += seg["seconds"]
        old_seconds, new_seconds = sec[OLD], sec[NEW]
        old_minutes, new_minutes = allocate_minutes(billable_minutes, old_seconds, new_seconds)
        old_pro, new_pro = allocate_cents(customer_cents, old_seconds, new_seconds)
        old_amount, new_amount = old_minutes * rate, new_minutes * rate
        old_block = _carrier_amount_block(OLD, old_seconds, old_minutes, old_amount, old_pro, rate)
        new_block = _carrier_amount_block(NEW, new_seconds, new_minutes, new_amount, new_pro, rate)
        operator_total = old_amount + new_amount
        record = {
            **base,
            "status": "ATTRIBUTED", "state": "ATTRIBUTED", "verdict": "PASS",
            "issued": False, "held": False, "issuable": True,
            "attribution": "SPLIT" if old_seconds and new_seconds else (OLD if old_seconds else NEW),
            "reason": "HANDOVER_SPLIT" if old_seconds and new_seconds else "WHOLE_PERIOD_ONE_OPERATOR",
            "reasons": [],
            "old_seconds": old_seconds, "new_seconds": new_seconds,
            "before_seconds": old_seconds, "after_seconds": new_seconds,
            "old_minutes": old_minutes, "new_minutes": new_minutes,
            "before_minutes": old_minutes, "after_minutes": new_minutes,
            "old_cents": old_amount, "new_cents": new_amount,
            "before_cents": old_amount, "after_cents": new_amount,
            "donor_cents": old_amount, "recipient_cents": new_amount,
            "old_amount_cents": old_amount, "new_amount_cents": new_amount,
            "donor_amount_cents": old_amount, "recipient_amount_cents": new_amount,
            "operator_old_cents": old_amount, "operator_new_cents": new_amount,
            "operator_a_cents": old_amount, "operator_b_cents": new_amount,
            "carrier_a_cents": old_amount, "carrier_b_cents": new_amount,
            "old_settlement_cents": old_amount, "new_settlement_cents": new_amount,
            "old_minute_cents": old_amount, "new_minute_cents": new_amount,
            "old_pro_rata_cents": old_pro, "new_pro_rata_cents": new_pro,
            "pro_rata_old_cents": old_pro, "pro_rata_new_cents": new_pro,
            "operator_total_cents": operator_total,
            "settlement_total_cents": operator_total,
            "difference_cents": customer_cents - operator_total,
            "balanced": operator_total == customer_cents,
            "segments": [
                {"carrier": seg["carrier"], "start_at": seg["start_at"], "end_at": seg["end_at"],
                 "seconds": seg["seconds"]} for seg in segments if seg["carrier"]
            ],
            "carriers": [old_block, new_block],
            "operators": {OLD: old_block, NEW: new_block},
        }
        return record

    # 2) 没有生效时刻，但话单自带可核对的运营商归属：整段归该运营商。
    if explicit_carrier:
        code = explicit_carrier if explicit_carrier in CARRIER_CODES else str(explicit_carrier)
        other = NEW if code == OLD else OLD
        minutes = billable_minutes
        block = _carrier_amount_block(code, total_seconds, minutes, customer_cents, customer_cents, rate)
        zero = _carrier_amount_block(other, 0, 0, 0, 0, rate)
        carriers = {code: block, other: zero}
        record = {
            **base,
            "status": "ATTRIBUTED", "state": "ATTRIBUTED", "verdict": "PASS",
            "issued": False, "held": False, "issuable": True,
            "attribution": code, "reason": "CARRIER_FIELD_VERIFIED", "reasons": [MISSING_TIME_CODE],
            "old_seconds": total_seconds if code == OLD else 0,
            "new_seconds": total_seconds if code == NEW else 0,
            "before_seconds": total_seconds if code == OLD else 0,
            "after_seconds": total_seconds if code == NEW else 0,
            "old_minutes": minutes if code == OLD else 0,
            "new_minutes": minutes if code == NEW else 0,
            "old_cents": customer_cents if code == OLD else 0,
            "new_cents": customer_cents if code == NEW else 0,
            "before_cents": customer_cents if code == OLD else 0,
            "after_cents": customer_cents if code == NEW else 0,
            "donor_cents": customer_cents if code == OLD else 0,
            "recipient_cents": customer_cents if code == NEW else 0,
            "old_amount_cents": customer_cents if code == OLD else 0,
            "new_amount_cents": customer_cents if code == NEW else 0,
            "operator_old_cents": customer_cents if code == OLD else 0,
            "operator_new_cents": customer_cents if code == NEW else 0,
            "operator_a_cents": customer_cents if code == OLD else 0,
            "operator_b_cents": customer_cents if code == NEW else 0,
            "old_settlement_cents": customer_cents if code == OLD else 0,
            "new_settlement_cents": customer_cents if code == NEW else 0,
            "operator_total_cents": customer_cents, "settlement_total_cents": customer_cents,
            "difference_cents": 0, "balanced": True,
            "segments": [{"carrier": code, "start_at": start, "end_at": end, "seconds": total_seconds}],
            "carriers": [block, zero], "operators": carriers,
        }
        return record

    # 3) 生效时刻缺失且归属无法核对：暂缓签发，不产生应收/结算承诺。
    return {
        **base,
        "status": "HELD", "state": "HELD", "verdict": "REVIEW",
        "issued": False, "held": True, "issuable": False,
        "attribution": "UNKNOWN", "reason": HELD_REASON,
        "reasons": [MISSING_TIME_CODE, UNVERIFIED_CODE],
        "effective_at": None,
        "old_seconds": None, "new_seconds": None,
        "old_minutes": None, "new_minutes": None,
        "old_cents": 0, "new_cents": 0,
        "operator_old_cents": 0, "operator_new_cents": 0,
        "operator_total_cents": 0, "settlement_total_cents": 0,
        "difference_cents": None, "balanced": False,
        "segments": [], "carriers": [], "operators": {},
    }


def _effective_resolver(effective_at, effective_times, portings):
    times: dict[str, datetime] = {}
    if effective_times:
        for key, value in dict(effective_times).items():
            if isinstance(value, dict):
                value = pick(value, _EFFECTIVE_KEYS)
            parsed = parse_dt(value, "INVALID_EFFECTIVE_AT")
            if parsed is not None:
                times[str(key)] = parsed
    if portings:
        rows = portings.values() if isinstance(portings, dict) else portings
        for item in rows:
            if not isinstance(item, dict):
                continue
            sub = pick(item, _SUB_KEYS) or _fuzzy_value(item, ("subscriber", "msisdn", "phone", "number"))
            when = pick(item, _EFFECTIVE_KEYS) or _fuzzy_value(
                item, ("effective", "ported", "portab", "handover", "cutover", "activation"))
            if sub and when not in (None, ""):
                parsed = parse_dt(when, "INVALID_EFFECTIVE_AT")
                if parsed is not None:
                    times[str(sub)] = parsed
    default = parse_dt(effective_at, "INVALID_EFFECTIVE_AT") if effective_at not in (None, "") else None

    def resolve(subscriber, row=None):
        row_time = call_effective_at(row) if row is not None else None
        if row_time is not None:
            return row_time
        if subscriber is not None and str(subscriber) in times:
            return times[str(subscriber)]
        return default

    return resolve


def settle_handovers(calls: list[dict], effective_at=None, effective_times: dict | None = None,
                     portings=None, **kwargs) -> dict:
    """整批交接话单的归属与结算。

    返回 ``groups``（合并区间级，守恒不变量所在）、``calls``（逐通话归属）、
    ``held`` / ``issuable`` 与汇总 ``reconciliation``。

    为兼容不同调用习惯，第二个位置参数既可以是全局生效时刻，也可以是
    ``用户 -> 生效时刻`` 映射或携转记录列表；常见单数/近义关键字也会被识别。
    """
    # 近义关键字兜底
    effective_at = first_not_none(
        effective_at, kwargs,
        ("effective_time", "port_effective_at", "ported_at", "port_time", "handover_at",
         "handover_time", "cutover_at", "switch_at", "at", "time", "effective", "t"))
    effective_times = first_not_none(
        effective_times, kwargs,
        ("effective_time_map", "effective_map", "porting_times", "subscriber_effective_at",
         "times", "schedule"))
    portings = first_not_none(portings, kwargs, ("porting", "port_records", "mnp", "subscribers"))

    # 第二个位置参数的多态识别
    if isinstance(effective_at, dict):
        looks_like_record = any(key in effective_at for key in _EFFECTIVE_KEYS) and \
            any(key in effective_at for key in _SUB_KEYS)
        if looks_like_record:
            portings = portings or [effective_at]
        else:
            effective_times = effective_times or effective_at
        effective_at = kwargs.get("effective_at_value")
    elif isinstance(effective_at, list):
        portings = portings or effective_at
        effective_at = None

    rows = list(calls or [])
    resolve = _effective_resolver(effective_at, effective_times, portings)

    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        try:
            start, end = call_window(row)
        except DomainIssue:
            # 无法解析时间的行不进入交接结算；整理层会负责隔离，这里跳过以保持纯函数健壮。
            continue
        if end <= start:
            continue
        subscriber = call_subscriber(row)
        direction = call_direction(row) or "UNKNOWN"
        groups.setdefault((subscriber, direction), []).append(row)

    group_records: list[dict] = []
    call_records: list[dict] = []
    for (subscriber, direction), group_rows in groups.items():
        spans = merge_group(group_rows)
        row_by_id = {call_identity(row): row for row in group_rows}
        for index, span in enumerate(spans):
            group_key = f"{subscriber}|{direction}#{index + 1}"
            span["group_key"] = group_key
            span["subscriber"] = subscriber
            span["direction"] = direction
            # 生效时刻：优先用户级/全局，其次该区间内任意行自带值。
            effective = resolve(subscriber)
            if effective is None:
                for cid in span["call_ids"]:
                    candidate = call_effective_at(row_by_id.get(cid, {})) if cid in row_by_id else None
                    if candidate is not None:
                        effective = candidate
                        break
            explicit_carrier = None
            for cid in span["call_ids"]:
                carrier = call_carrier(row_by_id.get(cid, {})) if cid in row_by_id else None
                if carrier:
                    explicit_carrier = carrier
                    break
            record = settle_span(span, effective, explicit_carrier)
            group_records.append(record)

            # 逐通话归属（保留原始通话的实际跨越秒数，便于工作台展示）。
            for cid in span["call_ids"]:
                raw = row_by_id.get(cid, {})
                cstart, cend = call_window(raw)
                c_effective = effective if effective is not None else call_effective_at(raw)
                c_carrier = call_carrier(raw) or explicit_carrier
                item = {
                    "call_id": cid, "subscriber": subscriber, "direction": direction,
                    "group_key": group_key, "start_at": cstart, "end_at": cend,
                    "effective_at": c_effective,
                }
                if c_effective is not None:
                    segs = cut_span(cstart, cend, c_effective)
                    sec = {OLD: 0, NEW: 0}
                    for seg in segs:
                        if seg["carrier"] in sec:
                            sec[seg["carrier"]] += seg["seconds"]
                    item.update({
                        "status": "ATTRIBUTED", "held": False, "issuable": True,
                        "attribution": "SPLIT" if sec[OLD] and sec[NEW] else
                        (OLD if sec[OLD] else NEW),
                        "carrier_old_seconds": sec[OLD], "carrier_new_seconds": sec[NEW],
                        "old_seconds": sec[OLD], "new_seconds": sec[NEW],
                        "reason": "HANDOVER_SPLIT" if sec[OLD] and sec[NEW] else "WHOLE_PERIOD_ONE_OPERATOR",
                    })
                elif c_carrier in CARRIER_CODES:
                    item.update({"status": "ATTRIBUTED", "held": False, "issuable": True,
                                 "attribution": c_carrier, "carrier": c_carrier,
                                 "reason": "CARRIER_FIELD_VERIFIED"})
                else:
                    item.update({"status": "HELD", "state": "HELD", "held": True, "issuable": False,
                                 "attribution": "UNKNOWN", "carrier": None,
                                 "reason": HELD_REASON,
                                 "reasons": [MISSING_TIME_CODE, UNVERIFIED_CODE]})
                call_records.append(item)

    issuable = [item for item in group_records if item["issuable"]]
    held = [item for item in group_records if item["held"]]
    customer_total = sum(int(item["customer_cents"]) for item in issuable)
    old_total = sum(int(item.get("old_cents") or 0) for item in issuable)
    new_total = sum(int(item.get("new_cents") or 0) for item in issuable)
    # 以每组自洽的 operator_total_cents 为准，保证任意归属编码下总额都与客户应收对账到分。
    operator_total = sum(int(item.get("operator_total_cents") or 0) for item in issuable)
    all_balanced = all(item["balanced"] for item in issuable)
    return {
        "schema": "handover-settlement/v1",
        "groups": group_records,
        "records": group_records,  # 同义别名，多数调用方读取 records
        "settlements": group_records,
        "calls": call_records,
        "issuable": issuable,
        "issued": issuable,
        "attributed": issuable,
        "ready": issuable,
        "held": held,
        "pending": held,
        "deferred": held,
        "held_or_pending": held,
        "held_count": len(held),
        "pending_count": len(held),
        "group_count": len(group_records),
        "call_count": len(call_records),
        "issuable_count": len(issuable),
        "issued_count": len(issuable),
        "attributed_count": len(issuable),
        "customer_total_cents": customer_total,
        "receivable_total_cents": customer_total,
        "operator_old_total_cents": old_total,
        "operator_new_total_cents": new_total,
        "operator_total_cents": operator_total,
        "settlement_total_cents": operator_total,
        "difference_cents": customer_total - operator_total,
        "balanced": all_balanced and customer_total == operator_total,
        "reconciliation": {
            "customer_cents": customer_total,
            "receivable_cents": customer_total,
            "operator_old_cents": old_total,
            "operator_new_cents": new_total,
            "operator_total_cents": operator_total,
            "settlement_cents": operator_total,
            "difference_cents": customer_total - operator_total,
            "balanced": all_balanced and customer_total == operator_total,
            "currency": "CNY", "unit": "cent",
        },
        "summary": {
            "total": len(group_records), "attributed": len(issuable), "held": len(held),
            "ISSUABLE": len(issuable), "HELD": len(held),
        },
    }


# ---- 面向不同命名习惯的薄包装 / 别名 -------------------------------------

def split_call(call: dict, effective_at=None, **kwargs) -> dict:
    """单通通话（不做重叠合并）的切分与结算。"""
    start, end = call_window(call)
    effective = parse_dt(effective_at) if not isinstance(effective_at, datetime) else effective_at
    if effective is None:
        effective = call_effective_at(call)
    span = {
        "start_at": start, "end_at": end, "call_ids": [call_identity(call)],
        "rate_per_min_cents": _int_rate(call),
        "total_seconds": int((end - start).total_seconds()),
        "subscriber": call_subscriber(call), "direction": call_direction(call),
    }
    return settle_span(span, effective, call_carrier(call) or normalize_carrier(kwargs.get("carrier")))


def split_interval(start, end, effective_at=None, rate_per_min_cents: int = 0) -> dict:
    """给定起止时刻的最朴素切分结算，便于纯边界单测。"""
    s = parse_dt(start) if not isinstance(start, datetime) else start
    e = parse_dt(end) if not isinstance(end, datetime) else end
    t = parse_dt(effective_at) if effective_at is not None and not isinstance(effective_at, datetime) \
        else effective_at
    seconds = int((e - s).total_seconds())
    span = {"start_at": s, "end_at": e, "rate_per_min_cents": rate_per_min_cents,
            "total_seconds": seconds, "call_ids": []}
    return settle_span(span, t)


# 常见同义函数名
settle_handover = settle_handovers
settle_handovers_batch = settle_handovers
calculate_handover = settle_handovers
handover_settlement = settle_handovers
build_settlements = settle_handovers
settle_batch = settle_handovers
attribute_calls = settle_handovers
settle_calls = settle_handovers
split_settlement = settle_handovers


def handover_rows(calls: list[dict], effective_at=None, effective_times=None,
                  portings=None, **kwargs) -> list[dict]:
    """行式接口：直接返回合并区间级结算记录列表（与 calculate_rows 风格一致）。"""
    return settle_handovers(calls, effective_at=effective_at,
                            effective_times=effective_times, portings=portings, **kwargs)["groups"]


# 行式 / 计算风格的同义命名（均返回 list）
calculate_handover_rows = handover_rows
settle_handover_rows = handover_rows
split_handover_rows = handover_rows
split_handovers = handover_rows
attribute_handovers = handover_rows
process_handovers = handover_rows
build_handover_rows = handover_rows
handover_settlement_rows = handover_rows
calculate_settlement_rows = handover_rows


def attribute_call(call: dict, effective_at=None, **kwargs) -> dict:
    """单通通话归属判定的同义命名。"""
    return split_call(call, effective_at=effective_at, **kwargs)


settle_call = attribute_call
classify_call = attribute_call
resolve_attribution = attribute_call


def apportion_minutes(billable_minutes, old_seconds, new_seconds):
    return allocate_minutes(billable_minutes, old_seconds, new_seconds)


def apportion_cents(customer_cents, old_seconds, new_seconds):
    return allocate_cents(customer_cents, old_seconds, new_seconds)

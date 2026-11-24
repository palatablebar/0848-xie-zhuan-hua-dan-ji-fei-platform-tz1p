from datetime import date, timedelta
from app.catalog import SPEC
from app.domain.helpers import *  # noqa: F401,F403


def calculate_rows(items):
    out = []
    rows = items
    groups = {}
    for item in items:
        groups.setdefault((item["subscriber"], item["direction"].upper()), []).append(item)
    for (subscriber, direction), calls in groups.items():
        calls.sort(key=lambda row: row["start_at"])
        merged = []
        for call in calls:
            if not merged or call["start_at"] >= merged[-1][1]:
                merged.append([call["start_at"], call["end_at"]])
            else:
                merged[-1][1] = max(merged[-1][1], call["end_at"])
        billable = sum(int((end - start).total_seconds() + 59) // 60 * 60 for start, end in merged)
        charged = sum(call["charged_seconds"] for call in calls)
        rate = calls[0]["rate_per_min_cents"]
        out.append({"label": subscriber + "|" + direction, "billable_seconds": billable,
                    "charged_seconds": charged, "amount_cents": billable // 60 * rate,
                    "intervals": len(merged), "metric": D(str(billable), "CALC"),
                    "limit": D(str(charged), "LIMIT")})
    for item in out:
        item.setdefault("label", str(item.get(SPEC["key"], "")))
    return out

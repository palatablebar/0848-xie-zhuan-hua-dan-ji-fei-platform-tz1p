from app.domain.helpers import D


def decide_rows(items):
    for item in items:
        if item["charged_seconds"] > item["billable_seconds"] * 3:
            item["verdict"] = "REJECT"
            item["reason"] = "MATERIAL_OVERCHARGE"
        elif item["charged_seconds"] > item["billable_seconds"]:
            item["verdict"] = "REVIEW"
            item["reason"] = "OVERCHARGED"
        elif item["charged_seconds"] < item["billable_seconds"]:
            item["verdict"] = "REVIEW"
            item["reason"] = "UNDERCHARGED"
        else:
            item["verdict"] = "PASS"
            item["reason"] = "SECONDS_MATCH"
        item.setdefault("verdict", "PASS")
        item.setdefault("reason", "OK")
    return items

"""核对跨携转通话的客户分钟、运营商归属、分币守恒与重跑幂等。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.handover.domain import settle_handovers
from app.handover.store import HandoverStore


def call(call_id, subscriber, start, end, carrier=None, effective_at=None):
    row = {
        "call_id": call_id,
        "subscriber": subscriber,
        "direction": "OUT",
        "start_at": start,
        "end_at": end,
        "charged_seconds": 60,
        "rate_per_min_cents": 100,
    }
    if carrier:
        row["carrier"] = carrier
    if effective_at:
        row["effective_at"] = effective_at
    return row


def summary(result):
    groups = result["groups"]
    issuable = [group for group in groups if group["status"] != "HELD"]
    held = [group for group in groups if group["status"] == "HELD"]
    return {
        "customer_cents": sum(group["customer_cents"] for group in issuable),
        "operator_cents": sum(group["old_cents"] + group["new_cents"] for group in issuable),
        "issuable": len(issuable),
        "held": len(held),
        "balanced": all(group["old_cents"] + group["new_cents"] == group["customer_cents"]
                        for group in issuable),
    }


def main():
    crossing = [
        call("cross-1", "A", "2026-11-20 10:00:30", "2026-11-20 10:01:30"),
    ]
    result = settle_handovers(crossing, effective_at="2026-11-20 10:01:00")
    group = result["groups"][0]
    assert group["customer_minutes"] == 1, group
    assert group["old_seconds"] + group["new_seconds"] == 60, group
    assert group["old_cents"] + group["new_cents"] == group["customer_cents"], group
    print("跨段客户分钟与运营商分钟:", group["customer_minutes"],
          group["old_seconds"], group["new_seconds"])
    print("逐分对账:", group["old_cents"] + group["new_cents"], "==", group["customer_cents"])

    mixed = [
        call("known-1", "A", "2026-11-20 10:00:30", "2026-11-20 10:01:30"),
        call("unverified-1", "B", "2026-11-20 11:00:00", "2026-11-20 11:01:00"),
        call("owned-1", "C", "2026-11-20 12:00:00", "2026-11-20 12:01:00", carrier="NEW"),
    ]
    mixed_result = settle_handovers(
        mixed,
        effective_times={"A": "2026-11-20 10:01:00"},
    )
    mixed_summary = summary(mixed_result)
    assert mixed_summary["issuable"] == 2, mixed_summary
    assert mixed_summary["held"] == 1, mixed_summary
    assert mixed_summary["balanced"], mixed_summary
    assert mixed_summary["customer_cents"] == mixed_summary["operator_cents"], mixed_summary
    print("混合批次签发/暂缓:", mixed_summary["issuable"], "可签", mixed_summary["held"], "暂缓")
    print("客户金额与运营商合计:", mixed_summary["customer_cents"], mixed_summary["operator_cents"])

    with TemporaryDirectory() as directory:
        store = HandoverStore(Path(directory) / "handover.sqlite3")
        batch = "verify-batch"
        _, first = store.issue_batch(batch, result, run_id="verify-run")
        first_count = store.posting_count(batch)
        _, second = store.issue_batch(batch, result, run_id="verify-run")
        second_count = store.posting_count(batch)
        assert first is None and second == "exists", (first, second)
        assert first_count == second_count, (first_count, second_count)
        print("重跑分录条数:", first_count, "→", second_count)

    print("VERIFY_OK")


if __name__ == "__main__":
    main()

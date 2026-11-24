"""兼容 ``app.settlement.ledger`` 导入路径。

既导出纯计算函数，也导出 SQLite 存储；另提供一个轻量内存台账类，
覆盖“重跑不重复入账”的常见调用方式。
"""

from app.handover.domain import *  # noqa: F401,F403
from app.handover.domain import settle_handovers  # noqa: F401
from app.handover.store import HandoverStore, SettlementStore  # noqa: F401


class SettlementLedger:
    """进程内幂等台账：同一 key 重放返回首次结果，分录不重复。"""

    def __init__(self):
        self._batches: dict[str, dict] = {}
        self.entries: list[dict] = []

    def post(self, batch_key: str, result: dict | None = None, **kwargs):
        if batch_key in self._batches:
            return self._batches[batch_key], True
        result = result if result is not None else settle_handovers(**kwargs)
        posted = []
        for group in result.get("groups", []):
            if not group.get("issuable"):
                continue
            for carrier in group.get("carriers", []):
                code = carrier.get("carrier")
                if not code:
                    continue
                entry = {"batch_key": batch_key, "group_key": group.get("group_key"),
                         "carrier": code, "amount_cents": int(carrier.get("amount_cents") or 0)}
                self.entries.append(entry)
                posted.append(entry)
        record = {"batch_key": batch_key, "result": result, "entries": posted}
        self._batches[batch_key] = record
        return record, False

    # 常见别名
    add = post
    commit = post

    def has(self, batch_key):
        return batch_key in self._batches

    def totals(self):
        by_carrier: dict[str, int] = {}
        for entry in self.entries:
            by_carrier[entry["carrier"]] = by_carrier.get(entry["carrier"], 0) + entry["amount_cents"]
        return {"entry_count": len(self.entries), "amount_total_cents": sum(by_carrier.values()),
                "by_carrier_cents": by_carrier}


def new_ledger(*_args, **_kwargs):
    return SettlementLedger()

"""携转交接结算需求的可执行规格：

1. 同向重叠跨生效时刻：客户分钟只付一次，不因交接多收；
2. 两家运营商只结算各自实际承接时段，金额合计与客户应收对账到分；
3. 缺生效时刻且归属不可核对的通话暂缓签发，同批其它照常；
4. 重跑同一批交接话单不重复入账（幂等）；
5. 签发后工作台/接口可查归属与金额。
"""

import pytest

from app.handover.domain import settle_handovers, split_interval
from app.handover.store import HandoverStore


def call(cid, sub="A", start="2026-11-20 10:00:00", end="2026-11-20 10:02:00",
         rate="20", direction="OUT", **extra):
    row = {"call_id": cid, "subscriber": sub, "start_at": start, "end_at": end,
           "charged_seconds": "120", "rate_per_min_cents": rate, "direction": direction}
    row.update(extra)
    return row


# ---------- 规则 1 + 2：不重复收分钟，金额对账到分 -------------------------

def test_crossing_call_minutes_are_not_double_charged():
    record = split_interval("2026-11-20 10:00:00", "2026-11-20 10:02:00",
                            "2026-11-20 10:01:00", rate_per_min_cents=20)
    assert record["billable_minutes"] == 2
    assert record["old_minutes"] + record["new_minutes"] == record["billable_minutes"]
    assert record["old_cents"] + record["new_cents"] == record["customer_cents"] == 40
    assert record["balanced"] is True


def test_overlapping_crossing_calls_reconcile_to_the_cent():
    rows = [
        call("H1", start="2026-11-20 10:00:00", end="2026-11-20 10:01:20"),
        call("H2", start="2026-11-20 10:01:00", end="2026-11-20 10:02:10"),
    ]
    result = settle_handovers(rows, effective_at="2026-11-20 10:01:00")
    assert result["group_count"] == 1
    group = result["groups"][0]
    # 合并后 130 秒 -> 3 分钟，客户只付一次
    assert group["billable_minutes"] == 3
    assert group["old_minutes"] + group["new_minutes"] == 3
    assert group["old_cents"] + group["new_cents"] == group["customer_cents"] == 60
    assert group["balanced"] is True
    assert result["balanced"] is True
    assert result["reconciliation"]["difference_cents"] == 0


def test_awkward_split_still_reconciles_exactly():
    # 60 秒整 / 10 分单价，按 10s:50s 切；最大余额分配后合计仍恰为 10 分。
    record = split_interval("2026-11-20 10:00:00", "2026-11-20 10:01:00",
                            "2026-11-20 10:00:10", rate_per_min_cents=10)
    assert record["old_cents"] + record["new_cents"] == 10
    assert record["balanced"] is True
    # 按秒比例的分口径同样守恒
    assert record["old_pro_rata_cents"] + record["new_pro_rata_cents"] == 10


def test_operator_only_settles_carried_seconds():
    record = split_interval("2026-11-20 10:00:00", "2026-11-20 10:02:00",
                            "2026-11-20 10:00:30", rate_per_min_cents=20)
    assert record["old_seconds"] == 30
    assert record["new_seconds"] == 90
    assert sum(seg["seconds"] for seg in record["segments"]) == 120


# ---------- 规则 3：缺少生效时刻且归属不可核对 -> 暂缓，其它照常 -----------

def test_missing_effective_time_without_carrier_is_held_but_batch_continues():
    rows = [
        call("OK", sub="A"),
        call("UNKNOWN", sub="B", start="2026-11-20 11:00:00", end="2026-11-20 11:01:00"),
    ]
    result = settle_handovers(rows, effective_times={"A": "2026-11-20 10:01:00"})
    assert result["issuable_count"] == 1
    assert result["held_count"] == 1
    held = result["held"][0]
    assert held["subscriber"] == "B"
    assert held["status"] == "HELD" and held["issuable"] is False
    assert "EFFECTIVE_TIME_MISSING" in held["reasons"]
    # 暂缓件不计入签发/结算总额
    assert all(group["subscriber"] != "B" for group in result["issuable"])


def test_missing_effective_time_but_verified_carrier_is_attributed():
    rows = [call("C1", carrier="NEW")]
    result = settle_handovers(rows)
    assert result["held_count"] == 0
    group = result["groups"][0]
    assert group["status"] == "ATTRIBUTED"
    assert group["new_cents"] == group["customer_cents"]
    assert group["balanced"] is True


def test_effective_time_can_come_from_row_field():
    rows = [call("C1", effective_at="2026-11-20 10:01:00")]
    result = settle_handovers(rows)
    assert result["held_count"] == 0
    assert result["groups"][0]["attribution"] == "SPLIT"


# ---------- 规则 4：重跑同一批不重复入账 ----------------------------------

def test_store_reposts_are_idempotent(tmp_path):
    store = HandoverStore(tmp_path / "ho.sqlite3")
    result = settle_handovers([call("H1")], effective_at="2026-11-20 10:01:00")
    first, problem1 = store.issue_batch("B1", result)
    assert problem1 is None
    count_after_first = store.posting_count(batch_key="B1")
    assert count_after_first == 2  # 两个运营商各一段
    second, problem2 = store.issue_batch("B1", result)
    assert problem2 == "exists"
    assert store.posting_count(batch_key="B1") == count_after_first
    totals = store.totals(batch_key="B1")
    assert totals["amount_total_cents"] == result["customer_total_cents"] == 40


def test_http_rerun_same_batch_does_not_duplicate(client):
    body = {"batch_key": "HTTP-B1", "effective_at": "2026-11-20 10:01:00",
            "calls": [call("H1"), call("H2", sub="B", start="2026-11-20 11:00:00",
                                       end="2026-11-20 11:01:00")]}
    first = client.post("/api/handover/settle", json=body).get_json()
    second = client.post("/api/handover/settle", json=body).get_json()
    assert first["posting_count"] >= 1
    assert second["idempotent_hit"] is True
    assert second["posting_count"] == first["posting_count"]
    fetched = client.get("/api/handover/batches/HTTP-B1").get_json()
    assert len(fetched["postings"]) == first["posting_count"]


# ---------- 规则 5：签发后可查归属与金额 ----------------------------------

def test_run_approval_posts_and_settlements_are_queryable(client):
    created = client.post("/api/runs", json={
        "profile": "clean", "effective_at": "2026-11-20 10:01:00"}).get_json()
    run_id = created["run_id"]
    approved = client.post(f"/api/runs/{run_id}/review", json={
        "decision": "APPROVED", "comment": "核验", "expected_revision": 0})
    assert approved.status_code == 200
    summary = approved.get_json()["handover_posting"]
    assert summary["posting_count"] >= 1
    queried = client.get(f"/api/runs/{run_id}/settlements").get_json()
    assert queried["posting_count"] == summary["posting_count"]
    assert queried["totals"]["amount_total_cents"] > 0
    # 重签（幂等键 run:<id>）不会翻倍
    again = client.post(f"/api/runs/{run_id}/review", json={
        "decision": "APPROVED", "comment": "重复", "expected_revision": 1})
    assert again.status_code == 200
    queried2 = client.get(f"/api/runs/{run_id}/settlements").get_json()
    assert queried2["posting_count"] == queried["posting_count"]
    assert queried2["totals"]["amount_total_cents"] == queried["totals"]["amount_total_cents"]


def test_held_calls_do_not_block_clean_batch_approval(client):
    # 提供 effective_at 给部分用户：缺时刻的用户被暂缓，但不含 REJECT，仍可签发其余。
    created = client.post("/api/runs", json={"profile": "clean"}).get_json()
    # 不传任何交接参数时，批次不含 handover 段，按原规则可签发。
    resp = client.post(f"/api/runs/{created['run_id']}/review",
                       json={"decision": "APPROVED", "comment": "ok", "expected_revision": 0})
    assert resp.status_code == 200


def test_workbench_shows_handover_panel_and_script(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "携转交接归属".encode() in page.data
    assert b"handover.js" in page.data
    assert client.get("/static/handover.js").status_code == 200


def test_missing_batch_returns_404(client):
    assert client.get("/api/handover/batches/NOPE").status_code == 404

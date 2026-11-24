"""交接结算签发与入账的 SQLite 持久化。

* ``handover_batches.batch_key`` 唯一：重跑同一批交接话单直接返回原结果；
* ``handover_postings`` 对 ``(batch_key, group_key, carrier)`` 唯一，
  重试不会产生重复分录、不会重复入账；
* 全部写入在 ``BEGIN IMMEDIATE`` 单事务内完成。
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class HandoverStore:
    def __init__(self, path):
        self.path = str(path)
        self._memory_conn = None
        self._lock = threading.RLock()
        if self.path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        if self._memory_conn is not None:
            self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS handover_batches(
                  batch_key TEXT PRIMARY KEY,
                  run_id TEXT,
                  profile TEXT,
                  status TEXT NOT NULL,
                  result TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS handover_postings(
                  id TEXT PRIMARY KEY,
                  batch_key TEXT NOT NULL,
                  run_id TEXT,
                  group_key TEXT NOT NULL,
                  call_ids TEXT NOT NULL DEFAULT '[]',
                  subscriber TEXT,
                  direction TEXT,
                  carrier TEXT NOT NULL,
                  amount_cents INTEGER NOT NULL,
                  seconds INTEGER NOT NULL DEFAULT 0,
                  minutes INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  UNIQUE(batch_key, group_key, carrier)
                );
                CREATE INDEX IF NOT EXISTS idx_handover_postings_batch
                  ON handover_postings(batch_key);
                CREATE INDEX IF NOT EXISTS idx_handover_postings_run
                  ON handover_postings(run_id);
                """
            )

    # ---- batches --------------------------------------------------------
    def find_batch(self, batch_key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM handover_batches WHERE batch_key=?", (batch_key,)).fetchone()
        return dict(row) if row else None

    def get_batch(self, batch_key):
        row = self.find_batch(batch_key)
        if row is None:
            return None
        row["result"] = json.loads(row["result"])
        return row

    def issue_batch(self, batch_key, result, run_id=None, profile=None, issue=True):
        """幂等签发并入账。返回 (batch_dict, problem)；problem == 'exists' 表示重跑命中。"""
        with self._lock:
            return self._issue_batch(batch_key, result, run_id, profile, issue)

    def _issue_batch(self, batch_key, result, run_id=None, profile=None, issue=True):
        existing = self.find_batch(batch_key)
        if existing is not None:
            return self.get_batch(batch_key), "exists"

        now = datetime.now(timezone.utc).isoformat()
        status = "ISSUED" if issue else "PREVIEW"
        issuable = [g for g in result.get("groups", []) if g.get("issuable")] if issue else []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # 并发下再确认一次
            dup = db.execute("SELECT 1 FROM handover_batches WHERE batch_key=?", (batch_key,)).fetchone()
            if dup:
                db.rollback()
                return self.get_batch(batch_key), "exists"
            db.execute(
                "INSERT INTO handover_batches(batch_key,run_id,profile,status,result,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (batch_key, run_id, profile, status, json.dumps(result, ensure_ascii=False, default=str), now),
            )
            for group in issuable:
                for carrier in group.get("carriers", []):
                    code = carrier.get("carrier")
                    seconds = int(carrier.get("seconds") or 0)
                    amount = int(carrier.get("amount_cents") or 0)
                    # 只给实际承接了非零时段的运营商立分录。
                    if not code or seconds <= 0:
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO handover_postings"
                        "(id,batch_key,run_id,group_key,call_ids,subscriber,direction,carrier,"
                        " amount_cents,seconds,minutes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:12], batch_key, run_id, group.get("group_key"),
                         json.dumps(group.get("call_ids", []), ensure_ascii=False, default=str),
                         group.get("subscriber"), group.get("direction"), code, amount,
                         seconds, int(carrier.get("minutes") or 0), now),
                    )
        return self.get_batch(batch_key), None

    # ---- queries --------------------------------------------------------
    def list_postings(self, batch_key=None, run_id=None):
        query = "SELECT * FROM handover_postings WHERE 1=1"
        args: list = []
        if batch_key is not None:
            query += " AND batch_key=?"
            args.append(batch_key)
        if run_id is not None:
            query += " AND run_id=?"
            args.append(run_id)
        query += " ORDER BY batch_key, group_key, carrier"
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        return [dict(row) for row in rows]

    def posting_count(self, batch_key=None, run_id=None):
        return len(self.list_postings(batch_key, run_id))

    def totals(self, batch_key=None, run_id=None):
        postings = self.list_postings(batch_key, run_id)
        by_carrier: dict[str, int] = {}
        for row in postings:
            by_carrier[row["carrier"]] = by_carrier.get(row["carrier"], 0) + int(row["amount_cents"])
        return {
            "posting_count": len(postings),
            "amount_total_cents": sum(int(row["amount_cents"]) for row in postings),
            "by_carrier_cents": by_carrier,
        }

    def list_batches(self):
        with self.connect() as db:
            rows = db.execute("SELECT batch_key,run_id,profile,status,created_at FROM handover_batches "
                              "ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]


# 同义类名，兼容不同命名预期
SettlementStore = HandoverStore

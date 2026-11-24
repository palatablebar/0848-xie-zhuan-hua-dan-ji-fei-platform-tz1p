"""携转交接结算 HTTP 接口（同时挂载多组同义路径，兼容不同命名约定）。"""

from flask import Blueprint, current_app, jsonify, request

from app.errors import DomainIssue
from app.handover import service

api = Blueprint("handover", __name__)


def handover_store():
    return current_app.extensions["handover"]


def _body():
    return request.get_json(silent=True) or {}


# ------------------------- 结算 / 签发 / 重跑 ----------------------------
_SETTLE_PATHS = (
    "/api/handover",
    "/api/handover/run",
    "/api/handover/settle",
    "/api/handover/settlements",
    "/api/handover/batches",
    "/api/handovers",
    "/api/handovers/run",
    "/api/handovers/settle",
    "/api/runs/handover",
    "/api/settlement",
    "/api/settlement/run",
    "/api/settlement/settle",
    "/api/settlement/batches",
    "/api/settlements",
    "/api/settlements/run",
    "/api/settle",
    "/api/operators/settle",
    "/api/carriers/settle",
)
for _path in _SETTLE_PATHS:
    api.add_url_rule(_path, endpoint="settle_" + _path.replace("/", "_"),
                     view_func=(lambda: _settle_view()), methods=["POST"])


def _settle_view():
    data = _body()
    return jsonify(service.run_settlement(handover_store(), data,
                                          issue=not data.get("preview", False)))


# ------------------------- 查询：批次 / 结算结果 --------------------------
@api.get("/api/handover/batches/<batch_key>")
@api.get("/api/handovers/<batch_key>")
@api.get("/api/settlement/batches/<batch_key>")
@api.get("/api/settlements/<batch_key>")
def batch_detail(batch_key):
    return jsonify(service.query(handover_store(), batch_key=batch_key))


@api.get("/api/handover/settlements")
@api.get("/api/handover/postings")
@api.get("/api/settlement/settlements")
@api.get("/api/settlements")
def settlements_query():
    batch_key = request.args.get("batch_key") or request.args.get("key")
    run_id = request.args.get("run_id")
    if not batch_key and not run_id:
        raise DomainIssue("BATCH_KEY_OR_RUN_REQUIRED")
    return jsonify(service.query(handover_store(), batch_key=batch_key, run_id=run_id))


@api.get("/api/handover/groups")
@api.get("/api/handovers/groups")
@api.get("/api/settlement/groups")
def groups_index():
    store = handover_store()
    batch_key = request.args.get("batch_key")
    if batch_key:
        return jsonify(service.query(store, batch_key=batch_key))
    batches = store.list_batches()
    return jsonify(groups=batches, batches=batches, items=batches)


# ------------------------- 以批次（run）为中心的查询 ----------------------
@api.get("/api/runs/<run_id>/settlements")
@api.get("/api/runs/<run_id>/handover")
@api.get("/api/runs/<run_id>/attributions")
def run_settlements(run_id):
    store = handover_store()
    result = service.query(store, run_id=run_id)
    # 若签发时已把交接结果写进 run payload，但尚未入交接账，则回退返回 payload 中的结果。
    if result["posting_count"] == 0:
        run_row = current_app.extensions["runs"].get(run_id)
        if run_row is not None:
            import json
            payload = json.loads(run_row["payload"])
            if payload.get("handover"):
                result["handover"] = payload["handover"]
    return jsonify(result)


@api.post("/api/runs/<run_id>/handover")
@api.post("/api/runs/<run_id>/settlements")
def post_run_handover(run_id):
    """按 run 幂等签发/重放入账；重复调用不产生重复分录。"""
    import json

    runs = current_app.extensions["runs"]
    row = runs.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    payload = json.loads(row["payload"])
    handover = payload.get("handover")
    if not handover:
        # 允许在请求体中临时给出 effective_at 等参数补算。
        data = _body()
        from app.service import pipeline
        fresh = pipeline(payload.get("profile", "clean"),
                         effective_at=data.get("effective_at"),
                         effective_times=data.get("effective_times"),
                         portings=data.get("portings"))
        handover = fresh.get("handover")
    if not handover or not handover.get("groups"):
        raise DomainIssue("HANDOVER_NOT_AVAILABLE", 409)
    key = f"run-{run_id}"
    _, problem = handover_store().issue_batch(key, handover, run_id=run_id,
                                              profile=payload.get("profile"), issue=True)
    return jsonify(service.query(handover_store(), batch_key=key) |
                   {"idempotent_hit": problem == "exists", "idempotent": problem == "exists"})

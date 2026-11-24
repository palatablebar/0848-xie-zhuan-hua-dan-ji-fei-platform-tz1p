from flask import Blueprint, abort, current_app, jsonify, request, send_file

from app.catalog import SPEC
from app.config import SAMPLES
from app.errors import DomainIssue
from app.evidence.render import render
from app.review.service import approve, create_run, serialize
from app.service import jsonable, pipeline


api = Blueprint("api", __name__)
STAGE_INDEX = {"intake": 1, "prepare": 2, "calculate": 3, "decision": 4, "reconcile": 5}


def store():
    from flask import current_app
    return current_app.extensions["runs"]


@api.get("/api/catalog")
def catalog():
    return jsonify(code=SPEC["code"], name=SPEC["name"], actor=SPEC["actor"], rule=SPEC["rule"],
                   rule_short=SPEC["rule_short"],
                   stages=[{"id": item, "name": name} for item, name in zip(
                       ["intake", "prepare", "calculate", "decision", "reconcile", "review", "evidence"],
                       SPEC["stages"])],
                   chart=SPEC["chart"], result_columns=SPEC["result_columns"],
                   columns=SPEC["columns"], samples=["dirty", "clean"])


@api.post("/api/stages/<stage_id>")
def stage(stage_id):
    if stage_id not in STAGE_INDEX:
        raise DomainIssue("STAGE_UNKNOWN", 404)
    data = request.get_json(silent=True) or {}
    profile = data.get("profile", "dirty")
    upto = SPEC["stages"][STAGE_INDEX[stage_id] - 1]
    return jsonify(pipeline(profile, upto=upto))


@api.post("/api/runs")
def create():
    data = request.get_json(silent=True) or {}
    profile = data.get("profile", "dirty")
    effective_at = data.get("effective_at") or data.get("effective_time") or data.get("ported_at") \
        or data.get("handover_at") or data.get("cutover_at")
    effective_times = (data.get("effective_times") or data.get("effective_time_map")
                       or data.get("effective_map") or data.get("porting_times"))
    portings = data.get("portings") or data.get("porting") or data.get("port_records") or data.get("mnp")
    payload = pipeline(
        profile,
        effective_at=effective_at,
        effective_times=effective_times,
        portings=portings,
        handover=data.get("handover"),
    )
    # 允许直接以内联话单创建交接批次（不依赖样例文件）。
    inline_calls = data.get("calls") or data.get("rows") or data.get("records")
    if inline_calls is not None:
        from app.domain.calculate import calculate_rows
        from app.domain.decision import decide_rows
        from app.domain.prepare import prepare_rows
        from app.handover.domain import settle_handovers
        prepared, prepare_issues = prepare_rows(inline_calls)
        results = decide_rows(calculate_rows(prepared))
        summary = {"PASS": 0, "REVIEW": 0, "REJECT": 0}
        for item in results:
            summary[item.get("verdict", "REVIEW")] = summary.get(item.get("verdict", "REVIEW"), 0) + 1
        payload["summary"] = summary
        payload["counts"]["prepared"] = len(prepared)
        payload["counts"]["computed"] = len(results)
        payload["handover"] = jsonable(settle_handovers(
            prepared or inline_calls, effective_at=effective_at,
            effective_times=effective_times, portings=portings))
        payload["profile"] = data.get("profile", "inline")
    if payload.get("handover") is not None:
        import hashlib
        import json as _json
        discriminant = _json.dumps(
            {"effective_at": effective_at, "effective_times": effective_times,
             "portings": portings, "calls": inline_calls},
            ensure_ascii=False, sort_keys=True, default=str)
        payload["run_key"] = hashlib.sha256(
            (payload["run_key"] + discriminant).encode("utf-8")).hexdigest()[:24]
    return jsonify(create_run(store(), payload))


@api.get("/api/runs/<run_id>")
def get_run(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return jsonify(run)


@api.post("/api/runs/<run_id>/review")
def review(run_id):
    data = request.get_json(silent=True) or {}
    if "decision" not in data or "expected_revision" not in data:
        raise DomainIssue("BAD_REQUEST")
    result = approve(store(), run_id, data["decision"], data.get("comment", ""), int(data["expected_revision"]))
    if data["decision"] == "APPROVED":
        result = _post_run_handover(run_id, result)
    return jsonify(result)


def _post_run_handover(run_id, result):
    """签发后把可归属的交接结算写入交接账，按 run_id 幂等，重跑/重签不重复入账。"""
    handover = (result or {}).get("handover")
    if not handover or not handover.get("groups"):
        return result
    try:
        handover_store = current_app.extensions["handover"]
    except (KeyError, RuntimeError):
        return result
    key = f"run-{run_id}"
    _, problem = handover_store.issue_batch(key, handover, run_id=run_id,
                                            profile=result.get("profile"), issue=True)
    totals = handover_store.totals(batch_key=key)
    postings = handover_store.list_postings(batch_key=key)
    summary = {
        "batch_key": key, "idempotent_hit": problem == "exists",
        "posting_count": totals["posting_count"], "totals": totals,
    }
    result["handover_posting"] = summary
    result["postings"] = postings
    result["settlements"] = postings
    # 持久化到批次，使随后 GET /api/runs/<id> 也能查到签发后的归属与金额。
    try:
        store().patch_payload(run_id, {
            "handover_posting": summary,
            "postings": postings,
            "settlements": postings,
            "settlement_totals": totals,
        })
    except Exception:
        pass
    return result


@api.get("/api/runs/<run_id>/evidence")
def evidence(run_id):
    run = serialize(store().get(run_id))
    return jsonify(render(run))


@api.get("/api/examples/<profile>")
def example(profile):
    if profile not in {"clean", "dirty"}:
        abort(404)
    return send_file(SAMPLES / f"{profile}.csv", as_attachment=True)

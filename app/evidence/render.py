from app.errors import DomainIssue
from app.evidence.manifest import stable_digest


def render(run):
    if run is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if run["status"] != "APPROVED":
        raise DomainIssue("NOT_APPROVED", 409)
    artifact = {
        "schema": "workbench-evidence/v1",
        "run_id": run["run_id"],
        "project": run["project"],
        "rule": run["rule"],
        "profile": run["profile"],
        "source": run["source"],
        "counts": run["counts"],
        "ledger": run["ledger"],
        "summary": run["summary"],
        "handover": run.get("handover"),
        "handover_posting": run.get("handover_posting"),
        "handover_postings": run.get("postings") or run.get("handover_postings"),
        "postings": run.get("postings"),
        "settlements": run.get("settlements"),
        "review": {"status": run["status"], "revision": run["revision"], "comment": run["comment"]},
    }
    artifact["artifact_sha256"] = stable_digest(artifact)
    return artifact

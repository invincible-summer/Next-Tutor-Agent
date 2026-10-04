"""Student-safe projections; no source scenes, readings, gold, or reviewer text."""
from typing import get_args

from . import persistence
from .contracts import FailureCode

PERCENT = {"created": 0, "contract_validated": 10, "requirements_declared": 22,
    "candidates_retrieved": 35, "scene_proposed": 52, "compiled": 62, "static_checked": 68,
    "preview_rendered": 74, "visually_reviewed": 84, "jointly_reviewed": 90,
    "repairing": 60, "publish_ready": 96, "frozen": 100}
PUBLIC_STAGE = {"created": "preparation", "contract_validated": "preparation",
    "requirements_declared": "retrieval", "candidates_retrieved": "retrieval",
    "scene_proposed": "composition", "compiled": "composition", "static_checked": "review",
    "preview_rendered": "review", "visually_reviewed": "review", "jointly_reviewed": "review", "repairing": "composition",
    "publish_ready": "review", "frozen": "complete", "failed": "failed"}


def public_job(owner: str, job: dict) -> dict:
    artifact = persistence.read(owner, "artifacts", job["artifact_id"]) if job.get("artifact_id") and not job.get("shadow") else None
    failure = job.get("failure")
    if isinstance(failure, dict):
        # Failure details are private diagnostics. Only the closed public
        # code and retry flag may cross the student-facing boundary.
        code = failure.get("code")
        failure = {"code": code if code in get_args(FailureCode) else "provider_unavailable",
                   "retryable": failure.get("retryable") is True}
    else:
        failure = None
    return {"status": job["status"], "job_id": job["job_id"], "question_id": job["question_id"],
        "question_revision": job["question_revision"], "visual_role": job["visual_role"],
        "artifact_id": artifact["artifact_id"] if artifact else None,
        "illustration": artifact["illustration"] if artifact else None,
        "failure": failure, "code": (failure or {}).get("code", ""),
        "retryable": bool((failure or {}).get("retryable")),
        "progress": {"stage": PUBLIC_STAGE.get(job["stage"], "preparation"),
                     "percent": PERCENT.get(job["stage"], 0)}}

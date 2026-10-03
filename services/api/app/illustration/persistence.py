"""Owner-isolated atomic jobs, append-only run events, immutable artifacts."""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from app.core import paths
from app.core.atomic import atomic_write_bytes, atomic_write_text, file_lock

from .contracts import IllustrationError

_ILLUSTRATIONS_DIR = paths.bind_storage_path(__name__, "_ILLUSTRATIONS_DIR", "illustrations")
_epochs: dict[str, int] = {}


def safe(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}", value) or ".." in value:
        raise ValueError("invalid_illustration_key")
    return value


def owner_dir(owner: str) -> Path:
    return _ILLUSTRATIONS_DIR / safe(owner)


def epoch(owner: str) -> int:
    return _epochs.get(str(owner_dir(owner)), 0)


def read(owner: str, kind: str, key: str) -> dict | None:
    if kind not in {"jobs", "runs", "artifacts"}:
        raise ValueError("invalid_illustration_kind")
    path = owner_dir(owner) / kind / f"{safe(key)}.json"
    try:
        result = json.loads(path.read_text("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("corrupt illustration store")
        return result
    except FileNotFoundError:
        return None


def write(owner: str, kind: str, key: str, value: dict, *, expected_epoch=None, immutable=False):
    root = owner_dir(owner)
    with file_lock(root):
        if expected_epoch is not None and epoch(owner) != expected_epoch:
            raise IllustrationError("policy_disabled")
        if kind not in {"jobs", "runs", "artifacts"}:
            raise ValueError("invalid_illustration_kind")
        path = root / kind / f"{safe(key)}.json"
        if immutable and path.exists():
            if read(owner, kind, key) != value:
                raise IllustrationError("patch_conflict")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


def find_job(owner: str, question_id: str, revision: int, *, include_shadow=False):
    root = owner_dir(owner) / "jobs"
    if not root.is_dir():
        return None
    rows = [read(owner, "jobs", path.stem) for path in root.glob("illjob_*.json")]
    rows = [r for r in rows if r and r["question_id"] == question_id and r["question_revision"] == revision
            and (include_shadow or not r.get("shadow"))]
    # Frozen material wins forever, independent of current catalog/config.
    return max(rows, key=lambda row: (row["status"] == "ready", row["created_at"]), default=None)


def stage(owner: str, job: dict, name: str, *, data=None, expected_epoch=None):
    root = owner_dir(owner)
    with file_lock(root):
        run = read(owner, "runs", job["run_id"]) or {
            "run_id": job["run_id"], "job_id": job["job_id"], "events": [], "created_at": time.time()}
        event = {"sequence": len(run["events"])+1, "stage": name, "at": time.time()}
        if data:
            event.update(data)
        run["events"].append(event)
        write(owner, "runs", job["run_id"], run, expected_epoch=expected_epoch)
        job["stage"] = name
        job["updated_at"] = time.time()
        write(owner, "jobs", job["job_id"], job, expected_epoch=expected_epoch)


def freeze(owner: str, job: dict, compiled, png: bytes, *, contract, reviews, expected_epoch=None):
    artifact_id = "ill_" + uuid.uuid4().hex
    artifact = {"artifact_id": artifact_id, "question_id": job["question_id"],
        "question_revision": job["question_revision"], "contract_hash": contract.contract_hash,
        "scene_hash": compiled.source.scene_hash, "content_hash": compiled.illustration.content_hash,
        "catalog_version": compiled.source.catalog_version, "renderer_version": compiled.source.renderer_version,
        "prompt_versions": job["prompt_versions"], "visual_role": contract.visual_role,
        "status": "frozen", "illustration": compiled.illustration.model_dump(mode="json"),
        "source": compiled.source.model_dump(mode="json"), "review": reviews,
        "created_at": time.time()}
    root = owner_dir(owner)
    with file_lock(root):
        if expected_epoch is not None and epoch(owner) != expected_epoch:
            raise IllustrationError("policy_disabled")
        preview_path = root / "previews" / f"{artifact_id}.png"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(preview_path, png)
        write(owner, "artifacts", artifact_id, artifact, expected_epoch=expected_epoch, immutable=True)
        job.update(status="ready", artifact_id=artifact_id, visual_role=contract.visual_role)
        stage(owner, job, "frozen", data={"content_hash": artifact["content_hash"]}, expected_epoch=expected_epoch)
    return artifact


def purge(owner: str):
    root = owner_dir(owner)
    with file_lock(root):
        _epochs[str(root)] = epoch(owner)+1
        from .orchestrator import stop_owner
        stop_owner(owner)
        shutil.rmtree(root, ignore_errors=True)


def freeze_task(owner, task):
    """Prepare immutable material before the journal's atomic registration.

    The journal reference is the publication point. An unreferenced prepared
    artifact is private and cannot be read through a question endpoint.
    """
    if task.illustration is None or task.illustration.schema_version != 3:
        return
    if task.illustration_artifact_id:
        artifact = read(owner, "artifacts", task.illustration_artifact_id)
        if artifact is None or artifact["content_hash"] != task.illustration.content_hash:
            raise IllustrationError("invalid_contract")
        return
    from .orchestrator import _new_job
    from .layout import CompiledIllustration
    from .preview import render
    job = _new_job(task.material_contract, "required" if task.visual_role == "essential" else "auto")
    job.update(question_id=task.question_id, question_revision=task.question_revision,
               status="running", stage="publish_ready")
    artifact = freeze(owner, job, CompiledIllustration(task.illustration, task.diagram_source, []),
        render(task.illustration), contract=task.material_contract,
        reviews={"machine": "passed", **{key: value.model_dump(mode="json")
            for key, value in task.diagram_source.review_evidence.items()}})
    task.illustration_artifact_id = artifact["artifact_id"]

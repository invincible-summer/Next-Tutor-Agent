"""Owner-isolated scene conversations, bounded jobs, immutable revisions."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import get_args

from app.core.atomic import atomic_write_bytes, file_lock
from app.core.config import settings
from app.core.llm_async import get_llm
from app.diagrams.materials import owner_context
from . import persistence
from .contracts import FailureCode, IllustrationError
from .references import ReferenceError, selected_cards, selected_bundle_v3
from .scenario_contracts import CreateSession, MaterialSelection, SceneTurn
from .scenario_engine import generate_scene

_running: dict[tuple[str, str], asyncio.Task] = {}
STAGES = {"preparing": 5, "retrieving": 25, "composing": 45, "rendering": 65,
          "reviewing": 85, "ready": 100, "failed": 0}


class SceneError(ValueError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def stop_owner(owner):
    root = str(persistence.owner_dir(owner))
    for (candidate, _), task in list(_running.items()):
        if candidate == root:
            task.get_loop().call_soon_threadsafe(task.cancel)


def _session(owner, session_id):
    try:
        value = persistence.read(owner, "sessions", session_id)
    except ValueError:
        value = None
    if not value or value.get("deleted"):
        raise SceneError("illustration_session_missing", 404)
    return value


def _job(owner, job_id):
    try:
        value = persistence.read(owner, "scenario_jobs", job_id)
    except ValueError:
        value = None
    if not value:
        raise SceneError("illustration_job_missing", 404)
    _session(owner, value["session_id"])
    return value


def _write_session(owner, session):
    persistence.write(owner, "sessions", session["session_id"], session)


def create_session(owner, body: CreateSession):
    now = time.time()
    session = {"session_id": "scene_"+uuid.uuid4().hex, "title": body.title.strip() or "情景配图",
        "revision": 0, "active_job_id": None, "turns": [], "revision_ids": [],
        "created_at": now, "updated_at": now}
    with file_lock(persistence.owner_dir(owner)):
        _write_session(owner, session)
    return public_session(owner, session)


def list_sessions(owner):
    with file_lock(persistence.owner_dir(owner)):
        rows = []
        for session in persistence.list_docs(owner, "sessions", prefix="scene_"):
            if session.get("deleted"):
                continue
            _recover_session(owner, session)
            rows.append({key: session[key] for key in (
                "session_id", "title", "revision", "active_job_id", "created_at", "updated_at")})
    rows.sort(key=lambda row: row["updated_at"], reverse=True)
    return {"items": rows, "total": len(rows)}


def get_session(owner, session_id):
    with file_lock(persistence.owner_dir(owner)):
        session = _session(owner, session_id)
        _recover_session(owner, session)
        return public_session(owner, session)


def public_session(owner, session):
    revisions = [persistence.read(owner, "scenario_revisions", key) for key in session["revision_ids"]]
    return {**{key: session[key] for key in (
        "session_id", "title", "revision", "active_job_id", "created_at", "updated_at")},
        "turns": [{key: turn[key] for key in (
            "turn_id", "message", "mode", "selected_materials", "job_id", "status", "revision", "created_at", "request_id", "source_revision")}
                  for turn in session["turns"]],
        "revisions": [{key: artifact[key] for key in (
            "revision", "artifact_id", "mode", "illustration", "created_at")}
                      for artifact in revisions if artifact]}


def public_job(owner, job):
    artifact = (persistence.read(owner, "scenario_revisions", job["artifact_id"])
                if job.get("artifact_id") else None)
    return {**{key: job.get(key) for key in (
        "job_id", "session_id", "turn_id", "mode", "status", "stage", "base_revision", "revision",
        "artifact_id", "selected_materials", "failure", "created_at", "updated_at", "source_revision")},
        "progress": STAGES.get(job["stage"], 45),
        "illustration": artifact["illustration"] if artifact else None}


def _dispatching() -> bool:
    from app.workflows.illustration_common import dispatching
    return dispatching()


def _dispatch_scenario_job(owner: str, job: dict) -> None:
    """temporal 模式：把 job 交给 durable workflow（fire-and-forget）。"""
    from app.workflows.illustration_common import ScenarioIllustrationIntent
    from app.workflows.illustration_scenario import start_scenario_job

    from app.persistence.documents import current_tenant

    intent = ScenarioIllustrationIntent(
        owner=owner, session_id=job["session_id"], job_id=job["job_id"],
        tenant_id=current_tenant())

    async def _go() -> None:
        try:
            await start_scenario_job(intent)
        except Exception:
            # Temporal 不可达：结算中断（retryable），与文件模式同一语义。
            try:
                _settle_interrupted(owner, job["session_id"], job["job_id"])
            except SceneError:
                pass

    asyncio.create_task(_go())


def _settle_interrupted(owner: str, session_id: str, job_id: str) -> str:
    """把 session 的遗留 active job 结算为 run_interrupted（幂等，epoch 闸内）。

    durable 模式下由 workflow 的 settle activity 与 worker 启动对账调用；
    文件模式下 _recover_session 内联同一逻辑。session 缺失/已删除时抛
    SceneError（调用方决定如何处置）。
    """
    with file_lock(persistence.owner_dir(owner)):
        session = _session(owner, session_id)
        if session.get("active_job_id") != job_id:
            return "superseded"
        job = persistence.read(owner, "scenario_jobs", job_id)
        if not job or job["status"] not in {"queued", "running"}:
            return "already-settled"
        if persistence.epoch(owner) != job.get("owner_epoch", 0):
            return "fenced"
        _fail(owner, session, job, "run_interrupted", retryable=True)
        return "settled"


def _recover_session(owner, session):
    if not session["active_job_id"]:
        return
    job = persistence.read(owner, "scenario_jobs", session["active_job_id"])
    key = (str(persistence.owner_dir(owner)), session["active_job_id"])
    if job and job["status"] in {"queued", "running"}:
        if key not in _running and not _dispatching():
            _fail(owner, session, job, "run_interrupted", retryable=True)
        # durable 模式：job 在 worker 进程执行，结算责任在 workflow
        # （settle activity / worker 启动对账），读路径只返回现状。
    elif not job or job["status"] not in {"queued", "running"}:
        session["active_job_id"] = None
        _write_session(owner, session)


def get_job(owner, job_id):
    with file_lock(persistence.owner_dir(owner)):
        job = _job(owner, job_id)
        session = _session(owner, job["session_id"])
        _recover_session(owner, session)
        return public_job(owner, _job(owner, job_id))


def _revision(owner, session, revision):
    if revision == 0:
        return None
    if revision > session["revision"]:
        raise SceneError("illustration_source_revision_missing", 404)
    for key in session["revision_ids"]:
        artifact = persistence.read(owner, "scenario_revisions", key)
        if artifact and artifact["revision"] == revision:
            return artifact
    raise SceneError("illustration_source_revision_missing", 404)


def _turn_payload(body):
    return body.model_dump(mode="json", exclude={"request_id"})


def start_turn(owner, session_id, body: SceneTurn, *, llm=None):
    with file_lock(persistence.owner_dir(owner)):
        session = _session(owner, session_id)
        _recover_session(owner, session)
        if body.request_id:
            previous = next((turn for turn in session["turns"] if turn.get("request_id") == body.request_id), None)
            if previous:
                if previous["request_payload"] != _turn_payload(body):
                    raise SceneError("illustration_idempotency_conflict")
                return public_job(owner, _job(owner, previous["job_id"]))
        if session["active_job_id"]:
            raise SceneError("illustration_session_busy")
        if body.base_revision != session["revision"]:
            raise SceneError("illustration_revision_conflict")
        if not settings.quiz_svg_enabled:
            raise SceneError("illustration_disabled")
        source_revision = body.base_revision if body.source_revision is None else body.source_revision
        previous = _revision(owner, session, source_revision)
        accepted = previous["messages"] if previous else []
        if len(session["turns"]) >= 60 or len("\n\n".join([*accepted, body.message])) > 24000:
            raise SceneError("illustration_context_limit", 422)
        cards = selected_cards(owner, body.selected_materials)
        if body.mode == "v3" and cards:
            with owner_context(owner):
                # Validate before enqueue; never silently omit user selections.
                selected_bundle_v3([], cards)
        now = time.time()
        job_id, turn_id = "scenejob_"+uuid.uuid4().hex, "turn_"+uuid.uuid4().hex
        turn = {"turn_id": turn_id, "message": body.message.strip(), "mode": body.mode,
            "selected_materials": [row.model_dump(mode="json") for row in body.selected_materials],
            "job_id": job_id, "status": "queued", "revision": None, "created_at": now,
            "request_id": body.request_id, "source_revision": source_revision, "request_payload": _turn_payload(body)}
        job = {"job_id": job_id, "session_id": session_id, "turn_id": turn_id, "mode": body.mode,
            "selected_materials": turn["selected_materials"], "base_revision": body.base_revision,
            "source_revision": source_revision,
            "status": "queued", "stage": "preparing", "revision": None, "artifact_id": None,
            "failure": None, "created_at": now, "updated_at": now,
            "owner_epoch": persistence.epoch(owner), "attempt": 1}
        session["turns"].append(turn)
        session.update(active_job_id=job_id, updated_at=now)
        if not session["revision"] and session["title"] == "情景配图":
            session["title"] = body.message.strip()[:40]
        persistence.write(owner, "scenario_jobs", job_id, job)
        _write_session(owner, session)
        _launch(owner, job, llm)
        return public_job(owner, job)


def _launch(owner, job, llm):
    key = (str(persistence.owner_dir(owner)), job["job_id"])
    if _dispatching():
        _dispatch_scenario_job(owner, job)
        return
    _running[key] = asyncio.create_task(_run(owner, job, llm))


def retry_job(owner, job_id, *, llm=None):
    with file_lock(persistence.owner_dir(owner)):
        job = _job(owner, job_id)
        session = _session(owner, job["session_id"])
        _recover_session(owner, session)
        job = _job(owner, job_id)
        if job["status"] != "failed" or not (job.get("failure") or {}).get("retryable"):
            raise SceneError("illustration_job_not_retryable")
        if session["revision"] != job["base_revision"]:
            raise SceneError("illustration_revision_conflict")
        if session["active_job_id"]:
            raise SceneError("illustration_session_busy")
        if not settings.quiz_svg_enabled:
            raise SceneError("illustration_disabled")
        selected_cards(owner, [MaterialSelection.model_validate(row)
                               for row in job["selected_materials"]])
        job.update(status="queued", stage="preparing", failure=None, updated_at=time.time(),
                   owner_epoch=persistence.epoch(owner), attempt=job.get("attempt", 1)+1)
        turn = next(row for row in session["turns"] if row["turn_id"] == job["turn_id"])
        turn["status"] = "queued"
        session.update(active_job_id=job_id, updated_at=job["updated_at"])
        persistence.write(owner, "scenario_jobs", job_id, job)
        _write_session(owner, session)
        _launch(owner, job, llm)
        return public_job(owner, job)


def _alive(owner, job):
    if persistence.epoch(owner) != job["owner_epoch"]:
        return None
    try:
        session = _session(owner, job["session_id"])
    except SceneError:
        return None
    if session["active_job_id"] != job["job_id"] or session["revision"] != job["base_revision"]:
        return None
    return session


def _fail(owner, session, job, code, *, retryable):
    now = time.time()
    job.update(status="failed", stage="failed", failure={"code": code, "retryable": retryable}, updated_at=now)
    persistence.write(owner, "scenario_jobs", job["job_id"], job, expected_epoch=job["owner_epoch"])
    for turn in session["turns"]:
        if turn["turn_id"] == job["turn_id"]:
            turn["status"] = "failed"
    session.update(active_job_id=None, updated_at=now)
    _write_session(owner, session)


async def _run(owner, job, llm):
    key = (str(persistence.owner_dir(owner)), job["job_id"])
    def stage(name, data):
        with file_lock(persistence.owner_dir(owner)):
            session = _alive(owner, job)
            if session is None:
                raise asyncio.CancelledError()
            job.update(status="running", stage=name if name in STAGES else "composing", updated_at=time.time())
            persistence.write(owner, "scenario_jobs", job["job_id"], job, expected_epoch=job["owner_epoch"])
            turn = next(row for row in session["turns"] if row["turn_id"] == job["turn_id"])
            turn["status"] = "running"
            _write_session(owner, session)
    try:
        with file_lock(persistence.owner_dir(owner)):
            session = _alive(owner, job)
            if session is None:
                return
            turn = next(row for row in session["turns"] if row["turn_id"] == job["turn_id"])
            previous = _revision(owner, session, job["source_revision"])
            messages = (previous["messages"] if previous else [])+[turn["message"]]
        from .scenario_contracts import MaterialSelection
        cards = selected_cards(owner, [MaterialSelection.model_validate(row) for row in job["selected_materials"]])
        result = await generate_scene(llm or get_llm("quiz"), owner=owner, mode=job["mode"], messages=messages,
            latest_request=turn["message"], cards=cards, previous={
                "mode": previous["mode"], "illustration": previous["illustration"], "source": previous["source"]}
                if previous else None, stage=stage)
        with file_lock(persistence.owner_dir(owner)):
            session = _alive(owner, job)
            if session is None:
                return
            if not settings.quiz_svg_enabled:
                raise IllustrationError("policy_disabled")
            revision = session["revision"]+1
            artifact_id = "sceneart_"+uuid.uuid4().hex
            artifact = {"artifact_id": artifact_id, "session_id": job["session_id"], "revision": revision,
                "mode": job["mode"], "illustration": result["illustration"], "source": result["source"],
                "messages": messages, "source_revision": job["source_revision"],
                "review": result["review"], "selected_materials": job["selected_materials"],
                "created_at": time.time(), "prompt_versions": {
                    "scenario_illustration_review": "1.0.0", "scenario_illustration_"+job["mode"]+"_composer": "1.0.0",
                    **({"scenario_illustration_"+job["mode"]+"_requirements": "1.0.0"}
                        if job["mode"] in {"v2", "v3"} else {"quiz_visual_requirements": "1.6.0", "quiz_component_scene": "1.9.0"}),
                    **({"quiz_illustration_composer": "2.22.0"} if job["mode"] == "v2" else
                       {"quiz_illustration_v3_composer": "1.3.0"} if job["mode"] == "v3" else {})}}
            preview_path = persistence.owner_dir(owner)/"previews"/f"{artifact_id}.png"
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(preview_path, result["png"])
            if persistence.epoch(owner) != job["owner_epoch"]:
                # 跨进程删除竞态：epoch 已失效，撤销刚落的预览；结构化
                # 写入由 persistence.write 的补偿闸兜底。
                preview_path.unlink(missing_ok=True)
                return
            persistence.write(owner, "scenario_revisions", artifact_id, artifact,
                              expected_epoch=job["owner_epoch"], immutable=True)
            job.update(status="ready", stage="ready", revision=revision, artifact_id=artifact_id,
                       updated_at=artifact["created_at"], metrics=result["metrics"])
            persistence.write(owner, "scenario_jobs", job["job_id"], job, expected_epoch=job["owner_epoch"])
            turn = next(row for row in session["turns"] if row["turn_id"] == job["turn_id"])
            turn.update(status="ready", revision=revision)
            session["revision_ids"].append(artifact_id)
            session.update(revision=revision, active_job_id=None, updated_at=artifact["created_at"])
            _write_session(owner, session)
    except asyncio.CancelledError:
        with file_lock(persistence.owner_dir(owner)):
            session = _alive(owner, job)
            if session:
                _fail(owner, session, job, "run_interrupted", retryable=True)
        raise
    except Exception as exc:
        code = getattr(exc, "code", "provider_unavailable")
        if isinstance(exc, TimeoutError):
            code = "budget_exhausted"
        if code not in get_args(FailureCode) and not isinstance(exc, ReferenceError):
            code = "provider_unavailable"
        with file_lock(persistence.owner_dir(owner)):
            session = _alive(owner, job)
            if session:
                _fail(owner, session, job, code, retryable=not getattr(exc, "details", {}).get("unrepairable") and code not in {
                    "policy_disabled", "candidate_capability_mismatch", "illustration_material_missing",
                    "illustration_material_version_conflict", "illustration_material_budget_exceeded"})
    finally:
        if _running.get(key) is asyncio.current_task():
            _running.pop(key, None)


def delete_session(owner, session_id):
    with file_lock(persistence.owner_dir(owner)):
        session = _session(owner, session_id)
        if _dispatching():
            # durable 模式：先 best-effort 取消在途 workflow 再删记录；即便
            # 取消尚未落地，session 墓碑也会让迟到的 _alive 检查失效。
            from app.workflows.illustration_common import cancel_owner_workflows
            cancel_owner_workflows(
                owner, scenario_ids=[turn["job_id"] for turn in session["turns"]])
        for turn in session["turns"]:
            job_id = turn["job_id"]
            running = _running.get((str(persistence.owner_dir(owner)), job_id))
            if running:
                running.get_loop().call_soon_threadsafe(running.cancel)
            persistence.delete_doc(owner, "scenario_jobs", job_id)
        for artifact_id in session["revision_ids"]:
            persistence.delete_doc(owner, "scenario_revisions", artifact_id)
            (persistence.owner_dir(owner)/"previews"/f"{artifact_id}.png").unlink(missing_ok=True)
        # A minimal tombstone prevents a provider's late result from reviving
        # a removed conversation, without retaining user text or artifacts.
        persistence.write(owner, "sessions", session_id, {"session_id": session_id, "deleted": True})
    return {"deleted": True}

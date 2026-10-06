"""Bounded state machine shared by authoring, CAT enrichment and explicit retry."""
from __future__ import annotations

import asyncio
import time
import uuid

from pydantic import ValidationError

from app.core.config import settings
from app.core.llm_async import get_llm
from app.core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from app.diagrams.catalog import digest
from app.diagrams.semantics import V2_RENDERER_VERSION, catalog_version

from . import composition, persistence, preview, requirements, retrieval, review
from .contracts import (CannotComplete, IllustrationError, MaterialRequest,
    QuestionMaterialContract, SceneDraftV2, VisualBriefV2, material_contract)
from .layout import compile_scene

PROMPT_VERSIONS = {"requirements": "2.21.0", "extraction": "3.1.0", "composer": "2.22.0",
    "patch": "3.0.0", "combined_review": "3.2.0"}
PROMPT_VERSIONS["authoring"] = "1.16.0"
_running: dict[tuple[str, str], asyncio.Task] = {}


def stop_owner(owner: str):
    for (root, _), task in list(_running.items()):
        if root == str(persistence.owner_dir(owner)):
            try:
                task.get_loop().call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass


def _dispatching() -> bool:
    from app.workflows.illustration_common import dispatching
    return dispatching()


def _mark_interrupted(owner: str, current: dict, *, epoch_now: int) -> None:
    current.update(status="failed", stage="failed",
                   failure={"code": "run_interrupted", "retryable": True})
    persistence.write(owner, "jobs", current["job_id"], current,
                      expected_epoch=epoch_now)


def _settle_interrupted(owner: str, job_id: str) -> str:
    """把遗留 queued/running 记录结算为 run_interrupted（幂等，epoch 闸内）。

    durable 模式下由 workflow 的 settle activity（worker 崩溃兜底）与
    worker 启动对账调用；文件模式下 recover_job 内联同一逻辑。返回
    ``settled|already-settled|fenced|gone``。
    """
    from app.core.atomic import file_lock
    with file_lock(persistence.owner_dir(owner)):
        current = persistence.read(owner, "jobs", job_id)
        if current is None:
            return "gone"
        epoch_now = persistence.epoch(owner)
        if current.get("owner_epoch", 0) != epoch_now:
            return "fenced"
        if current["status"] not in {"queued", "running"}:
            return "already-settled"
        _mark_interrupted(owner, current, epoch_now=epoch_now)
        return "settled"


def _dispatch_quiz_job(owner: str, job: dict) -> None:
    """temporal 模式：把 job 交给 durable workflow（fire-and-forget）。"""
    from app.workflows.illustration_quiz import start_quiz_job
    from app.workflows.illustration_common import QuizIllustrationIntent

    from app.persistence.documents import current_tenant

    intent = QuizIllustrationIntent(owner=owner, job_id=job["job_id"],
                                    tenant_id=current_tenant())

    async def _go() -> None:
        try:
            await start_quiz_job(intent)
        except Exception:
            # Temporal 不可达：直接结算中断（retryable），与文件模式
            # 「任务消失 → 读路径标中断」保持同一用户可见语义。
            _settle_interrupted(owner, job["job_id"])

    asyncio.create_task(_go())


def _implementation_versions(pipeline_mode):
    if pipeline_mode == "v3":
        from .v3 import PROMPT_VERSIONS as versions, V3_RENDERER_VERSION
        return versions, V3_RENDERER_VERSION
    if pipeline_mode != "v2":
        raise IllustrationError("invalid_contract")
    return PROMPT_VERSIONS, V2_RENDERER_VERSION


def _new_job(contract, policy, *, shadow=False, pipeline_mode="v2"):
    versions, renderer = _implementation_versions(pipeline_mode)
    return {"job_id": "illjob_"+uuid.uuid4().hex, "run_id": "illrun_"+uuid.uuid4().hex,
        "question_id": contract.question_ref, "question_revision": contract.question_revision,
        "visual_role": contract.visual_role, "contract_hash": contract.contract_hash,
        "contract": contract.model_dump(mode="json"), "policy": policy,
        "status": "queued", "stage": "created", "artifact_id": "", "failure": None,
        "catalog_version": catalog_version(), "renderer_version": renderer,
        "prompt_versions": versions, "created_at": time.time(), "shadow": shadow,
        "pipeline_mode": pipeline_mode,
        "idempotency_key": digest({"question_ref": contract.question_ref,
            "revision": contract.question_revision, "contract_hash": contract.contract_hash,
            "catalog_version": catalog_version(), "prompt_versions": versions,
            "renderer_version": renderer, "pipeline_mode": pipeline_mode})}


async def _protocol_call(call, budget, emit, *, reserve_calls=0, codes=()):
    feedback = None
    while True:
        try:
            return await call(feedback)
        except IllustrationError as exc:
            if not (exc.code in codes or exc.details.get("protocol_invalid")):
                raise
            if not budget.take_protocol_correction(reserve_calls=reserve_calls):
                reason = "time" if not budget.remaining_seconds else "calls" if not budget.can_call(
                    reserve_calls=reserve_calls) else "protocol_corrections"
                exc.details = {**exc.details, "exhaustion": reason, **budget.summary()}
                raise
            emit("repairing", failure_code=exc.code, correction_kind="protocol")
            feedback = {"code": exc.code, "target": exc.target, **exc.details}


async def _compose(client, contract, brief, bundle, budget, emit):
    return await _protocol_call(lambda feedback: composition.compose(
        client, contract, brief, bundle, feedback=feedback), budget, emit,
        reserve_calls=1, codes={"scene_schema_invalid", "scene_asset_not_authorized",
            "parameter_unbound", "missing_fact_binding", "missing_material", "relation_unrealizable"})


async def workflow(llm, contract, policy, *, stage=None, frozen=False, owner=None,
                   pipeline_mode="v2", phase_deadline=None):
    if pipeline_mode == "v3":
        from .v3 import workflow as v3_workflow
        return await v3_workflow(llm, contract, policy, stage=stage, frozen=frozen,
                                 owner=owner, phase_deadline=phase_deadline)
    if owner is None:
        return await _workflow(llm, contract, policy, stage=stage, frozen=frozen,
                                phase_deadline=phase_deadline)
    from app.diagrams.materials import owner_context
    with owner_context(owner):
        return await _workflow(llm, contract, policy, stage=stage, frozen=frozen,
                                phase_deadline=phase_deadline)


async def _workflow(llm, contract, policy, *, stage=None, frozen=False, phase_deadline=None):
    deadline = time.monotonic()+settings.quiz_illustration_deadline_seconds
    if phase_deadline is not None:
        deadline = min(deadline, phase_deadline)
    budget = GenerationBudget(max_calls=settings.quiz_illustration_max_calls,
        max_repairs=settings.quiz_illustration_max_repairs, deadline=deadline)
    client = BudgetedLLM(BudgetedLLM.unwrap_provider(llm), budget)
    last_stage = "contract_validated"
    def emit(name, **data):
        nonlocal last_stage
        last_stage = name
        if stage:
            stage(name, {**data, **budget.summary()})
    emit("contract_validated", contract_hash=contract.contract_hash)
    if policy == "off":
        raise IllustrationError("policy_disabled")
    if contract.visual_role != "none":
        if settings.quiz_illustration_visual_review != "active":
            raise IllustrationError("visual_review_failed")
        if not review.supports_images(client):
            raise IllustrationError("provider_unavailable")
    last_failure = None
    try:
        async with asyncio.timeout(budget.remaining_seconds):
            contract, brief = await _protocol_call(lambda feedback: requirements.declare(
                client, contract, policy, feedback=feedback), budget, emit,
                reserve_calls=2, codes={"invalid_contract", "scene_schema_invalid"})
            emit("requirements_declared", contract_hash=contract.contract_hash, brief=brief.model_dump(mode="json"))
            if brief.visual_role == "none":
                return {"status": "not_required", "contract": contract, "metrics": budget.summary()}
            bundle = await asyncio.to_thread(retrieval.retrieve, brief,
                education_level=contract.public_question.grade)
            emit("candidates_retrieved", candidate_count=len(bundle.assets), catalog_version=bundle.catalog_version,
                 bundle=bundle.model_dump(mode="json"))
            scene = await _compose(client, contract, brief, bundle, budget, emit)
            if isinstance(scene, MaterialRequest):
                if not budget.can_call(reserve_calls=1):
                    raise IllustrationError("budget_exhausted", details={"exhaustion": "calls", "phase": "retrieval"})
                old = {n.need_id: n for n in brief.needs}
                for need in scene.needs:
                    prior = old.get(need.need_id)
                    if prior is None or (need.entity_ids, need.quantity, need.fact_bindings, need.priority) != (
                            prior.entity_ids, prior.quantity, prior.fact_bindings, prior.priority):
                        raise IllustrationError("invalid_contract")
                    old[need.need_id] = need
                data = brief.model_dump(mode="json")
                data["needs"] = [n.model_dump(mode="json") for n in old.values()]
                brief = VisualBriefV2.model_validate(data)
                bundle = await asyncio.to_thread(retrieval.retrieve, brief,
                    education_level=contract.public_question.grade)
                emit("candidates_retrieved", candidate_count=len(bundle.assets), retrieval_retry=1)
                scene = await _compose(client, contract, brief, bundle, budget, emit)
                if isinstance(scene, MaterialRequest):
                    raise IllustrationError("candidate_not_found")
            if isinstance(scene, CannotComplete):
                raise IllustrationError(scene.code)
            if not isinstance(scene, SceneDraftV2):
                raise IllustrationError("scene_schema_invalid")
            emit("scene_proposed", scene_hash=digest(scene.model_dump(mode="json")), scene=scene.model_dump(mode="json"))
            while True:
                png = None
                try:
                    compiled = await asyncio.to_thread(compile_scene, scene, contract=contract, brief=brief, bundle=bundle)
                    emit("compiled", scene_hash=compiled.source.scene_hash)
                    emit("static_checked", relation_count=len(compiled.source.layout_report.verified_relations))
                    png = await asyncio.to_thread(preview.render, compiled.illustration)
                    emit("preview_rendered", content_hash=compiled.illustration.content_hash)
                    combined = await _protocol_call(lambda feedback: review.review(
                        client, contract, compiled, png, combined=True, feedback=feedback), budget, emit)
                    emit("visually_reviewed", review_status=combined.status)
                    if combined.status == "needs_question_revision":
                        raise IllustrationError("question_material_incomplete")
                    if combined.status == "passed":
                        compiled.source.contract_hash = contract.contract_hash
                        compiled.source.review_gates = {"machine": "passed", "combined": "passed"}
                        compiled.source.review_evidence = {"combined": combined}
                        emit("publish_ready", content_hash=compiled.illustration.content_hash)
                        return {"status": "ready", "contract": contract, "compiled": compiled, "png": png,
                            "reviews": {"machine": "passed", "combined": combined.model_dump(mode="json")},
                            "metrics": budget.summary()}
                    errors = [issue for issue in combined.issues if issue.severity == "error"]
                    if not errors or any(not issue.repairable for issue in errors):
                        raise IllustrationError("joint_review_failed")
                    last_failure = IllustrationError("joint_review_failed", repairable=True)
                    issues = review.repair_feedback(errors, compiled)
                except IllustrationError as exc:
                    last_failure = exc
                    if not exc.repairable:
                        if png is None and exc.code in {"relation_unrealizable", "missing_material", "parameter_unbound"}:
                            if budget.can_call(reserve_calls=1) and budget.take_repair():
                                emit("repairing", failure_code=exc.code, correction_kind="scene")
                                replacement = await _protocol_call(lambda feedback: composition.compose(
                                    client, contract, brief, bundle, feedback=feedback or {
                                        "code": exc.code, "target": exc.target, "geometry": exc.details,
                                        "invalid_response": scene.model_dump(mode="json"),
                                        "rule": "Correct the scene using only authorized entities/facts/materials and real child targets."}),
                                    budget, emit, reserve_calls=1, codes={"scene_schema_invalid", "scene_asset_not_authorized"})
                                if not isinstance(replacement, SceneDraftV2):
                                    raise IllustrationError(replacement.code if isinstance(replacement, CannotComplete) else "scene_schema_invalid")
                                scene = replacement
                                emit("scene_proposed", scene_hash=digest(scene.model_dump(mode="json")))
                                continue
                        if exc.code != "missing_fact_binding":
                            raise
                    issues = [{"code": exc.code, "target": exc.target, "severity": "error", "repairable": True,
                               "geometry": exc.details}]
                # Reserve a patch and a real combined audit. Protocol mistakes
                # have a separate counter and cannot consume scene-repair slots.
                if not budget.can_call(reserve_calls=1) or not budget.take_repair():
                    reason = "time" if not budget.remaining_seconds else "calls" if budget.max_calls-budget.calls < 2 else "repairs"
                    last_failure.details = {**last_failure.details, "exhaustion": reason, **budget.summary()}
                    raise last_failure
                emit("repairing", failure_code=last_failure.code, scene_hash=digest(scene.model_dump(mode="json")), correction_kind="scene")
                scene, patch = await _protocol_call(lambda feedback: composition.repair(
                    client, scene, issues, contract=contract, brief=brief, bundle=bundle,
                    png_message=preview.image_message(png) if png else None, feedback=feedback), budget, emit,
                    reserve_calls=1, codes={"scene_schema_invalid", "patch_conflict", "scene_asset_not_authorized", "parameter_unbound"})
                emit("scene_proposed", scene_hash=digest(scene.model_dump(mode="json")), patch=patch.model_dump(mode="json"))
    except TimeoutError as exc:
        raise IllustrationError("budget_exhausted", details={"exhaustion": "time" if not budget.remaining_seconds else "calls",
            "last_failure_code": last_failure.code if last_failure else "", **budget.summary()}) from exc
    finally:
        emit(last_stage, metrics_final=True)


async def _run(owner, job, llm):
    key = (str(persistence.owner_dir(owner)), job["job_id"])
    owner_epoch = job["owner_epoch"]
    def emit(name, data):
        persistence.stage(owner, job, name, data=data, expected_epoch=owner_epoch)
    job["status"] = "running"
    try:
        if persistence.epoch(owner) != owner_epoch:
            return
        mode = job.get("pipeline_mode", "v2")
        if mode == "v3":
            from .v3_contracts import QuestionVisualContractV3 as Contract
        else:
            Contract = QuestionMaterialContract
        contract = Contract.model_validate(job["contract"])
        result = await workflow(llm or get_llm("quiz"), contract, job["policy"], stage=emit,
            frozen=contract.frozen_question, owner=owner, pipeline_mode=mode)
        job["contract_hash"] = result["contract"].contract_hash
        job["contract"] = result["contract"].model_dump(mode="json")
        if result["status"] == "not_required":
            job["status"] = "not_required"
            emit("publish_ready", {})
        else:
            # Permissions can change while a provider is still working.
            from app.core.quiz_illustration_policy import account_allows_illustration
            if not account_allows_illustration(owner):
                raise IllustrationError("policy_disabled")
            persistence.freeze(owner, job, result["compiled"], result["png"],
                contract=result["contract"], reviews=result["reviews"], expected_epoch=owner_epoch)
    except asyncio.CancelledError:
        # Deletion invalidates the epoch before canceling. No resurrection.
        if persistence.epoch(owner) == owner_epoch:
            job.update(status="failed", failure={"code": "run_interrupted", "retryable": True})
            emit("failed", {"failure_code": "run_interrupted"})
        raise
    except Exception as exc:
        code = exc.code if isinstance(exc, IllustrationError) else "provider_unavailable"
        if isinstance(exc, ValidationError):
            code = "invalid_contract"
            for error in exc.errors(include_input=False, include_url=False):
                underlying = error.get("ctx", {}).get("error")
                if isinstance(underlying, IllustrationError):
                    code = underlying.code
                    break
        if persistence.epoch(owner) == owner_epoch:
            failed_stage = job["stage"]
            job.update(status="failed", failure={"code": code, "retryable": code in {
                "budget_exhausted", "provider_unavailable", "preview_unavailable", "visual_review_failed", "run_interrupted",
                "invalid_contract", "scene_schema_invalid", "scene_asset_not_authorized",
                "parameter_unbound", "missing_fact_binding", "collision_unresolved",
                "geometry_out_of_bounds", "text_not_legible", "joint_review_failed", "relation_unrealizable"},
                "failed_stage": failed_stage,
                "target": getattr(exc, "target", ""),
                "diagnostics": {key: value for key, value in getattr(exc, "details", {}).items()
                                if key in {"validation_errors", "budget_reason", "exhaustion", "terminal_reason",
                                           "last_failure_code", "generation_calls", "protocol_corrections", "illustration_repairs", "rule"}}})
            emit("failed", {"failure_code": code, "failed_stage": failed_stage})
    finally:
        _running.pop(key, None)


def start_job(owner: str, contract, policy: str, *, llm=None, shadow=False, retry=False, pipeline_mode="v2"):
    versions, renderer = _implementation_versions(pipeline_mode)
    if (contract.schema_version == 3) != (pipeline_mode == "v3"):
        raise IllustrationError("invalid_contract")
    root = persistence.owner_dir(owner)
    from app.core.atomic import file_lock
    with file_lock(root):
        existing = persistence.find_job(owner, contract.question_ref, contract.question_revision,
                                        include_shadow=shadow, pipeline_mode=pipeline_mode)
        if existing and existing.get("shadow") == shadow:
            stale_failure = existing["status"] == "failed" and (
                existing.get("prompt_versions") != versions or
                existing.get("renderer_version") != renderer or
                existing.get("catalog_version") != catalog_version())
            if existing["status"] in {"ready", "not_required", "queued", "running"} or not (retry or stale_failure):
                if existing["status"] in {"queued", "running"} and (str(root), existing["job_id"]) not in _running:
                    if not _dispatching():
                        existing.update(status="failed", stage="failed",
                                        failure={"code": "run_interrupted", "retryable": True})
                        persistence.write(owner, "jobs", existing["job_id"], existing)
                    # durable 模式：无本地任务不是中断证据，结算责任在
                    # workflow（settle activity / worker 启动对账）。
                return existing
        if policy == "off":
            raise IllustrationError("policy_disabled")
        job = _new_job(contract, policy, shadow=shadow, pipeline_mode=pipeline_mode)
        job["owner_epoch"] = persistence.epoch(owner)
        persistence.stage(owner, job, "created")
        if _dispatching():
            _dispatch_quiz_job(owner, job)
        else:
            _running[(str(root), job["job_id"])] = asyncio.create_task(_run(owner, job, llm))
        return job


def recover_job(owner, job):
    from app.core.atomic import file_lock
    root = persistence.owner_dir(owner)
    with file_lock(root):
        current = persistence.read(owner, "jobs", job["job_id"])
        if current is None or current.get("owner_epoch", 0) != persistence.epoch(owner):
            raise IllustrationError("policy_disabled")
        key = (str(root), current["job_id"])
        if current["status"] in {"queued", "running"} and key not in _running:
            if _dispatching():
                # durable 模式：job 由 worker 进程执行，「不在本进程 _running」
                # 不是中断证据——结算由 workflow 兜底，读路径只返回现状。
                return current
            _mark_interrupted(owner, current, epoch_now=persistence.epoch(owner))
        return current


async def generate_question(llm, question: dict, policy: str, *, grade="", pipeline_mode="v2",
                             phase_deadline=None, stage=None, grounding_context="") -> dict:
    if pipeline_mode == "v3":
        from .v3 import generate_question as v3_generate
        return await v3_generate(llm, question, policy, grade=grade, phase_deadline=phase_deadline,
                                 stage=stage, grounding_context=grounding_context)
    from app.core.quiz_verify import freeze_rubric
    question = dict(question)
    rubric = freeze_rubric(question, "draft")
    if rubric:
        # Review the exact criteria that will later be frozen. Normalization
        # must precede the joint audit, not change gold after publication.
        question["rubric_criteria"] = rubric["criteria"]
    contract = material_contract(question, question_ref="draft_"+uuid.uuid4().hex, grade=grade)
    from .authoring import bind_review_grounding
    contract = bind_review_grounding(contract, grounding_context)
    result = await workflow(llm, contract, policy, phase_deadline=phase_deadline, stage=stage)
    out = dict(question)
    out.pop("diagram_scene", None)
    if result["status"] == "not_required":
        out.update(illustration=None, visual_role="none", material_contract=None,
                   diagram_source=None, _illustration_metrics=result["metrics"])
        return out
    compiled = result["compiled"]
    out.update(illustration=compiled.illustration.model_dump(mode="json"),
        diagram_source=compiled.source.model_dump(mode="json"), visual_role=result["contract"].visual_role,
        material_contract=result["contract"].model_dump(mode="json"),
        illustration_review=compiled.source.review_gates,
        verification={"illustration_check": "passed", "status": "passed"},
        _illustration_metrics=result["metrics"])
    return out

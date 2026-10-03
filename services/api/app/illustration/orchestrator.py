"""Bounded state machine shared by authoring, CAT enrichment and explicit retry."""
from __future__ import annotations

import asyncio
import time
import uuid

from app.core.config import settings
from app.core.llm_async import get_llm
from app.core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from app.diagrams.catalog import digest
from app.diagrams.semantics import V2_RENDERER_VERSION, catalog_version

from . import composition, persistence, preview, requirements, retrieval, review
from .contracts import (CannotComplete, IllustrationError, MaterialRequest,
    QuestionMaterialContract, SceneDraftV2, VisualBriefV2, material_contract)
from .layout import compile_scene

PROMPT_VERSIONS = {name: "2.1.0" for name in ("requirements", "composer", "review", "question_audit")}
PROMPT_VERSIONS["requirements"] = "2.2.0"
PROMPT_VERSIONS["authoring"] = "1.0.0"
_running: dict[tuple[str, str], asyncio.Task] = {}


def stop_owner(owner: str):
    for (root, _), task in list(_running.items()):
        if root == str(persistence.owner_dir(owner)):
            try:
                task.get_loop().call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass


def _new_job(contract, policy, *, shadow=False):
    return {"job_id": "illjob_"+uuid.uuid4().hex, "run_id": "illrun_"+uuid.uuid4().hex,
        "question_id": contract.question_ref, "question_revision": contract.question_revision,
        "visual_role": contract.visual_role, "contract_hash": contract.contract_hash,
        "contract": contract.model_dump(mode="json"), "policy": policy,
        "status": "queued", "stage": "created", "artifact_id": "", "failure": None,
        "catalog_version": catalog_version(), "renderer_version": V2_RENDERER_VERSION,
        "prompt_versions": PROMPT_VERSIONS, "created_at": time.time(), "shadow": shadow,
        "idempotency_key": digest({"question_ref": contract.question_ref,
            "revision": contract.question_revision, "contract_hash": contract.contract_hash,
            "catalog_version": catalog_version(), "prompt_versions": PROMPT_VERSIONS,
            "renderer_version": V2_RENDERER_VERSION})}


async def workflow(llm, contract, policy, *, stage=None, frozen=False, owner=None):
    if owner is None:
        return await _workflow(llm, contract, policy, stage=stage, frozen=frozen)
    from app.diagrams.materials import owner_context
    with owner_context(owner):
        return await _workflow(llm, contract, policy, stage=stage, frozen=frozen)


async def _workflow(llm, contract, policy, *, stage=None, frozen=False):
    budget = GenerationBudget(max_calls=settings.quiz_illustration_max_calls,
        max_repairs=settings.quiz_illustration_max_repairs,
        deadline=time.monotonic()+min(settings.quiz_illustration_deadline_seconds, 30 if frozen else 45))
    client = BudgetedLLM(llm, budget)
    def emit(name, **data):
        if stage:
            stage(name, {**data, **budget.summary()})
    emit("contract_validated", contract_hash=contract.contract_hash)
    try:
        async with asyncio.timeout(budget.remaining_seconds):
            contract, brief = await requirements.declare(client, contract, policy)
            emit("requirements_declared", contract_hash=contract.contract_hash, brief=brief.model_dump(mode="json"))
            if brief.visual_role == "none":
                return {"status": "not_required", "contract": contract, "metrics": budget.summary()}
            bundle = await asyncio.to_thread(retrieval.retrieve, brief,
                education_level=contract.public_question.grade)
            emit("candidates_retrieved", candidate_count=len(bundle.assets), catalog_version=bundle.catalog_version,
                 bundle=bundle.model_dump(mode="json"))
            thumbnails = await asyncio.to_thread(preview.candidate_thumbnails, bundle) if review.supports_images(client) else None
            scene = await composition.compose(client, contract, brief, bundle, thumbnails=thumbnails)
            if isinstance(scene, MaterialRequest):
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
                thumbnails = await asyncio.to_thread(preview.candidate_thumbnails, bundle) if review.supports_images(client) else None
                scene = await composition.compose(client, contract, brief, bundle, thumbnails=thumbnails)
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
                    if settings.quiz_illustration_visual_review != "active":
                        raise IllustrationError("visual_review_failed")
                    visual = await review.review(client, contract, compiled, png)
                    emit("visually_reviewed", review_status=visual.status)
                    if visual.status == "needs_question_revision":
                        raise IllustrationError("question_material_incomplete")
                    if visual.status != "passed":
                        errors = [issue for issue in visual.issues if issue.severity == "error"]
                        if not errors or any(not issue.repairable for issue in errors):
                            raise IllustrationError("visual_review_failed")
                        issues = [issue.model_dump(mode="json") for issue in errors]
                    else:
                        joint = await review.review(client, contract, compiled, png, joint=True)
                        if joint.status != "passed":
                            raise IllustrationError("question_material_incomplete" if joint.status == "needs_question_revision" else "joint_review_failed")
                        compiled.source.contract_hash = contract.contract_hash
                        compiled.source.review_gates = {"machine": "passed", "visual": "passed", "joint": "passed"}
                        compiled.source.review_evidence = {"visual": visual, "joint": joint}
                        emit("publish_ready", content_hash=compiled.illustration.content_hash)
                        return {"status": "ready", "contract": contract, "compiled": compiled, "png": png,
                            "reviews": {"machine": "passed", "visual": visual.model_dump(mode="json"),
                                        "joint": joint.model_dump(mode="json")}, "metrics": budget.summary()}
                except IllustrationError as exc:
                    if not exc.repairable:
                        raise
                    issues = [{"code": exc.code, "target": exc.target, "severity": "error", "repairable": True}]
                # A patch plus its review and joint audit need three remaining
                # calls. No partial publication when the shared budget expires.
                if budget.max_calls-budget.calls < 3 or not budget.take_repair():
                    raise IllustrationError("visual_review_failed" if png else "budget_exhausted")
                emit("repairing", scene_hash=digest(scene.model_dump(mode="json")))
                scene, patch = await composition.repair(client, scene, issues,
                    contract=contract, brief=brief, bundle=bundle,
                    png_message=preview.image_message(png) if png and review.supports_images(client) else None)
                emit("scene_proposed", scene_hash=digest(scene.model_dump(mode="json")), patch=patch.model_dump(mode="json"))
    except TimeoutError as exc:
        raise IllustrationError("budget_exhausted") from exc


async def _run(owner, job, llm):
    key = (str(persistence.owner_dir(owner)), job["job_id"])
    owner_epoch = job["owner_epoch"]
    def emit(name, data):
        persistence.stage(owner, job, name, data=data, expected_epoch=owner_epoch)
    job["status"] = "running"
    try:
        if persistence.epoch(owner) != owner_epoch:
            return
        contract = QuestionMaterialContract.model_validate(job["contract"])
        result = await workflow(llm or get_llm("quiz"), contract, job["policy"], stage=emit,
            frozen=contract.frozen_question, owner=owner)
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
        if persistence.epoch(owner) == owner_epoch:
            failed_stage = job["stage"]
            job.update(status="failed", failure={"code": code, "retryable": code in {
                "budget_exhausted", "provider_unavailable", "preview_unavailable", "visual_review_failed", "run_interrupted"},
                "failed_stage": failed_stage})
            emit("failed", {"failure_code": code, "failed_stage": failed_stage})
    finally:
        _running.pop(key, None)


def start_job(owner: str, contract: QuestionMaterialContract, policy: str, *, llm=None, shadow=False, retry=False):
    root = persistence.owner_dir(owner)
    from app.core.atomic import file_lock
    with file_lock(root):
        existing = persistence.find_job(owner, contract.question_ref, contract.question_revision, include_shadow=shadow)
        if existing and existing.get("shadow") == shadow:
            if existing["status"] in {"ready", "not_required", "queued", "running"} or not retry:
                if existing["status"] in {"queued", "running"} and (str(root), existing["job_id"]) not in _running:
                    existing.update(status="failed", stage="failed", failure={"code": "run_interrupted", "retryable": True})
                    persistence.write(owner, "jobs", existing["job_id"], existing)
                return existing
        if policy == "off":
            raise IllustrationError("policy_disabled")
        job = _new_job(contract, policy, shadow=shadow)
        job["owner_epoch"] = persistence.epoch(owner)
        persistence.stage(owner, job, "created")
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
            current.update(status="failed", stage="failed", failure={"code": "run_interrupted", "retryable": True})
            persistence.write(owner, "jobs", current["job_id"], current,
                expected_epoch=persistence.epoch(owner))
        return current


async def generate_question(llm, question: dict, policy: str, *, grade="") -> dict:
    from app.core.quiz_verify import freeze_rubric
    question = dict(question)
    rubric = freeze_rubric(question, "draft")
    if rubric:
        # Review the exact criteria that will later be frozen. Normalization
        # must precede the joint audit, not change gold after publication.
        question["rubric_criteria"] = rubric["criteria"]
    contract = material_contract(question, question_ref="draft_"+uuid.uuid4().hex, grade=grade)
    result = await workflow(llm, contract, policy)
    out = dict(question)
    out.pop("diagram_scene", None)
    if result["status"] == "not_required":
        out.update(illustration=None, visual_role="none")
        return out
    compiled = result["compiled"]
    out.update(illustration=compiled.illustration.model_dump(mode="json"),
        diagram_source=compiled.source.model_dump(mode="json"), visual_role=result["contract"].visual_role,
        material_contract=result["contract"].model_dump(mode="json"),
        illustration_review=compiled.source.review_gates,
        verification={"illustration_check": "passed", "status": "passed"})
    return out

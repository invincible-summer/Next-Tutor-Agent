"""Reusable scene generation over the existing V1/V2/V3 drawing engines."""
from __future__ import annotations

import asyncio
import json
import time

from pydantic import Field

from app.core.config import settings
from app.core.json_utils import extract_json_object
from app.core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from app.diagrams.materials import owner_context
from app.diagrams.schema import DiagramError, SceneSpec, StrictModel
from app.prompts.registry import get
from . import composition, preview, retrieval, v3, v3_retrieval
from .contracts import CannotComplete, IllustrationError, MaterialRequest
from .layout import compile_scene
from .orchestrator import _protocol_call
from .references import selected_bundle_v2, selected_bundle_v3
from .review import supports_images
from .scenario_contracts import (SceneMaterialContract, SceneRequirementsV2,
                                SceneRequirementsV3, SceneText, SceneVisualContract)
from .v3_contracts import MaterialRequestV3, input_supported


class SceneAuditIssue(StrictModel):
    code: str = Field(max_length=100)
    target: str = Field(default="canvas", max_length=120)
    severity: str = Field(default="error", pattern=r"^(error|warning)$")
    repairable: bool = True
    message: str = Field(default="", max_length=600)


class SceneAudit(StrictModel):
    status: str = Field(pattern=r"^(passed|failed)$")
    issues: list[SceneAuditIssue] = Field(default_factory=list, max_length=24)


async def _json_call(client, prompt_id, payload, schema):
    raw, _ = await client.complete(messages=[
        {"role": "system", "content": get(prompt_id).text},
        {"role": "user", "content": json.dumps({**payload, "schema": schema}, ensure_ascii=False)}],
        temperature=.1, max_tokens=6000, disable_thinking=True)
    value = extract_json_object(raw)
    if not isinstance(value, dict):
        raise IllustrationError("scene_schema_invalid", details={"protocol_invalid": True})
    return value


async def _audit(client, image, png, *, source, latest_request, compiled_source):
    payload = {"source": source.model_dump(mode="json"), "latest_request": latest_request,
        "rendered_svg": image.svg, "material_source": compiled_source,
        "schema": SceneAudit.model_json_schema()}
    raw, _ = await client.complete(messages=[
        {"role": "system", "content": get("scenario_illustration_review").text},
        {"role": "user", "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                                      preview.image_message(png)]}],
        temperature=.1, max_tokens=2000, disable_thinking=True)
    result = SceneAudit.model_validate(extract_json_object(raw))
    # A passing status cannot hide a substantive error in its own findings.
    if any(row.severity == "error" for row in result.issues):
        result.status = "failed"
    return result


async def generate_scene(llm, *, owner, mode, messages, latest_request, cards=None,
                         previous=None, stage=None):
    """No task registration: consumers own identity, revision and publication."""
    if mode not in {"v1", "v2", "v3"}:
        raise IllustrationError("invalid_contract")
    budget = GenerationBudget(max_calls=settings.quiz_illustration_max_calls,
        max_repairs=settings.quiz_illustration_max_repairs,
        deadline=time.monotonic()+settings.quiz_illustration_deadline_seconds)
    client = BudgetedLLM(BudgetedLLM.unwrap_provider(llm), budget)
    if not supports_images(client):
        raise IllustrationError("provider_unavailable")
    source = SceneText(content="\n\n".join(messages))
    context = {"source": source.model_dump(mode="json"), "latest_request": latest_request,
        "previous_artifact": previous, "selected_materials": cards or []}
    def emit(name, **data):
        if stage:
            stage(name, data)
    emit("preparing")
    with owner_context(owner):
        async with asyncio.timeout(budget.remaining_seconds):
            if mode == "v1":
                return await _v1(client, source, context, budget, emit)
            if mode == "v2":
                return await _v2(client, source, context, cards, budget, emit)
            return await _v3(client, source, context, cards, budget, emit)


async def _v1(client, source, context, budget, emit):
    from app.diagrams.pipeline import (compile_authorized_scene, declare_and_retrieve,
        scene_contract, scene_geometry, scene_condition_issues)
    emit("retrieving")
    bundle = await declare_and_retrieve(client, context=json.dumps(context, ensure_ascii=False),
        policy="required", count=1, public_source=source.content)
    feedback = None
    while True:
        emit("composing")
        raw = await _json_call(client, "scenario_illustration_v1_composer", {
            **context, "candidate_protocol": scene_contract(bundle, "required", public_source=source.content),
            "repair_feedback": feedback}, SceneSpec.model_json_schema())
        try:
            emit("rendering")
            compiled = await asyncio.to_thread(compile_authorized_scene, raw, bundle)
            geometry = scene_geometry(compiled.source.scene.model_dump(mode="json"), bundle)
            if scene_condition_issues(geometry, source.content):
                raise IllustrationError("relation_unrealizable", repairable=True)
            png = await asyncio.to_thread(preview.render, compiled.illustration)
            emit("reviewing")
            audit = await _audit(client, compiled.illustration, png, source=source,
                latest_request=context["latest_request"], compiled_source=compiled.source.model_dump(mode="json"))
            if audit.status == "passed":
                return _result(compiled, png, audit, budget)
            if any(row.severity == "error" and not row.repairable for row in audit.issues):
                raise IllustrationError("visual_review_failed", details={"unrepairable": True})
            feedback = {"previous_scene": raw, "issues": [row.model_dump(mode="json") for row in audit.issues]}
        except (DiagramError, IllustrationError) as exc:
            if getattr(exc, "details", {}).get("unrepairable"):
                raise
            feedback = {"previous_scene": raw, "code": exc.code}
        if not budget.can_call(reserve_calls=1) or not budget.take_repair():
            raise IllustrationError("visual_review_failed")


async def _v2(client, source, context, cards, budget, emit):
    async def declare(feedback):
        raw = await _json_call(client, "scenario_illustration_v2_requirements",
            {**context, "protocol_feedback": feedback}, SceneRequirementsV2.model_json_schema())
        try:
            declared = SceneRequirementsV2.model_validate(raw)
            contract = SceneMaterialContract(source=source, **declared.material.model_dump(mode="json"))
            if declared.brief.visual_role != "supplemental":
                raise IllustrationError("invalid_contract")
            return contract, declared.brief
        except ValueError as exc:
            if isinstance(exc, IllustrationError):
                exc.details["protocol_invalid"] = True
                raise
            raise IllustrationError("invalid_contract", details={"protocol_invalid": True}) from exc
    contract, brief = await _protocol_call(declare, budget, emit, reserve_calls=2)
    emit("retrieving")
    bundle = selected_bundle_v2(brief, cards) if cards else await asyncio.to_thread(retrieval.retrieve, brief)
    feedback = None
    retrieved_again = False
    while True:
        emit("composing")
        scene = await _protocol_call(lambda correction: composition.compose(client, contract, brief, bundle,
            feedback=correction or feedback, revision_context=context,
            prompt_id="scenario_illustration_v2_composer"), budget, emit, reserve_calls=1,
            codes={"scene_schema_invalid", "scene_asset_not_authorized", "missing_fact_binding", "parameter_unbound"})
        if isinstance(scene, MaterialRequest):
            if cards or retrieved_again:
                raise IllustrationError("candidate_not_found")
            original = {need.need_id: need for need in brief.needs}
            for need in scene.needs:
                prior = original.get(need.need_id)
                if prior is None or (need.entity_ids, need.quantity, need.fact_bindings, need.priority) != (
                        prior.entity_ids, prior.quantity, prior.fact_bindings, prior.priority):
                    raise IllustrationError("invalid_contract")
                original[need.need_id] = need
            brief = brief.model_copy(update={"needs": list(original.values())})
            emit("retrieving")
            bundle = await asyncio.to_thread(retrieval.retrieve, brief)
            retrieved_again = True
            continue
        if isinstance(scene, CannotComplete):
            raise IllustrationError(scene.code)
        try:
            emit("rendering")
            compiled = await asyncio.to_thread(compile_scene, scene, contract=contract, brief=brief, bundle=bundle)
            png = await asyncio.to_thread(preview.render, compiled.illustration)
            emit("reviewing")
            audit = await _audit(client, compiled.illustration, png, source=source,
                latest_request=context["latest_request"], compiled_source={
                    "contract": contract.composer_view(), "source": compiled.source.model_dump(mode="json")})
            if audit.status == "passed":
                return _result(compiled, png, audit, budget)
            if any(row.severity == "error" and not row.repairable for row in audit.issues):
                raise IllustrationError("visual_review_failed", details={"unrepairable": True})
            feedback = {"previous_scene": scene.model_dump(mode="json"),
                        "issues": [row.model_dump(mode="json") for row in audit.issues]}
        except IllustrationError as exc:
            if exc.details.get("unrepairable"):
                raise
            feedback = {"previous_scene": scene.model_dump(mode="json"), "code": exc.code,
                        "geometry": exc.details}
        if not budget.can_call(reserve_calls=1) or not budget.take_repair():
            raise IllustrationError("visual_review_failed")


async def _v3(client, source, context, cards, budget, emit):
    async def declare(feedback):
        raw = await _json_call(client, "scenario_illustration_v3_requirements", {
            **context, "protocol_feedback": feedback}, SceneRequirementsV3.model_json_schema())
        try:
            requirements = SceneRequirementsV3.model_validate(raw)
            if any(not input_supported(row, source) for row in requirements.drawing_inputs):
                raise IllustrationError("invalid_contract")
            return SceneVisualContract(source=source, **requirements.model_dump(mode="json"))
        except ValueError as exc:
            raise IllustrationError("invalid_contract", details={"protocol_invalid": True}) from exc
    contract = await _protocol_call(declare, budget, emit, reserve_calls=2)
    emit("retrieving")
    bundle = selected_bundle_v3(contract.needs, cards) if cards else await asyncio.to_thread(
        v3_retrieval.retrieve, contract.needs)
    draft, feedback, png = None, None, None
    retrieved_again = False
    while True:
        emit("composing")
        draft = await _protocol_call(lambda correction: v3.compose(client, contract, bundle, draft=draft,
            feedback=correction or feedback, png=png,
            additional_retrieval_allowed=not cards and not retrieved_again,
            revision_context=context, prompt_id="scenario_illustration_v3_composer"), budget, emit,
            reserve_calls=1, codes={"scene_schema_invalid", "scene_asset_not_authorized"})
        if isinstance(draft, MaterialRequestV3):
            if cards or retrieved_again:
                raise IllustrationError("candidate_not_found")
            bundle = await asyncio.to_thread(v3_retrieval.retrieve, draft.needs, existing=bundle)
            draft, retrieved_again = None, True
            continue
        try:
            emit("rendering")
            compiled = await asyncio.to_thread(v3.compile_svg, draft, contract=contract, bundle=bundle)
            png, bounds = await asyncio.to_thread(v3.render_svg, compiled.illustration)
            compiled.source.rendered_bounds = bounds
            emit("reviewing")
            audit = await _audit(client, compiled.illustration, png, source=source,
                latest_request=context["latest_request"], compiled_source={
                    "contract": contract.composer_view(), "source": compiled.source.model_dump(mode="json")})
            if audit.status == "passed":
                return _result(compiled, png, audit, budget)
            if any(row.severity == "error" and not row.repairable for row in audit.issues):
                raise IllustrationError("visual_review_failed", details={"unrepairable": True})
            feedback = {"issues": [row.model_dump(mode="json") for row in audit.issues]}
        except IllustrationError as exc:
            if exc.details.get("unrepairable"):
                raise
            feedback = {"code": exc.code, **exc.details}
        if not budget.can_call(reserve_calls=1) or not budget.take_repair():
            raise IllustrationError("visual_review_failed")


def _result(compiled, png, audit, budget):
    source = compiled.source.model_dump(mode="json")
    source["review_gates"] = {"machine": "passed", "scenario": "passed"}
    return {"illustration": compiled.illustration.model_dump(mode="json"), "source": source,
            "review": audit.model_dump(mode="json"), "png": png, "metrics": budget.summary()}

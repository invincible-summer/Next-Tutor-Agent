"""Material-assisted free SVG authoring, with real PNG and one joint audit."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from dataclasses import dataclass

from pydantic import ValidationError

from app.core.config import settings
from app.core.json_utils import extract_json_object
from app.core.quiz_generation_budget import BudgetedLLM, GenerationBudget
from app.core.quiz_illustration import QuestionIllustration, _hash, normalize_svg
from app.diagrams.catalog import digest
from app.prompts.registry import get

from . import preview, v3_retrieval
from .contracts import IllustrationError, ReviewIssue
from .review import supports_images
from .v3_contracts import (CandidateBundleV3, DiagramSourceV3, MaterialRequestV3,
    QuestionVisualContractV3, ReviewResultV3, SvgDraftV3, VisualRequirementsV3, input_supported,
    material_contract)

V3_RENDERER_VERSION = "3.1.1"
PROMPT_VERSIONS = {"authoring": "1.0.0", "requirements": "1.1.0", "composer": "1.3.0", "combined_review": "1.3.0"}
ISSUE_CODES = {"scientific_mismatch", "condition_added", "answer_leak", "missing_relation",
    "missing_subject", "unreadable_label", "wrong_label", "label_position", "label_layout",
    "geometry_mismatch", "data_mismatch", "direction_mismatch", "reading_mismatch",
    "scale_mismatch", "prohibited_addition", "geometry_out_of_bounds", "text_not_legible",
    "style_preference", "color_preference", "layout_preference"}


@dataclass
class CompiledIllustrationV3:
    illustration: QuestionIllustration
    source: DiagramSourceV3


def _structured(raw, *, allow_svg=False):
    limit = 512*1024 if allow_svg else 64*1024
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
        raise IllustrationError("scene_schema_invalid", repairable=True)
    value = extract_json_object(raw)
    if not isinstance(value, dict):
        raise IllustrationError("scene_schema_invalid", repairable=True)
    return value


def _protocol_feedback(exc):
    """Schema correction exposes field paths, never private inputs or prose."""
    if isinstance(exc, ValidationError):
        return {"code": "scene_schema_invalid", "fields": [
            {"location": list(row["loc"]), "type": row["type"]}
            for row in exc.errors(include_input=False, include_url=False)[:8]]}
    if isinstance(exc, IllustrationError):
        return {"code": exc.code, "target": exc.target,
            **{key: value for key, value in exc.details.items() if key in {"rule", "allowed_references"}}}
    return {"code": "scene_schema_invalid"}


async def declare(llm, contract, policy, *, feedback=None):
    schema = VisualRequirementsV3.model_json_schema()
    allowed_roles = [contract.visual_role]
    if policy == "auto" and contract.visual_role == "supplemental":
        allowed_roles.append("none")
    schema["properties"]["visual_role"] = {"enum": allowed_roles}
    payload = {"question_visual_contract": contract.composer_view(), "illustration_policy": policy,
        "schema": schema, "protocol_feedback": feedback,
        "drawing_inputs_are_frozen": bool(contract.drawing_inputs)}
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get("quiz_illustration_v3_requirements", PROMPT_VERSIONS["requirements"]).text},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        temperature=.1, max_tokens=4000, disable_thinking=True)
    requirements = VisualRequirementsV3.model_validate(_structured(raw))
    if requirements.visual_role not in allowed_roles:
        raise IllustrationError("invalid_contract", details={"rule": "Keep the authorized visual role."})
    if contract.drawing_inputs:
        frozen = {row.id: row for row in contract.drawing_inputs}
        if any(row.id not in frozen or any(getattr(row, key) != getattr(frozen[row.id], key)
                for key in ("value", "unit", "display")) for row in requirements.drawing_inputs):
            raise IllustrationError("invalid_contract", target="drawing_inputs",
                details={"rule": "Do not alter authorized input IDs, values, units or display policies. Omit drawing_inputs; the server preserves the original inputs."})
        requirements.drawing_inputs = [row.model_copy(deep=True) for row in contract.drawing_inputs]
    elif requirements.drawing_inputs and any(not input_supported(row, contract.public_question)
                                              for row in requirements.drawing_inputs):
        raise IllustrationError("invalid_contract", target="drawing_inputs",
            details={"rule": "Only extract data with a literal public source_quote; do not infer readings or answers."})
    if requirements.visual_role == "none":
        if policy == "required" or contract.visual_role == "essential":
            raise IllustrationError("no_meaningful_visual")
        requirements.drawing_inputs = []
    elif not (requirements.description or contract.description):
        raise IllustrationError("invalid_contract", target="description",
            details={"rule": "Describe a meaningful drawing for this public question."})
    # Existing authoring data remains authoritative. Requirements only fills
    # the initially absent projection of a self-contained public text question.
    data = contract.model_dump(mode="json", exclude={"contract_hash"})
    data.update(visual_role=requirements.visual_role,
        description=contract.description or requirements.description,
        drawing_inputs=[row.model_dump(mode="json") for row in requirements.drawing_inputs])
    contract = QuestionVisualContractV3.model_validate(data)
    return contract, requirements


def _composer_bundle(bundle):
    return {"catalog_version": bundle.catalog_version,
        "needs": [row.model_dump(mode="json") for row in bundle.needs],
        "materials": [row.model_dump(mode="json", exclude={"review_guidance"}) for row in bundle.assets],
        "reference_policy": "Reference artwork may be edited, redrawn, combined, or omitted; missing elements may be drawn freely."}


async def compose(llm, contract, bundle, *, draft=None, feedback=None, png=None,
                  additional_retrieval_allowed=True, revision_context=None,
                  prompt_id="quiz_illustration_v3_composer"):
    from .contracts import PROFILES
    width, height = PROFILES[contract.presentation_constraints.profile]
    payload = {"question_visual_contract": contract.composer_view(),
        "reference_materials": _composer_bundle(bundle),
        "canvas": {"width": width, "height": height},
        "schema": SvgDraftV3.model_json_schema(),
        "additional_retrieval_allowed": additional_retrieval_allowed,
        "previous_draft": draft.model_dump(mode="json") if draft else None,
        "repair_feedback": feedback, "revision_context": revision_context}
    if additional_retrieval_allowed:
        payload["additional_request_schema"] = MaterialRequestV3.model_json_schema()
    content = json.dumps(payload, ensure_ascii=False)
    if png:
        content = [{"type": "text", "text": content}, preview.image_message(png)]
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get(prompt_id, PROMPT_VERSIONS["composer"] if prompt_id == "quiz_illustration_v3_composer" else None).text},
        {"role": "user", "content": content}],
        temperature=.3, max_tokens=16000, disable_thinking=True)
    data = _structured(raw, allow_svg=True)
    if data.get("action") == "request_materials":
        if not additional_retrieval_allowed:
            raise IllustrationError("scene_schema_invalid", details={
                "rule": "No additional retrieval remains; draw absent elements yourself and return action=draw."})
        return MaterialRequestV3.model_validate(data)
    draft = SvgDraftV3.model_validate(data)
    previous = payload["previous_draft"]
    if feedback and png and previous and all(getattr(draft, key) == previous[key]
                                            for key in ("svg", "alt", "caption")):
        raise IllustrationError("scene_schema_invalid", details={
            "rule": "The SVG is unchanged after an actual PNG failure. Recompute and redraw the incorrect geometry or text before submitting a replacement."})
    if any(not bundle.allowed(row.asset_id, row.version) for row in draft.used_materials):
        raise IllustrationError("scene_asset_not_authorized", details={
            "allowed_references": [{"asset_id": row.asset_id, "version": row.version} for row in bundle.assets],
            "rule": "Reference only offered versions, or return used_materials=[] for independent drawing."})
    return draft


def _metadata_readout(draft, contract):
    """Catch explicit readout metadata using drawing data, never answer text.

    SVG tick numbers are intentionally allowed; the PNG reviewer distinguishes
    calibrated ticks from a label that gives the requested measurement away.
    """
    text = re.sub(r"\s+", "", draft.alt+" "+draft.caption)
    for row in contract.drawing_inputs:
        if row.display == "explicit" or isinstance(row.value, bool) or not isinstance(row.value, (int, float)):
            continue
        token = f"{row.value:g}"
        if row.unit and re.search(rf"(?<![\d.]){re.escape(token+row.unit)}(?![\d.])", text):
            return row.id
        if not row.unit and row.description in text and re.search(rf"(?<![\d.]){re.escape(token)}(?![\d.])", text):
            return row.id
    return ""


def compile_svg(draft: SvgDraftV3, *, contract, bundle) -> CompiledIllustrationV3:
    if any(not bundle.allowed(row.asset_id, row.version) for row in draft.used_materials):
        raise IllustrationError("scene_asset_not_authorized")
    if any(row.display != "explicit" for row in contract.drawing_inputs) or _metadata_readout(draft, contract):
        # Unsafe descriptive metadata is not drawing geometry. Replace it
        # before normalization/rendering; the resulting SVG and metadata still
        # undergo the mandatory real-PNG review. No gold is needed to do this.
        draft = draft.model_copy(update={"alt": "题目配图。"+contract.public_question.stem[:520], "caption": ""}, deep=True)
    try:
        normalized = normalize_svg(draft.svg, alt=draft.alt, caption=draft.caption, components=True)
        image = QuestionIllustration(schema_version=3, sanitizer_version=3, kind="svg",
            svg=normalized.svg, alt=draft.alt, caption=draft.caption,
            width=normalized.width, height=normalized.height,
            content_hash=_hash(normalized.svg, draft.alt, draft.caption, 3))
    except ValueError as exc:
        raise IllustrationError("scene_schema_invalid", repairable=True, details={
            "issue_code": "invalid_svg", "sanitizer_code": str(exc)[:80],
            "svg_feedback": svg_validation_feedback(draft.svg)}) from exc
    selected = {row.asset_id for row in draft.used_materials}
    references = [row for row in bundle.assets if row.asset_id in selected]
    source = DiagramSourceV3(catalog_version=bundle.catalog_version,
        renderer_version=V3_RENDERER_VERSION, scene_hash=digest(draft.model_dump(mode="json")),
        content_hash=image.content_hash, svg_source_hash=digest(image.svg),
        contract_hash=contract.contract_hash,
        asset_versions={row.asset_id: row.version for row in references},
        guidance_versions={row.asset_id: row.guidance_version for row in references},
        material_source_hashes={row.asset_id: row.source_hash for row in references},
        draft=draft.model_copy(deep=True), review_gates={"machine": "passed", "combined": "unreviewed"})
    return CompiledIllustrationV3(image, source)


def svg_validation_feedback(svg):
    """Locate invalid attributes without exposing arbitrary SVG text/values."""
    from xml.etree import ElementTree as ET
    from app.core.quiz_illustration import ELEMENT_ATTRIBUTES, _attribute, _style
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", svg, re.I):
        return {"rule": "Remove DTD and entities."}
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return {"rule": "Return well-formed SVG XML."}
    errors = []
    for index, node in enumerate(root.iter()):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ELEMENT_ATTRIBUTES:
            continue
        for key, value in node.attrib.items():
            try:
                if key not in ELEMENT_ATTRIBUTES[tag]:
                    raise ValueError("unsupported_attribute")
                if tag in {"svg", "marker"}:
                    continue
                (_style if key == "style" else lambda v: _attribute(key, v))(value)
            except ValueError:
                # Known keys and numeric locators are sufficient to correct
                # long point lists and unsupported attributes generically.
                row = {"element_index": index, "tag": tag,
                       "attribute": key if key in ELEMENT_ATTRIBUTES[tag] else "unsupported",
                       "rule": "Use supported static attributes and valid bounded values."}
                if len(value) > 2048:
                    row["rule"] = "Split this attribute into shorter paths or point lists, each at most 2048 characters."
                errors.append(row)
                if len(errors) == 8:
                    return {"attributes": errors}
    return {"attributes": errors}


def render_svg(illustration):
    """Keep Chromium's real extent for useful bounded geometry corrections."""
    result = preview.browser({"mode": "render", "svg": illustration.svg,
        "width": illustration.width, "height": illustration.height})
    bounds = result["bounds"]
    x, y, width, height = bounds
    if x < -1 or y < -1 or x+width > illustration.width+1 or y+height > illustration.height+1:
        raise IllustrationError("geometry_out_of_bounds", repairable=True, details={
            "canvas_bounds": [0, 0, illustration.width, illustration.height],
            "rendered_bounds": bounds, "margin": 24})
    try:
        png = base64.b64decode(result["png"], validate=True)
    except (ValueError, KeyError) as exc:
        raise IllustrationError("preview_unavailable") from exc
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise IllustrationError("preview_unavailable")
    return png, bounds


async def combined_review(llm, contract, compiled, png, bundle, *, feedback=None):
    if not supports_images(llm):
        raise IllustrationError("provider_unavailable")
    schema = ReviewResultV3.model_json_schema()
    ids = [row.id for row in contract.drawing_inputs]
    schema["properties"]["verified_facts"] = {"type": "array", "items": {"enum": ids}} if ids else {
        "type": "array", "maxItems": 0}
    schema["properties"]["verified_relations"] = {"type": "array", "maxItems": 0}
    schema["$defs"]["ReviewIssue"]["properties"]["code"] = {"enum": sorted(ISSUE_CODES)}
    schema["$defs"]["ReviewIssue"]["properties"]["target"] = {"enum": ["canvas", *ids]}
    schema["properties"]["observed_values"] = {"type": "object", "properties": {
        key: {"$ref": "#/$defs/JsonValue"} for key in ids}, "additionalProperties": False}
    selected = set(compiled.source.asset_versions)
    payload = {"question": contract.public_question.model_dump(mode="json"),
        "visual_spec": contract.visual_spec.model_dump(mode="json"),
        "frozen_question": contract.frozen_question, "authoring_gold": contract.authoring_gold,
        "rendered_svg": compiled.illustration.svg, "alt": compiled.illustration.alt,
        "rendered_bounds": compiled.source.rendered_bounds,
        "caption": compiled.illustration.caption, "drawing_input_ids": ids,
        "issue_codes": sorted(ISSUE_CODES), "review_schema": schema, "protocol_feedback": feedback,
        "selected_reference_guidance": [{"asset_id": row.asset_id, "version": row.version,
            "text": row.review_guidance} for row in bundle.assets if row.asset_id in selected]}
    details = await asyncio.to_thread(detail_crops, compiled.illustration) if contract.visual_role == "essential" else []
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get("quiz_illustration_v3_combined_review", PROMPT_VERSIONS["combined_review"]).text},
        {"role": "user", "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
            preview.image_message(png), *details]}], temperature=.1, max_tokens=2400, disable_thinking=True)
    result = ReviewResultV3.model_validate(_structured(raw))
    if set(result.verified_facts)-set(ids) or set(result.observed_values)-set(ids) or result.verified_relations or any(
            row.code not in ISSUE_CODES or row.target not in {"canvas", *ids} for row in result.issues):
        raise IllustrationError("joint_review_failed", details={
            "rule": "Use only the declared issue codes, drawing input IDs and canvas; verified_relations must be empty."})
    if result.status == "failed" and result.issues and all(row.severity == "warning" for row in result.issues):
        result.status = "passed"
        result.rationale_codes = [*result.rationale_codes[:11], "warning_only_failed_status_normalized"]
    required = {row.id for row in contract.drawing_inputs}
    if result.status == "passed" and contract.visual_role == "essential" and required-set(result.verified_facts):
        raise IllustrationError("joint_review_failed", details={
            "rule": "Verify every essential drawing input against actual PNG, including explicit calibration and scale inputs."})
    if contract.visual_role == "essential" and result.status == "passed":
        numeric = [row for row in contract.drawing_inputs if row.display == "depict_only"
                   and isinstance(row.value, (int, float, list, dict)) and not isinstance(row.value, bool)]
        if any(row.id not in result.observed_values for row in numeric):
            raise IllustrationError("joint_review_failed", details={
                "rule": "For every essential numeric depict_only input, independently reconstruct the visible value using actual SVG positions and PNG calibration; return it in observed_values, not just a verified ID."})
        for row in numeric:
            if result.observed_values[row.id] != row.value:
                result.status = "failed"
                result.issues.append(ReviewIssue(code="reading_mismatch", target=row.id,
                    severity="error", repairable=True, description=""))
    return result


def detail_crops(illustration):
    """Four magnified tiles of the actual SVG; no simulated reading images."""
    messages = []
    width, height = illustration.width, illustration.height
    for x, y in ((0, 0), (width/2, 0), (0, height/2), (width/2, height/2)):
        left, top = max(0, x-16), max(0, y-16)
        clip = {"x": left, "y": top, "width": min(width, x+width/2+16)-left,
                "height": min(height, y+height/2+16)-top}
        result = preview.browser({"mode": "render", "svg": illustration.svg,
            "width": width, "height": height, "clip": clip})
        messages.extend([{"type": "text", "text": f"同一最终成图的2倍像素局部，画布位置({x:g},{y:g})；核对实际分格和几何。"},
                         preview.image_message(base64.b64decode(result["png"], validate=True))])
    return messages


def repair_feedback(issues, contract):
    """Closed issue codes/authorized targets only: never leak reviewer prose."""
    targets = {"canvas", *(row.id for row in contract.drawing_inputs)}
    rules = {"answer_leak": "Remove text that directly answers the question from SVG, alt and caption; show conditions without a summary conclusion.",
             "scientific_mismatch": "Check all depicted structures, labels and their leaders against the public question and reference SVG; redraw incorrect relationships."}
    return {"issues": [{"code": row.code if row.code in ISSUE_CODES else "scientific_mismatch",
        "target": row.target if row.target in targets else "canvas",
        **({"rule": rules[row.code]} if row.code in rules else {})} for row in issues]}


async def workflow(llm, contract, policy, *, stage=None, frozen=False, owner=None, phase_deadline=None):
    if not isinstance(contract, QuestionVisualContractV3):
        raise IllustrationError("invalid_contract")
    if owner is None:
        return await _workflow(llm, contract, policy, stage=stage, phase_deadline=phase_deadline)
    from app.diagrams.materials import owner_context
    with owner_context(owner):
        return await _workflow(llm, contract, policy, stage=stage, phase_deadline=phase_deadline)


async def _workflow(llm, contract, policy, *, stage=None, phase_deadline=None):
    if policy == "off":
        raise IllustrationError("policy_disabled")
    deadline = time.monotonic()+settings.quiz_illustration_deadline_seconds
    if phase_deadline is not None:
        deadline = min(deadline, phase_deadline)
    budget = GenerationBudget(max_calls=settings.quiz_illustration_max_calls,
        max_repairs=settings.quiz_illustration_max_repairs, deadline=deadline,
        max_protocol_corrections=2)
    client = BudgetedLLM(BudgetedLLM.unwrap_provider(llm), budget)
    last_stage = "contract_validated"
    def emit(name, **data):
        nonlocal last_stage
        last_stage = name
        if stage:
            stage(name, {**data, **budget.summary()})
    async def protocol(call, *, reserve_calls):
        feedback = None
        while True:
            try:
                return await call(feedback)
            except (ValidationError, IllustrationError) as exc:
                if isinstance(exc, IllustrationError) and exc.code not in {
                        "invalid_contract", "scene_schema_invalid", "scene_asset_not_authorized", "joint_review_failed"}:
                    raise
                if not budget.take_protocol_correction(reserve_calls=reserve_calls):
                    reason = ("protocol_corrections_exhausted" if budget.protocol_corrections >= budget.max_protocol_corrections
                        else "deadline_exhausted" if budget.remaining_seconds <= 0 else "call_budget_exhausted")
                    details = {"terminal_reason": reason, **budget.summary()}
                    if isinstance(exc, IllustrationError):
                        exc.details.update(details)
                        raise
                    raise IllustrationError("scene_schema_invalid", details=details) from exc
                feedback = _protocol_feedback(exc)
                emit("repairing", failure_code=feedback["code"], correction="protocol")
    emit("contract_validated", contract_hash=contract.contract_hash)
    try:
        async with asyncio.timeout(budget.remaining_seconds):
            if not supports_images(client):
                raise IllustrationError("provider_unavailable")
            contract, requirements = await protocol(lambda feedback: declare(client, contract, policy, feedback=feedback),
                reserve_calls=2)
            emit("requirements_declared", contract_hash=contract.contract_hash,
                brief=requirements.model_dump(mode="json"))
            if requirements.visual_role == "none":
                return {"status": "not_required", "contract": contract, "metrics": budget.summary()}
            bundle = await asyncio.to_thread(v3_retrieval.retrieve, requirements.needs,
                subject=contract.public_question.subject, education_level=contract.public_question.grade)
            emit("candidates_retrieved", candidate_count=len(bundle.assets),
                catalog_version=bundle.catalog_version, retrieval_trace=bundle.retrieval_trace)
            extra_retrieval = False
            async def proposed(feedback, *, previous=None, image=None):
                return await compose(client, contract, bundle, draft=previous, feedback=feedback,
                    png=image, additional_retrieval_allowed=not extra_retrieval)
            draft = await protocol(lambda feedback: proposed(feedback), reserve_calls=1)
            if isinstance(draft, MaterialRequestV3):
                extra_retrieval = True
                bundle = await asyncio.to_thread(v3_retrieval.retrieve, draft.needs,
                    subject=contract.public_question.subject, education_level=contract.public_question.grade,
                    existing=bundle)
                emit("candidates_retrieved", candidate_count=len(bundle.assets), retrieval_retry=1,
                    retrieval_trace=bundle.retrieval_trace)
                draft = await protocol(lambda feedback: proposed(feedback), reserve_calls=1)
            while True:
                emit("scene_proposed", scene_hash=digest(draft.model_dump(mode="json")))
                png = None
                terminal_code = "scene_schema_invalid"
                feedback = None
                try:
                    compiled = await asyncio.to_thread(compile_svg, draft, contract=contract, bundle=bundle)
                    emit("compiled", scene_hash=compiled.source.scene_hash)
                    emit("static_checked", source_hash=compiled.source.svg_source_hash)
                    png, bounds = await asyncio.to_thread(render_svg, compiled.illustration)
                    compiled.source.rendered_bounds = bounds
                    emit("preview_rendered", content_hash=compiled.illustration.content_hash)
                    if settings.quiz_illustration_visual_review != "active":
                        raise IllustrationError("visual_review_failed")
                    result = await protocol(lambda feedback: combined_review(client, contract, compiled, png,
                        bundle, feedback=feedback), reserve_calls=0)
                    emit("jointly_reviewed", review_status=result.status)
                    if result.status == "needs_question_revision":
                        raise IllustrationError("question_material_incomplete")
                    if result.status == "passed":
                        compiled.source.review_gates = {"machine": "passed", "combined": "passed"}
                        compiled.source.review_evidence = {"combined": result}
                        emit("publish_ready", content_hash=compiled.illustration.content_hash)
                        return {"status": "ready", "contract": contract, "compiled": compiled,
                            "png": png, "reviews": {"machine": "passed", "combined": result.model_dump(mode="json")},
                            "metrics": budget.summary()}
                    errors = [row for row in result.issues if row.severity == "error"]
                    if not errors or any(not row.repairable for row in errors):
                        raise IllustrationError("joint_review_failed")
                    terminal_code = "joint_review_failed"
                    feedback = repair_feedback(errors, contract)
                except IllustrationError as exc:
                    if not exc.repairable:
                        raise
                    terminal_code = exc.code
                    feedback = {"issues": [{"code": exc.details.get("issue_code", exc.code), "target": exc.target}],
                        "geometry_feedback": exc.details if exc.code == "geometry_out_of_bounds" else {},
                        "svg_feedback": exc.details.get("svg_feedback", {}),
                        "sanitizer_code": exc.details.get("sanitizer_code", "")}
                # Reserve a replacement AND its single combined review.
                if not budget.can_call(reserve_calls=1) or not budget.take_repair():
                    raise IllustrationError(terminal_code, details={
                        "terminal_reason": "scene_repairs_exhausted" if budget.repairs >= budget.max_repairs else "call_budget_exhausted",
                        **budget.summary()})
                emit("repairing", failure_code=terminal_code, correction="drawing")
                old_draft = draft
                draft = await protocol(lambda correction: proposed(
                    {**(feedback or {}), "protocol_feedback": correction}, previous=old_draft, image=png), reserve_calls=1)
                if isinstance(draft, MaterialRequestV3):
                    # Additional retrieval is still legal during the first
                    # repair, but cannot reset the source or call budgets.
                    extra_retrieval = True
                    bundle = await asyncio.to_thread(v3_retrieval.retrieve, draft.needs,
                        subject=contract.public_question.subject, education_level=contract.public_question.grade,
                        existing=bundle)
                    emit("candidates_retrieved", candidate_count=len(bundle.assets), retrieval_retry=1)
                    draft = await protocol(lambda correction: proposed(
                        {**(feedback or {}), "protocol_feedback": correction}, previous=old_draft, image=png), reserve_calls=1)
    except TimeoutError as exc:
        raise IllustrationError("budget_exhausted", details={"terminal_reason":
            "deadline_exhausted" if budget.remaining_seconds <= 0 else "call_budget_exhausted", **budget.summary()}) from exc
    finally:
        # A failed provider call still consumes a call and wall time. Preserve
        # the actual phase while reporting its final operational counters.
        emit(last_stage, metrics_final=True)


async def generate_question(llm, question: dict, policy: str, *, grade="", phase_deadline=None,
                            stage=None, grounding_context="") -> dict:
    from app.core.quiz_verify import freeze_rubric
    question = dict(question)
    rubric = freeze_rubric(question, "draft")
    if rubric:
        question["rubric_criteria"] = rubric["criteria"]
    contract = material_contract(question, question_ref="draft_"+uuid.uuid4().hex, grade=grade)
    from .authoring import bind_review_grounding
    contract = bind_review_grounding(contract, grounding_context)
    result = await workflow(llm, contract, policy, phase_deadline=phase_deadline, stage=stage)
    out = dict(question)
    out.pop("diagram_scene", None)
    if result["status"] == "not_required":
        for field in ("material_contract", "diagram_source", "visual_spec", "illustration_review"):
            out.pop(field, None)
        out.update(illustration=None, visual_role="none", _illustration_metrics=result["metrics"])
        return out
    compiled = result["compiled"]
    out.update(illustration=compiled.illustration.model_dump(mode="json"),
        diagram_source=compiled.source.model_dump(mode="json"), visual_role=result["contract"].visual_role,
        visual_spec=result["contract"].visual_spec.model_dump(mode="json"),
        material_contract=result["contract"].model_dump(mode="json"),
        illustration_review=compiled.source.review_gates,
        verification={"illustration_check": "passed", "status": "passed"},
        _illustration_metrics=result["metrics"])
    return out

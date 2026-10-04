"""Independent PNG/question audit; historical separate audits remain readable."""
from __future__ import annotations

import json
import asyncio

from app.core.config import settings
from app.prompts.registry import get

from .contracts import IllustrationError, ReviewResult
from .preview import image_message, measurement_crops
from .requirements import compact_schema, structured, validation_details


def repair_feedback(issues, compiled):
    """Keep private audit prose out of the public-scene composer."""
    from typing import get_args
    from .contracts import FailureCode, PatchOperation
    from app.diagrams.semantics import RECIPES
    codes = set(get_args(FailureCode)) | {
        "scientific_mismatch", "condition_added", "answer_leak", "missing_relation", "missing_subject",
        "unreadable_label", "wrong_label", "label_position", "label_layout", "geometry_mismatch",
        "data_mismatch", "direction_mismatch", "reading_mismatch", "scale_mismatch", "prohibited_addition",
    }
    operations = set(get_args(PatchOperation.model_fields["op"].annotation))
    scene = compiled.source.scene
    targets = {"canvas", *(row.instance_id for row in scene.asset_instances),
        *(row.annotation_id for row in scene.annotations), *(row.relation_id for row in scene.relations),
        *(row.group_id for row in scene.groups)}
    for node in scene.asset_instances:
        targets.update([node.entity_id, *node.entity_map.values()])
        if node.asset_id in RECIPES:
            targets.update(node.instance_id+":"+role for role, *_ in RECIPES[node.asset_id].children)
    targets.discard("")
    result = []
    for issue in issues:
        target = issue.target if issue.target in targets else "canvas"
        result.append({"code": issue.code if issue.code in codes else "scientific_mismatch",
            "target": target, "severity": "error", "repairable": True,
            "suggested_operation": issue.suggested_operation if issue.suggested_operation in operations else "",
            "allowed_operations": sorted(operations),
            "geometry": {"canvas_size": [scene.canvas.width, scene.canvas.height],
                "bounds": {key: value for key, value in compiled.source.layout_report.bounds.items()
                    if key == target or key.startswith(target+":")}}})
    return result


def supports_images(llm) -> bool:
    client = llm
    while hasattr(client, "llm"):
        client = client.llm
    return bool(getattr(client, "supports_images", settings.llm_supports_images))


async def review(llm, contract, compiled, png, *, joint=False, combined=False, feedback=None):
    if not supports_images(llm):
        raise IllustrationError("provider_unavailable")
    schema = ReviewResult.model_json_schema()
    relation_ids = {relation.id for relation in [*contract.required_relations, *contract.internal_relations]}
    for field, ids in (("verified_facts", [fact.id for fact in contract.facts]), ("verified_relations", sorted(relation_ids))):
        schema["properties"][field] = {"type": "array", "items": {"enum": ids}} if ids else {"type": "array", "maxItems": 0}
    payload = {"question": contract.public_question.model_dump(mode="json"),
        "allowed_material": contract.composer_view(),
        "review_facts": [fact.model_dump(mode="json") for fact in contract.facts],
        "machine_checks": compiled.source.layout_report.model_dump(mode="json"),
        "authoring_gold": contract.authoring_gold,
        "frozen_question": contract.frozen_question, "review_schema": compact_schema(schema),
        "protocol_feedback": feedback}
    payload["audit_scope"] = {"visual_role": contract.visual_role,
        "required_entity_ids": [entity.id for entity in contract.entities],
        "required_relation_ids": [relation.id for relation in contract.required_relations],
        "required_internal_relation_ids": [relation.id for relation in contract.internal_relations],
        "required_marks": contract.required_marks,
        "must_depict_fact_ids": [fact.id for fact in contract.facts if fact.display_policy == "depict_only"],
        "text_supplied_fact_ids": [fact.id for fact in contract.facts if fact.display_policy == "explicit"],
        "prohibited_additions": contract.prohibited_additions}
    from app.diagrams.guidance import for_asset
    payload["selected_material_guidance"] = [{"asset_id": asset_id,
        "text": guide["hints"]["review"]} for asset_id, version in compiled.source.asset_versions.items()
        if "review" in (guide := for_asset(asset_id, version))["hints"]]
    from app.diagrams.semantics import asset_card, parameter_semantics, resolved_calibration
    payload["selected_material_interfaces"] = [{"asset_id": aid,
        "parameters": parameter_semantics(aid, version), "calibration": asset_card(aid).get("calibration", {}) if not aid.startswith("material.") else {}}
        for aid, version in compiled.source.asset_versions.items()]
    payload["resolved_parameters"] = compiled.source.resolved_parameters
    payload["rendered_svg"] = compiled.illustration.svg
    payload["calibration_instances"] = {}
    for node in compiled.source.scene.asset_instances:
        if node.asset_id.startswith("material."):
            continue
        card = asset_card(node.asset_id)
        params = dict(compiled.source.resolved_parameters.get(node.instance_id, {}))
        for parent, (child, key) in card.get("parameter_bindings", {}).items():
            values = compiled.source.resolved_parameters.get(node.instance_id+":"+child, {})
            if key in values:
                params[parent] = values[key]
        payload["calibration_instances"][node.instance_id] = resolved_calibration(card, params)
    name = "quiz_illustration_combined_review" if combined else "quiz_illustration_question_audit" if joint else "quiz_illustration_review"
    crops = await asyncio.to_thread(measurement_crops, compiled)
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get(name, "3.2.0" if combined else "2.20.0").text},
        {"role": "user", "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                                      image_message(png), *crops]}],
        temperature=.1, max_tokens=1600, disable_thinking=True)
    try:
        result = ReviewResult.model_validate(structured(raw))
    except ValueError as exc:
        raise IllustrationError("joint_review_failed" if joint or combined else "visual_review_failed", details={
            "protocol_invalid": True, **validation_details(exc)}) from exc
    except IllustrationError as exc:
        exc.details = {**exc.details, "protocol_invalid": True}
        raise
    if result.status == "failed" and result.issues and all(issue.severity == "warning" for issue in result.issues):
        result.status = "passed"
        result.rationale_codes = [*result.rationale_codes[:11], "warning_only_failed_status_normalized"]
    fact_ids = {fact.id for fact in contract.facts}
    if set(result.verified_facts) - fact_ids:
        raise IllustrationError("visual_review_failed", details={"protocol_invalid": True,
            "field": ["verified_facts"], "rule": "Use only schema-authorized fact IDs.", "allowed_ids": sorted(fact_ids)})
    if set(result.verified_relations) - relation_ids or result.status == "passed" and {
            relation.id for relation in contract.internal_relations} - set(result.verified_relations):
        raise IllustrationError("joint_review_failed" if joint or combined else "visual_review_failed", details={
            "protocol_invalid": True, "field": ["verified_relations"],
            "rule": "Verify all required internal relations and use only schema-authorized IDs.", "allowed_ids": sorted(relation_ids)})
    required = {fact.id for fact in contract.facts if fact.display_policy == "depict_only"}
    if result.status == "passed" and contract.visual_role == "essential" and required - set(result.verified_facts):
        raise IllustrationError("visual_review_failed", details={"protocol_invalid": True,
            "field": ["verified_facts"], "rule": "A passing essential image must verify each depicted required fact.",
            "required_ids": sorted(required)})
    return result

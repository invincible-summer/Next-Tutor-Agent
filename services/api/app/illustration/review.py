"""Independent actual-PNG review and joint question audit. Never self-review."""
from __future__ import annotations

import json
import asyncio

from app.core.config import settings
from app.prompts.registry import get

from .contracts import IllustrationError, ReviewResult
from .preview import image_message, measurement_crops
from .requirements import structured


def supports_images(llm) -> bool:
    client = llm
    while hasattr(client, "llm"):
        client = client.llm
    return bool(getattr(client, "supports_images", settings.llm_supports_images))


async def review(llm, contract, compiled, png, *, joint=False):
    if not supports_images(llm):
        raise IllustrationError("provider_unavailable")
    schema = ReviewResult.model_json_schema()
    schema["properties"]["verified_facts"]["items"] = {"type": "string", "enum": [fact.id for fact in contract.facts]}
    payload = {"question": contract.public_question.model_dump(mode="json"),
        "allowed_material": contract.composer_view(),
        "review_facts": [fact.model_dump(mode="json") for fact in contract.facts],
        "machine_checks": compiled.source.layout_report.model_dump(mode="json"),
        "authoring_gold": contract.authoring_gold,
        "frozen_question": contract.frozen_question, "review_schema": schema,
        "verified_fact_ids": [fact.id for fact in contract.facts]}
    from app.diagrams.guidance import for_asset
    payload["selected_material_guidance"] = [{"asset_id": asset_id,
        "text": guide["hints"]["review"]} for asset_id, version in compiled.source.asset_versions.items()
        if "review" in (guide := for_asset(asset_id, version))["hints"]]
    name = "quiz_illustration_question_audit" if joint else "quiz_illustration_review"
    crops = await asyncio.to_thread(measurement_crops, compiled)
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get(name, "2.1.0").text},
        {"role": "user", "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                                      image_message(png), *crops]}],
        temperature=.1, max_tokens=1600, disable_thinking=True)
    try:
        result = ReviewResult.model_validate(structured(raw))
    except ValueError as exc:
        raise IllustrationError("joint_review_failed" if joint else "visual_review_failed") from exc
    fact_ids = {fact.id for fact in contract.facts}
    if set(result.verified_facts) - fact_ids:
        raise IllustrationError("visual_review_failed")
    required = {fact.id for fact in contract.facts if fact.display_policy == "depict_only"}
    if result.status == "passed" and contract.visual_role == "essential" and required - set(result.verified_facts):
        raise IllustrationError("visual_review_failed")
    return result

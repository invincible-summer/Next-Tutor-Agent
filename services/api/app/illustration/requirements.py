"""One structured requirements call; it has no tools or catalog access."""
from __future__ import annotations

import json
import re

from app.core.json_utils import extract_json_object
from app.prompts.registry import get

from .contracts import IllustrationError, QuestionMaterialContract, VisualBriefV2


def structured(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw.encode()) > 64 * 1024:
        raise IllustrationError("scene_schema_invalid")
    if re.search(r"<\s*(svg|script|html)|https?://|file://|\b(?:javascript|eval)\s*[:(]", raw, re.I):
        raise IllustrationError("scene_schema_invalid")
    result = extract_json_object(raw)
    if not isinstance(result, dict):
        raise IllustrationError("scene_schema_invalid")
    return result


def visible_text(contract: QuestionMaterialContract) -> str:
    return contract.public_question.stem + "\n" + "\n".join(contract.public_question.options.values())


def allowed_label(text: str, contract: QuestionMaterialContract) -> bool:
    return bool(text.strip()) and (text in contract.required_marks or
        text in [u.symbol for u in contract.unknowns] or text in visible_text(contract))


def capability_guide(context="", *, phase="authoring"):
    """Bounded local context matching; no full inventory of asset prompts."""
    from app.diagrams.catalog import search, _normalize, catalog
    from app.diagrams.semantics import RECIPES, asset_card
    from app.diagrams.guidance import for_asset, records
    from app.diagrams.materials import search_cards
    from .contracts import Capability
    from typing import get_args
    hits = search(context, gallery=True, top_k=24) if context else []
    normalized = _normalize(context)
    # Match common names with inert gallery suffixes removed. This is local
    # metadata selection, not an inventory of special model instructions.
    import re
    for asset in catalog()[1].values():
        names = [re.sub(r"符号|示意图|完整|结构图|装置|图$|器$", "", name) for name in
            [asset.title, *asset.aliases]]
        if context and asset.review.get("status") == "passed" and any(
                len(name) >= 2 and _normalize(name) in normalized for name in names) and asset not in hits:
            hits.append(asset)
    ids = {asset.id for asset in hits}
    assets = catalog()[1]
    matched_guides = [aid for aid, guide in records().items() if context and any(
        _normalize(term) in normalized for term in guide.get("match_terms", [])) and aid != "material.static"
        and (aid in RECIPES or aid in assets and assets[aid].review.get("status") == "passed")]
    recipes = []
    for asset_id, recipe in RECIPES.items():
        roles = {child for _, child, *_ in recipe.children}
        if roles <= ids or asset_id in matched_guides or any(_normalize(name) in normalized
                for name in (recipe.title, *recipe.aliases)):
            recipes.append(asset_id)
    cards = search_cards(context) if context else []
    cards += [asset_card(aid) for aid in dict.fromkeys([*recipes, *matched_guides, *(asset.id for asset in hits)])]
    relevant = []
    for card in cards[:12]:
        guide = for_asset(card["asset_id"], card["version"])
        relevant.append({"name": card["title"], "capabilities": card["capabilities"],
            "entity_policy": "recipe_children" if card.get("children") else
                "single_construction" if card["kind"] == "construction" else "single_object",
            "fixed_marks": [] if any(key in card["parameters"] for key in {
                "values", "points", "items", "labels", "edges", "matrix", "lanes", "people", "flows", "groups"}) else
                list(dict.fromkeys(mark for mark in card.get("intrinsic_marks", [])
                    if len(mark) <= 80 and not re.search(r"\d", mark)))[:24],
            "parameters": {key: {field: value for field, value in spec.items() if field in {
                "type", "unit", "required", "non_quantitative_allowed", "condition_bearing"}}
                for key, spec in card["parameters"].items()},
            "supported_views": card["supported_views"], "calibration": card.get("calibration"),
            "entities": [child["child_id"] for child in card.get("children", [])],
            "usage_guidance": {"version": guide["version"], "text": guide["hints"].get(phase, "")}})
    return {"capabilities": list(get_args(Capability)), "relevant_materials": relevant,
        "selection": "local context match, at most 12 materials; no preview values are question facts"}


def authoring_material_schema():
    schema = QuestionMaterialContract.model_json_schema()
    fields = {"visual_role", "entities", "facts", "required_relations", "required_marks",
              "unknowns", "prohibited_additions", "presentation_constraints"}
    return {"type": "object", "additionalProperties": False, "$defs": schema["$defs"],
        "properties": {key: value for key, value in schema["properties"].items() if key in fields},
        "required": ["visual_role", "entities", "facts"]}


def validate_brief(brief: VisualBriefV2, contract: QuestionMaterialContract, policy: str):
    if brief.missing_information:
        raise IllustrationError("missing_material")
    if brief.visual_role == "none":
        if policy == "required" or contract.visual_role == "essential":
            raise IllustrationError("no_meaningful_visual")
        return
    if contract.frozen_question and brief.visual_role == "essential":
        raise IllustrationError("question_material_incomplete")
    if contract.visual_role == "essential" and brief.visual_role != "essential":
        raise IllustrationError("invalid_contract")
    facts = {f.id for f in contract.facts if f.display_policy != "hidden"}
    entities = {e.id for e in contract.entities}
    relation_ids = {r.id for r in contract.required_relations}
    for need in brief.needs:
        if set(need.fact_bindings) - facts or set(need.entity_ids) - entities:
            raise IllustrationError("invalid_contract")
        if not need.entity_ids:
            raise IllustrationError("invalid_contract")
    if any(set(r.fact_refs) - facts - relation_ids for r in brief.relations_to_express):
        raise IllustrationError("invalid_contract")
    if any(not allowed_label(text, contract) for text in brief.labels_to_show):
        raise IllustrationError("invalid_contract")


async def declare(llm, contract: QuestionMaterialContract, policy: str):
    payload = contract.composer_view()
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get("quiz_illustration_requirements", "2.2.0").text},
        {"role": "user", "content": json.dumps({"question_material_contract": payload,
            "illustration_policy": policy, "schema": VisualBriefV2.model_json_schema(),
            "available_capability_summary": capability_guide(visible_text(contract) + "\n" + "\n".join(
                contract.presentation_constraints.preferred_material_names + [e.name for e in contract.entities]), phase="requirements"),
            "material_schema": authoring_material_schema()}, ensure_ascii=False)}],
        temperature=.1, max_tokens=5000, disable_thinking=True)
    result = structured(raw)
    # Existing text questions have no v2 facts yet. Extraction is allowed only
    # with verifiable quotes; identity, gold and hashes stay server-owned.
    if "brief" in result:
        if set(result) - {"brief", "material"}:
            raise IllustrationError("invalid_contract")
        if not contract.entities and result.get("material"):
            fields = {"entities", "facts", "required_relations", "required_marks", "unknowns",
                      "prohibited_additions", "visual_role"}
            material = result["material"]
            if not isinstance(material, dict) or set(material) - fields:
                raise IllustrationError("invalid_contract")
            data = contract.model_dump(mode="json", exclude={"contract_hash"})
            # A frozen question's role cannot be changed to dodge the gate.
            if material.get("visual_role") == "essential" and contract.frozen_question:
                raise IllustrationError("question_material_incomplete")
            data.update(material)
            contract = QuestionMaterialContract.model_validate(data)
        result = result["brief"]
    try:
        brief = VisualBriefV2.model_validate(result)
    except ValueError as exc:
        raise IllustrationError("invalid_contract") from exc
    # Canonicalize an omitted association only when its existing fact owners
    # (or a single need covering the whole contract) determine it uniquely.
    # This fills identities, never new entities or scientific conditions.
    facts = {fact.id: fact for fact in contract.facts}
    for need in brief.needs:
        if not need.entity_ids:
            owners = {facts[ref].entity_id for ref in need.fact_bindings if ref in facts and facts[ref].entity_id}
            if len(brief.needs) == 1:
                owners = {entity.id for entity in contract.entities}
            if owners:
                need.entity_ids = sorted(owners)
    validate_brief(brief, contract, policy)
    return contract, brief

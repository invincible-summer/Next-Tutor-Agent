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
    if not text.strip():
        return False
    if text in contract.required_marks or text in [u.symbol for u in contract.unknowns] or text in visible_text(contract):
        return True
    # Formatting an already explicit, quoted value adds no new condition.
    # Accept only its own declared symbol and literal value/unit, not arbitrary
    # equations, derived values, hidden readings or answer announcements.
    normalized = re.sub(r"\s+", "", text)
    for fact in contract.facts:
        if fact.display_policy != "explicit" or fact.type != "scalar" or not fact.symbol or (
                fact.source_quote not in contract.source_text(fact.source_ref)):
            continue
        value = f"{fact.value:g}" + fact.unit
        if re.sub(r"\s+", "", value) not in re.sub(r"\s+", "", fact.source_quote):
            continue
        if normalized == re.sub(r"\s+", "", fact.symbol+"="+value):
            return True
    return False


def capability_guide(context="", *, phase="authoring", selected_asset_ids=()):
    """Bounded local context matching; no full inventory of asset prompts."""
    from app.diagrams.catalog import search, _normalize, catalog
    from app.diagrams.semantics import RECIPES, asset_card
    from app.diagrams.guidance import for_asset, records
    from app.diagrams.materials import search_cards
    from .contracts import Capability
    from typing import get_args
    hits = search(context, gallery=True, top_k=24) if context else []
    # Natural data-comparison questions may never say "chart". Expose a few
    # relevant data materials from local metadata instead of making the model
    # invent an unsearchable material name.
    if re.search(r"\d+(?:\.\d+)?\s*[、,，]\s*\d+(?:\.\d+)?\s*[、,，]\s*\d", context):
        hits += [asset for asset in search("统计 数据 柱状图", gallery=True, top_k=6) if asset not in hits]
    normalized = _normalize(context)
    # Match common names with inert gallery suffixes removed. This is local
    # metadata selection, not an inventory of special model instructions.
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
        # A sentence can separate object names with placement/relationship
        # clauses. Match registered child names independently, instead of
        # requiring the full recipe title to occur as one contiguous phrase.
        mentioned = {child for child in roles if any(len(name) >= 2 and _normalize(name) in normalized
            for name in (assets[child].title, *assets[child].aliases))}
        if roles <= ids or len(mentioned) >= max(2, (len(roles)+1)//2) or asset_id in matched_guides or any(_normalize(name) in normalized
                for name in (recipe.title, *recipe.aliases)):
            recipes.append(asset_id)
    cards = search_cards(context) if context else []
    cards += [asset_card(aid) for aid in dict.fromkeys([*recipes, *matched_guides, *(asset.id for asset in hits)])]
    if selected_asset_ids:
        cards = [card for card in cards if card["asset_id"] in selected_asset_ids]
        present = {card["asset_id"] for card in cards}
        cards += [asset_card(aid) for aid in selected_asset_ids if aid not in present]
    relevant = []
    for card in cards[:12]:
        guide = for_asset(card["asset_id"], card["version"])
        relevant.append({"name": card["title"], "capabilities": card["capabilities"],
            "entity_policy": "recipe_children" if card.get("children") else
                "single_construction" if card["kind"] == "construction" else "single_object",
            "fixed_marks": [] if any(key in card["parameters"] for key in {
                "values", "points", "items", "labels", "edges", "matrix", "lanes", "people", "flows", "groups"}) else
                list(dict.fromkeys(mark for mark in card.get("fixed_marks", card.get("intrinsic_marks", []))
                    if len(mark) <= 80 and not re.search(r"\d", mark)))[:24],
            "parameters": {key: {field: value for field, value in spec.items() if field in {
                "type", "role", "description", "fact_types", "unit", "required", "minimum", "maximum", "choices",
                "non_quantitative_allowed", "qualitative_state", "condition_bearing", "default_rule", "derived_from", "canonical_value", "svg_bindings"}}
                for key, spec in card["parameters"].items()},
            "supported_views": card["supported_views"], "calibration": card.get("calibration"),
            "entities": [child["child_id"] for child in card.get("children", [])],
            "structural_relations": card.get("relations", []),
            "parameter_owners": {key: binding[0] for key, binding in card.get("parameter_bindings", {}).items()},
            "usage_guidance": {"version": guide["version"], "text": guide["hints"].get(phase, "")}})
    result = {"capabilities": list(get_args(Capability)), "relevant_materials": relevant,
        "selection": "local context match, at most 12 materials; no preview values are question facts"}
    return result


def authoring_material_schema():
    schema = QuestionMaterialContract.model_json_schema()
    schema["$defs"]["MaterialFact"]["allOf"] = [
        {"if": {"properties": {"type": {"const": kind}}},
         "then": {"properties": {"value": {"type": value_type}}}}
        for kind, value_type in (("scalar", "number"), ("state", "boolean"),
            ("function", "string"), ("range", "array"))]
    schema["$defs"]["MaterialFact"]["allOf"].append({
        "if": {"properties": {"type": {"const": "label"}}},
        "then": {"properties": {"value": {"anyOf": [{"type": "string"},
            {"type": "array", "items": {"type": "string"}}]}}}})
    fields = {"visual_role", "entities", "facts", "required_relations", "internal_relations", "required_marks",
              "unknowns", "prohibited_additions", "presentation_constraints"}
    return {"type": "object", "additionalProperties": False, "$defs": schema["$defs"],
        "properties": {key: value for key, value in schema["properties"].items() if key in fields},
        "required": ["visual_role", "entities", "facts"]}


def named_material_sources(name):
    """An explicitly named material's full source, never a related inventory."""
    from app.diagrams.catalog import _normalize, catalog
    from app.diagrams.materials import search_cards
    from app.diagrams.semantics import RECIPES, asset_card
    from .preview import material_sources
    query = _normalize(name)
    cards = [card for card in search_cards(name) if _normalize(card["title"]) == query]
    if not cards:
        ids = [aid for aid, recipe in RECIPES.items() if query in {
            _normalize(term) for term in (recipe.title, *recipe.aliases)}]
        ids += [aid for aid, asset in catalog()[1].items() if asset.review.get("status") == "passed"
            and query in {_normalize(term) for term in (asset.title, *asset.aliases)}]
        cards = [asset_card(aid) for aid in ids[:1]]
    from types import SimpleNamespace
    return material_sources(SimpleNamespace(assets=cards[:1]))


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
            raise IllustrationError("invalid_contract", target=need.need_id+":references")
        if not need.entity_ids:
            raise IllustrationError("invalid_contract", target=need.need_id+":entity_ids")
    if any(set(r.fact_refs) - facts - relation_ids for r in brief.relations_to_express):
        raise IllustrationError("invalid_contract")
    for text in brief.labels_to_show:
        if not allowed_label(text, contract):
            raise IllustrationError("invalid_contract", target="labels_to_show",
                details={"invalid_label": text, "rule": "Use literal public text or required_marks; omit optional annotations."})


def can_extract_material(contract):
    # A partial existing scientific projection is still frozen authority. It
    # must not be erased just because it has no named drawing entities yet.
    return not any((contract.entities, contract.facts, contract.required_relations,
        contract.internal_relations, contract.required_marks, contract.unknowns,
        contract.prohibited_additions)) and contract.visual_role != "none"


async def declare(llm, contract: QuestionMaterialContract, policy: str, *, feedback=None):
    payload = contract.composer_view()
    selected = [row["asset_id"] for name in contract.presentation_constraints.preferred_material_names
        for row in named_material_sources(name)]
    summary = capability_guide(visible_text(contract) + "\n" + "\n".join(
        contract.presentation_constraints.preferred_material_names + [e.name for e in contract.entities]),
        phase="requirements", selected_asset_ids=selected)
    schema = VisualBriefV2.model_json_schema()
    roles = [contract.visual_role]
    if policy == "auto" and contract.visual_role != "essential":
        roles.append("none")
    schema["properties"]["visual_role"] = ({"const": roles[0]} if len(roles) == 1 else {"enum": list(dict.fromkeys(roles))})
    if summary["relevant_materials"]:
        rows = summary["relevant_materials"]
        schema["$defs"]["MaterialNeed"]["properties"]["name"] = {"enum": [row["name"] for row in rows]}
        schema["$defs"]["MaterialNeed"]["allOf"] = [{
            "if": {"properties": {"name": {"const": row["name"]}}},
            "then": {"properties": {"capabilities": {"type": "array", "items": {"enum": row["capabilities"]}}}}}
            for row in rows]
        views = set.intersection(*(set(row["supported_views"]) for row in rows))
        if views:
            schema["properties"]["view"] = {"enum": sorted(views)}
    extraction = can_extract_material(contract)
    if extraction:
        material_schema = authoring_material_schema()
        # Frozen text can acquire a quoted drawing projection, never an
        # authoring blueprint or a changed presentation/scientific role.
        material_schema["properties"].pop("presentation_constraints")
        material_schema["properties"]["visual_role"] = {"const": contract.visual_role}
        definitions = {**material_schema.pop("$defs"), **schema.pop("$defs")}
        schema = {"type": "object", "additionalProperties": False,
            "$defs": definitions,
            "properties": {"material": material_schema, "brief": schema},
            "required": ["material", "brief"]}
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get("quiz_illustration_requirements", "2.20.0").text},
        {"role": "user", "content": json.dumps({"question_material_contract": payload,
            "illustration_policy": policy, "schema": schema,
            "available_capability_summary": summary,
            "extract_material_from_public_question": extraction,
            "repair_feedback": feedback}, ensure_ascii=False)}],
        temperature=.1, max_tokens=5000, disable_thinking=True)
    try:
        return accept_declaration(raw, contract, policy)
    except (IllustrationError, ValueError) as exc:
        error = exc if isinstance(exc, IllustrationError) else IllustrationError("invalid_contract")
        error.details = {**error.details, "invalid_response": raw[:64*1024]}
        raise error from exc


def accept_declaration(raw, contract, policy):
    """Validate a quoted drawing projection before it can become a contract."""
    result = structured(raw)
    cosmetic_fact_ids = set()
    # Existing text questions have no v2 facts yet. Extraction is allowed only
    # with verifiable quotes; identity, gold and hashes stay server-owned.
    if "brief" in result:
        if set(result) - {"brief", "material"}:
            raise IllustrationError("invalid_contract")
        if result.get("material") and not can_extract_material(contract):
            raise IllustrationError("invalid_contract", details={"rule": "Existing material is immutable; return only a visual brief."})
        if can_extract_material(contract) and result.get("material"):
            fields = {"entities", "facts", "required_relations", "internal_relations", "required_marks", "unknowns",
                      "prohibited_additions", "visual_role"}
            material = result["material"]
            if not isinstance(material, dict) or set(material) - fields:
                raise IllustrationError("invalid_contract")
            data = contract.model_dump(mode="json", exclude={"contract_hash"})
            # A frozen question's role cannot be changed to dodge the gate.
            if material.get("visual_role") == "essential" and contract.frozen_question:
                raise IllustrationError("question_material_incomplete")
            if material.get("visual_role", contract.visual_role) != contract.visual_role:
                raise IllustrationError("invalid_contract")
            for relation in material.get("required_relations", []):
                if isinstance(relation, dict) and relation.get("type") == "ordered_left_to_right" and re.search(
                        r"上方|下方|上面|下面|above|below", relation.get("source_quote", ""), re.I) and not re.search(
                        r"左|右|横向|left|right", relation.get("source_quote", ""), re.I):
                    raise IllustrationError("invalid_contract", target=relation.get("id", "relation")+":type",
                        details={"rule": "ordered_left_to_right means horizontal order only. Keep vertical placement in layout_intent; do not invent an unsupported scientific relation."})
            # This is the first drawing projection of frozen public text,
            # not a mutation of an existing frozen scientific contract. Apply
            # the same interface-proven cosmetic/qualitative normalization as
            # authoring, then bind it back to the original public identity.
            from .contracts import material_contract
            public = contract.public_question.model_dump(mode="json")
            try:
                projected = material_contract({**public, "material_contract": {**material,
                        "presentation_constraints": contract.presentation_constraints.model_dump(mode="json")}},
                    question_ref=contract.question_ref, revision=contract.question_revision,
                    frozen=contract.frozen_question, grade=contract.public_question.grade,
                    illustration_guidance=contract.illustration_guidance,
                    normalize_public_projection=True)
                data.update(projected.model_dump(mode="json", include=fields))
                contract = QuestionMaterialContract.model_validate(data)
                cosmetic_fact_ids = {row.get("id") for row in material.get("facts", []) if isinstance(row, dict)} - {
                    fact.id for fact in contract.facts}
            except IllustrationError:
                raise
            except ValueError as exc:
                for error in exc.errors(include_input=False, include_url=False) if hasattr(exc, "errors") else []:
                    underlying = error.get("ctx", {}).get("error")
                    if isinstance(underlying, IllustrationError):
                        raise underlying from exc
                raise IllustrationError("invalid_contract", details={"rule": "Follow the material schema and quoted public facts."}) from exc
        result = result["brief"]
    try:
        brief = VisualBriefV2.model_validate(result)
    except ValueError as exc:
        raise IllustrationError("invalid_contract") from exc
    if cosmetic_fact_ids:
        for need in brief.needs:
            need.fact_bindings = [ref for ref in need.fact_bindings if ref not in cosmetic_fact_ids]
        for relation in brief.relations_to_express:
            relation.fact_refs = [ref for ref in relation.fact_refs if ref not in cosmetic_fact_ids]
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

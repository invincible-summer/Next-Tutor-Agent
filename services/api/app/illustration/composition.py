"""Closed composition/patch protocol; no SVG and no editable question fields."""
from __future__ import annotations

import json

from app.diagrams.catalog import digest
from app.prompts.registry import get

from .contracts import (CandidateBundleV2, CannotComplete, IllustrationError,
    MaterialRequest, QuestionMaterialContract, SceneDraftV2, ScenePatchV2, VisualBriefV2)
from .requirements import allowed_label, compact_schema, structured, validation_details


def validate_scene(scene: SceneDraftV2, contract: QuestionMaterialContract,
                   brief: VisualBriefV2, bundle: CandidateBundleV2):
    needs = {n.need_id: n for n in brief.needs}
    counts = {n.need_id: 0 for n in brief.needs}
    facts = {f.id: f for f in contract.facts}
    entities = {e.id for e in contract.entities}
    for instance in scene.asset_instances:
        if not bundle.allowed(instance.need_id, instance.asset_id, instance.version):
            raise IllustrationError("scene_asset_not_authorized", target=instance.instance_id, details={
                "field": ["asset_instances", instance.instance_id, "asset_id"],
                "rule": "Use a candidate authorized for this need and its exact version.",
                "allowed_assets": [{"asset_id": card["asset_id"], "version": card["version"]}
                    for card in bundle.assets if bundle.allowed(instance.need_id, card["asset_id"], card["version"])]})
        from app.diagrams.semantics import RECIPES, parameter_semantics
        specs = parameter_semantics(instance.asset_id, instance.version)
        if instance.asset_id in RECIPES and set(instance.entity_map) != {
                role for role, *_ in RECIPES[instance.asset_id].children}:
            raise IllustrationError("scene_schema_invalid", target=instance.instance_id+":entity_map",
                details={"rule": "Map exactly the registered recipe child keys to authorized entities."})
        if instance.asset_id not in RECIPES and instance.entity_id and instance.entity_map and all(
                key in specs and ref in facts and facts[ref].entity_id == instance.entity_id and
                ref in needs[instance.need_id].fact_bindings and compatible_parameter_fact(facts[ref], specs[key], key,
                    to_scale=contract.presentation_constraints.to_scale)
                for key, ref in instance.entity_map.items()):
            for key, ref in instance.entity_map.items():
                if key in instance.fact_bindings and instance.fact_bindings[key] != ref:
                    raise IllustrationError("parameter_unbound", target=instance.instance_id)
                instance.fact_bindings[key] = ref
            instance.entity_map = {}
        if instance.entity_id and instance.entity_id not in needs[instance.need_id].entity_ids:
            raise IllustrationError("scene_asset_not_authorized", target=instance.instance_id)
        if set(instance.entity_map.values()) - entities:
            raise IllustrationError("scene_asset_not_authorized", target=instance.instance_id)
        if set(instance.entity_map.values()) - set(needs[instance.need_id].entity_ids):
            raise IllustrationError("scene_asset_not_authorized", target=instance.instance_id)
        from app.diagrams.semantics import RECIPES
        if instance.asset_id not in RECIPES and not instance.entity_id and set(instance.entity_map) == {"body"}:
            # A unique body association denotes the same component identity;
            # canonicalize it without authorizing any extra part or entity.
            instance.entity_id = instance.entity_map["body"]
            instance.entity_map = {}
            for annotation in scene.annotations:
                if annotation.target.instance == instance.instance_id + ":body":
                    annotation.target.instance = instance.instance_id
                    annotation.target.region = "body"
        if instance.asset_id not in RECIPES and instance.entity_id and instance.entity_map and (
                set(instance.entity_map) <= {"body", instance.entity_id} and
                set(instance.entity_map.values()) == {instance.entity_id}):
            # Repeating the one already authorized entity adds no structure.
            instance.entity_map = {}
        if instance.asset_id not in RECIPES and instance.entity_map:
            raise IllustrationError("scene_schema_invalid", target=instance.instance_id)
        # Fill existing bindings only when both fact and parameter association
        # are unique. Never guess from IDs, preview defaults or answer text.
        from app.diagrams.semantics import parameter_semantics
        recipe = RECIPES.get(instance.asset_id)
        for key, spec in parameter_semantics(instance.asset_id, instance.version).items():
            if key in instance.fact_bindings or not spec.get("condition_bearing"):
                continue
            owner = instance.entity_map.get(recipe.bindings[key][0], "") if recipe else instance.entity_id
            matches = [f.id for f in contract.facts if owner and f.entity_id == owner
                and f.display_policy != "hidden" and f.id in needs[instance.need_id].fact_bindings
                and compatible_parameter_fact(f, spec, key, to_scale=contract.presentation_constraints.to_scale)
                and (f.predicate or len([candidate for candidate, candidate_spec in specs.items()
                    if candidate_spec.get("condition_bearing") and compatible_parameter_fact(
                        f, candidate_spec, candidate, to_scale=contract.presentation_constraints.to_scale)]) == 1)]
            if len(matches) == 1:
                instance.fact_bindings[key] = matches[0]
        if any(ref not in facts or facts[ref].display_policy == "hidden" for ref in instance.fact_bindings.values()):
            raise IllustrationError("parameter_unbound", target=instance.instance_id)
        if set(instance.fact_bindings.values()) - set(needs[instance.need_id].fact_bindings):
            raise IllustrationError("parameter_unbound", target=instance.instance_id)
        for key, ref in instance.fact_bindings.items():
            if facts[ref].display_policy == "depict_only":
                # Server bindings are authoritative. A model placeholder or
                # guessed numeric value must never alter the depicted fact.
                instance.params.pop(key, None)
        counts[instance.need_id] += 1
    if any(counts[n.need_id] != n.quantity for n in brief.needs if n.priority == "required"):
        raise IllustrationError("missing_material")
    labels = [a.text for a in scene.annotations]
    if any(not allowed_label(text, contract) for text in labels):
        raise IllustrationError("parameter_unbound")
    for annotation in scene.annotations:
        if set(annotation.fact_refs) - set(facts) or any(
                facts[ref].display_policy in {"hidden", "depict_only"} for ref in annotation.fact_refs):
            raise IllustrationError("parameter_unbound", target=annotation.annotation_id)
    instance_ids = {node.instance_id for node in scene.asset_instances}
    if any(target.local_point is not None for relation in scene.relations for target in (relation.start, relation.end)):
        raise IllustrationError("relation_unrealizable")
    for node in scene.asset_instances:
        if node.asset_id in RECIPES:
            instance_ids |= {node.instance_id+":"+role for role, *_ in RECIPES[node.asset_id].children}
            # A recipe has no physical parent port. Resolve a shorthand only
            # if its registered child geometry exposes exactly one such port.
            from app.diagrams.semantics import asset_card
            for target in [end for relation in scene.relations for end in (relation.start, relation.end)] + [
                    annotation.target for annotation in scene.annotations]:
                if target.instance != node.instance_id:
                    continue
                # A whole recipe is not a rendered entity. A label quoting a
                # uniquely owned explicit fact can identify its actual child;
                # resolve that existing association without guessing geometry.
                annotation = next((row for row in scene.annotations if row.target is target), None)
                owners = {facts[ref].entity_id for ref in annotation.fact_refs if ref in facts} if annotation else set()
                children = [role for role, entity in node.entity_map.items() if entity in owners]
                if len(owners) == len(children) == 1 and not target.port and target.local_point is None:
                    child_id = next(aid for role, aid, *_ in RECIPES[node.asset_id].children if role == children[0])
                    if not target.region or target.region in asset_card(child_id)["nominal_geometry"].get("regions", {}):
                        target.instance = node.instance_id+":"+children[0]
                # Accept a qualified child key only when that exact registered
                # child exposes the requested port/region. No alias guessing.
                for field in ("port", "region"):
                    role, separator, key = getattr(target, field).partition(":")
                    children = {child: aid for child, aid, *_ in RECIPES[node.asset_id].children}
                    if separator and role in children:
                        geometry = asset_card(children[role])["nominal_geometry"]
                        if key in geometry.get("ports" if field == "port" else "regions", {}):
                            target.instance = node.instance_id+":"+role
                            setattr(target, field, key)
                if target.instance != node.instance_id or not target.port:
                    continue
                children = [role for role, aid, *_ in RECIPES[node.asset_id].children
                    if target.port in asset_card(aid)["nominal_geometry"].get("ports", {})]
                if len(children) == 1:
                    target.instance = node.instance_id+":"+children[0]
    for group in scene.groups:
        if len(group.members) != len(set(group.members)) or set(group.members) - instance_ids:
            raise IllustrationError("scene_schema_invalid", target=group.group_id)
    geometry_by_instance = {}
    from app.diagrams.semantics import asset_card
    for node in scene.asset_instances:
        if node.asset_id in RECIPES:
            geometry_by_instance.update({node.instance_id+":"+role: asset_card(aid)["nominal_geometry"]
                                         for role, aid, *_ in RECIPES[node.asset_id].children})
        else:
            geometry_by_instance[node.instance_id] = next(card["nominal_geometry"] for card in bundle.assets
                                                        if card["asset_id"] == node.asset_id)
    for relation in scene.relations:
        if relation.type in {"inside", "immersed_in"} and (relation.start.port or relation.end.port):
            raise IllustrationError("scene_schema_invalid", target=relation.relation_id, details={
                "rule": "Containment and immersion endpoints must name registered subject/container regions, not ports. Use the relevant internal region rather than the whole body when the public condition names only a part.",
                "allowed_regions": {target.instance: list(geometry_by_instance.get(target.instance, {}).get("regions", {}))
                                    for target in (relation.start, relation.end)}})
    for target in [end for relation in scene.relations for end in (relation.start, relation.end)] + [
            annotation.target for annotation in scene.annotations]:
        geometry = geometry_by_instance.get(target.instance)
        if geometry and ((target.port and target.port not in geometry.get("ports", {}))
                         or (target.region and target.region not in geometry.get("regions", {}))):
            raise IllustrationError("scene_schema_invalid", target=target.instance, details={
                "rule": "Choose an actual registered port/region, or omit an optional annotation target.",
                "allowed_regions": list(geometry.get("regions", {})), "allowed_ports": list(geometry.get("ports", {}))})
    if any(annotation.target.instance not in instance_ids for annotation in scene.annotations):
        raise IllustrationError("scene_schema_invalid")
    # Contextual leakage is reviewed visually. Local rules reject explicit
    # answer announcements, prohibited additions, and hidden numeric labels.
    import re
    text = "\n".join(labels + [scene.alt, scene.caption])
    if re.search(r"答案|正确选项|解题步骤|correct answer|answer\s*[:=]", text, re.I):
        raise IllustrationError("joint_review_failed")
    for forbidden in contract.prohibited_additions:
        # Accessibility prose can correctly describe absence ("无磁场线").
        # Actual visible labels are hard checked here; the independent joint
        # review interprets alt/caption and checks prohibited drawn geometry.
        if any(forbidden in label for label in labels):
            raise IllustrationError("joint_review_failed")
    for fact in contract.facts:
        if fact.display_policy in {"depict_only", "hidden"} and isinstance(fact.value, (int, float)):
            number = f"{fact.value:g}"
            if any(re.search(rf"(?<![\d.]){re.escape(number)}\s*{re.escape(fact.unit)}(?![\d.])", label)
                   for label in labels + [scene.alt, scene.caption]) and not any(
                   number in mark for mark in contract.required_marks):
                raise IllustrationError("joint_review_failed")


def compatible_parameter_fact(fact, spec, key, *, to_scale=False):
    from app.diagrams.interface import compatible_fact, parameter_contract
    if "fact_types" not in spec:
        spec = parameter_contract({key: spec})[key]
    return compatible_fact(fact, spec, key, to_scale=to_scale)


async def compose(llm, contract, brief, bundle, *, feedback=None):
    from app.diagrams.guidance import bundle_view
    from .preview import material_sources
    binding_choices = {card["asset_id"]: {key: [fact.id for fact in contract.facts
        if compatible_parameter_fact(fact, spec, key, to_scale=contract.presentation_constraints.to_scale)]
        for key, spec in card["parameters"].items()} for card in bundle.assets}
    schema = SceneDraftV2.model_json_schema()
    # Offer the real registered targets in the output grammar. This prevents
    # habitual names from producing a scene that cannot address its own art.
    from app.diagrams.semantics import asset_card
    target_regions, target_ports = {""}, {""}
    for card in bundle.assets:
        geometry = card.get("nominal_geometry", {})
        target_regions.update(geometry.get("regions", {}))
        target_ports.update(geometry.get("ports", {}))
        for child in card.get("children", []):
            geometry = asset_card(child["asset_id"])["nominal_geometry"]
            for field, values in (("regions", target_regions), ("ports", target_ports)):
                names = geometry.get(field, {})
                values.update(names)
                values.update(child["child_id"]+":"+name for name in names)
    schema["$defs"]["Target"]["properties"]["region"] = {"enum": sorted(target_regions)}
    schema["$defs"]["Target"]["properties"]["port"] = {"enum": sorted(target_ports)}
    # Dimensions are determined by the profile. The source material's viewBox
    # is not the composed canvas, so do not offer editable size fields.
    for field in ("width", "height"):
        schema["$defs"]["Canvas"]["properties"].pop(field)
    schema["$defs"]["AssetInstance"]["allOf"] = [{
        "if": {"properties": {"asset_id": {"const": card["asset_id"]}}},
        "then": {"required": ["entity_map"] if card.get("children") else ["entity_id"], "properties": {
            "version": {"const": card["version"]},
            "entity_map": {"type": "object", "additionalProperties": False,
                "required": [child["child_id"] for child in card.get("children", [])],
                "properties": {child["child_id"]: {"enum": list(dict.fromkeys(entity
                    for need in brief.needs if bundle.allowed(need.need_id, card["asset_id"], card["version"])
                    for entity in need.entity_ids))} for child in card.get("children", [])}},
            "params": {"type": "object", "additionalProperties": False,
                "properties": {key: {} for key in card["parameters"]}},
            "fact_bindings": {"type": "object", "additionalProperties": False,
                "properties": {key: {"enum": refs} for key, refs in binding_choices[card["asset_id"]].items() if refs}},
            "non_quantitative": {"type": "array", "items": {"enum": qualitative}}
                if (qualitative := [key for key, spec in card["parameters"].items()
                    if spec.get("non_quantitative_allowed")]) else {"type": "array", "maxItems": 0}}}}
        for card in bundle.assets]
    payload = {"visual_contract": contract.composer_view(), "visual_brief": brief.model_dump(mode="json"),
        "candidate_bundle": bundle_view(bundle, "compose"), "scene_schema": compact_schema(schema),
        "parameter_fact_choices": binding_choices,
        "material_svg_sources": material_sources(bundle),
        "request_schema": MaterialRequest.model_json_schema(), "repair_feedback": feedback}
    content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get("quiz_illustration_composer", "2.22.0").text},
        {"role": "user", "content": content[0]["text"]}],
        temperature=.2, max_tokens=5500, disable_thinking=True)
    data = structured(raw)
    try:
        if data.get("action") == "request_materials":
            return MaterialRequest.model_validate(data)
        if data.get("action") == "cannot_complete":
            return CannotComplete.model_validate(data)
        # A missing placement point is a presentation choice, not missing
        # scientific information. Use ordinary external placement instead of
        # inventing a point or spending a protocol correction on typography.
        for annotation in data.get("annotations", []):
            if (isinstance(annotation, dict) and annotation.get("placement") == "near_point"
                    and isinstance(annotation.get("target"), dict)
                    and not annotation["target"].get("port")
                    and annotation["target"].get("local_point") is None):
                annotation["placement"] = "outside_right"
        scene = SceneDraftV2.model_validate(data)
    except ValueError as exc:
        raise IllustrationError("scene_schema_invalid", details={"invalid_response": data,
            **validation_details(exc)}) from exc
    try:
        validate_scene(scene, contract, brief, bundle)
    except IllustrationError as exc:
        exc.details = {**exc.details, "invalid_response": scene.model_dump(mode="json")}
        raise
    return scene


def apply_patch(scene: SceneDraftV2, patch: ScenePatchV2, *, contract, brief, bundle):
    if digest(scene.model_dump(mode="json")) != patch.base_scene_hash:
        raise IllustrationError("patch_conflict")
    value = scene.model_dump(mode="json")
    instances = {row["instance_id"]: row for row in value["asset_instances"]}
    annotations = {row["annotation_id"]: row for row in value["annotations"]}
    relations = {row["relation_id"]: row for row in value["relations"]}
    for operation in patch.operations:
        try:
            if operation.op == "move_instance":
                if operation.x is None or operation.y is None:
                    raise ValueError()
                instances[operation.instance_id].update(x=operation.x, y=operation.y)
            elif operation.op in {"scale_instance", "rotate_instance"}:
                key = "scale" if operation.op == "scale_instance" else "rotation"
                if getattr(operation, key) is None:
                    raise ValueError()
                instances[operation.instance_id][key] = getattr(operation, key)
            elif operation.op == "set_param":
                row = instances[operation.instance_id]
                from app.diagrams.semantics import parameter_semantics
                spec = parameter_semantics(row["asset_id"], row["version"]).get(operation.key, {})
                if not operation.fact_id and spec.get("non_quantitative_allowed") and not contract.presentation_constraints.to_scale and operation.key not in row["fact_bindings"]:
                    if operation.key not in row["non_quantitative"]:
                        row["non_quantitative"].append(operation.key)
                    if operation.value is not None:
                        row["params"][operation.key] = operation.value
                    continue
                # Restore an omitted existing binding, never replace a bound
                # fact or authorize a new scientific condition.
                previous = row["fact_bindings"].get(operation.key)
                fact = next((fact for fact in contract.facts if fact.id == operation.fact_id), None)
                need = next(need for need in brief.needs if need.need_id == row["need_id"])
                from app.diagrams.semantics import RECIPES
                recipe = RECIPES.get(row["asset_id"])
                owner = row["entity_map"].get(recipe.bindings.get(operation.key, ("", ""))[0], "") if recipe else row["entity_id"]
                if previous and previous != operation.fact_id or not fact or fact.entity_id != owner or (
                        fact.id not in need.fact_bindings) or not compatible_parameter_fact(
                            fact, spec, operation.key, to_scale=contract.presentation_constraints.to_scale):
                    raise IllustrationError("parameter_unbound")
                if operation.value is not None:
                    if fact is None or operation.value != fact.value:
                        raise IllustrationError("parameter_unbound")
                row["fact_bindings"][operation.key] = operation.fact_id
                row["params"].pop(operation.key, None)
            elif operation.op == "replace_asset":
                row = instances[operation.instance_id]
                if not bundle.allowed(row["need_id"], operation.asset_id, operation.version):
                    raise IllustrationError("scene_asset_not_authorized")
                row.update(asset_id=operation.asset_id, version=operation.version)
            elif operation.op == "move_annotation":
                row = annotations[operation.annotation_id]
                if operation.placement is not None:
                    row["placement"] = operation.placement
                if operation.target is not None:
                    if operation.target.instance != row["target"]["instance"]:
                        raise IllustrationError("scene_asset_not_authorized")
                    row["target"] = operation.target.model_dump(mode="json")
                if operation.leader is not None:
                    row["leader"] = operation.leader
            elif operation.op == "replace_relation_route":
                relations[operation.relation_id]["route"] = operation.route
        except IllustrationError:
            raise
        except (KeyError, ValueError) as exc:
            raise IllustrationError("scene_schema_invalid") from exc
    try:
        updated = SceneDraftV2.model_validate(value)
    except ValueError as exc:
        raise IllustrationError("scene_schema_invalid", details=validation_details(exc)) from exc
    validate_scene(updated, contract, brief, bundle)
    return updated


async def repair(llm, scene, issues, *, contract, brief, bundle, png_message=None, feedback=None):
    from app.diagrams.guidance import bundle_view
    from .preview import material_sources
    selected = {node.asset_id for node in scene.asset_instances}
    from types import SimpleNamespace
    content = [{"type": "text", "text": json.dumps({"scene": scene.model_dump(mode="json"),
        "base_scene_hash": digest(scene.model_dump(mode="json")), "issues": issues,
        "contract": contract.composer_view(), "candidate_bundle": bundle_view(bundle, "compose"),
        "repair_feedback": feedback,
        "parameter_fact_choices": {node.instance_id: {key: [fact.id for fact in contract.facts
            if fact.id in next(need for need in brief.needs if need.need_id == node.need_id).fact_bindings
            and compatible_parameter_fact(fact, spec, key, to_scale=contract.presentation_constraints.to_scale)]
            for key, spec in card["parameters"].items()}
            for node in scene.asset_instances for card in bundle.assets if card["asset_id"] == node.asset_id},
        "material_svg_sources": material_sources(SimpleNamespace(assets=[
            card for card in bundle.assets if card["asset_id"] in selected])),
        "patch_schema": compact_schema(ScenePatchV2.model_json_schema())}, ensure_ascii=False)}]
    if png_message:
        content.append(png_message)
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get("quiz_illustration_patch", "3.0.0").text},
        {"role": "user", "content": content if png_message else content[0]["text"]}],
        temperature=.1, max_tokens=1800, disable_thinking=True)
    try:
        patch = ScenePatchV2.model_validate(structured(raw))
    except ValueError as exc:
        raise IllustrationError("scene_schema_invalid", details=validation_details(exc)) from exc
    return apply_patch(scene, patch, contract=contract, brief=brief, bundle=bundle), patch

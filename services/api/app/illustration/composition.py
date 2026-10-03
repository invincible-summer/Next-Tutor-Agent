"""Closed composition/patch protocol; no SVG and no editable question fields."""
from __future__ import annotations

import json

from app.diagrams.catalog import digest
from app.prompts.registry import get

from .contracts import (CandidateBundleV2, CannotComplete, IllustrationError,
    MaterialRequest, QuestionMaterialContract, SceneDraftV2, ScenePatchV2, VisualBriefV2)
from .requirements import allowed_label, structured


def validate_scene(scene: SceneDraftV2, contract: QuestionMaterialContract,
                   brief: VisualBriefV2, bundle: CandidateBundleV2):
    needs = {n.need_id: n for n in brief.needs}
    counts = {n.need_id: 0 for n in brief.needs}
    facts = {f.id: f for f in contract.facts}
    entities = {e.id for e in contract.entities}
    for instance in scene.asset_instances:
        if not bundle.allowed(instance.need_id, instance.asset_id, instance.version):
            raise IllustrationError("scene_asset_not_authorized", target=instance.instance_id)
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
        # Resolve only explicitly named, unique parameter facts on the same
        # authorized entity. This preserves hidden numeric bindings without
        # guessing from IDs, preview defaults, answer text or unit alone.
        from app.diagrams.semantics import parameter_semantics
        recipe = RECIPES.get(instance.asset_id)
        for key, spec in parameter_semantics(instance.asset_id).items():
            if key in instance.fact_bindings or not spec.get("condition_bearing"):
                continue
            owner = instance.entity_map.get(recipe.bindings[key][0], "") if recipe else instance.entity_id
            matches = [f.id for f in contract.facts if owner and f.entity_id == owner and f.predicate == key
                and f.display_policy != "hidden" and f.id in needs[instance.need_id].fact_bindings
                and (not spec.get("unit") or f.unit == spec["unit"])]
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
    for node in scene.asset_instances:
        if node.asset_id in RECIPES:
            instance_ids |= {node.instance_id+":"+role for role, *_ in RECIPES[node.asset_id].children}
            # A recipe has no physical parent port. Resolve a shorthand only
            # if its registered child geometry exposes exactly one such port.
            from app.diagrams.semantics import asset_card
            for target in [end for relation in scene.relations for end in (relation.start, relation.end)] + [
                    annotation.target for annotation in scene.annotations]:
                if target.instance != node.instance_id or not target.port:
                    continue
                children = [role for role, aid, *_ in RECIPES[node.asset_id].children
                    if target.port in asset_card(aid)["nominal_geometry"].get("ports", {})]
                if len(children) == 1:
                    target.instance = node.instance_id+":"+children[0]
    for group in scene.groups:
        if len(group.members) != len(set(group.members)) or set(group.members) - instance_ids:
            raise IllustrationError("scene_schema_invalid", target=group.group_id)
    if any(annotation.target.instance not in instance_ids for annotation in scene.annotations):
        raise IllustrationError("scene_schema_invalid")
    # Contextual leakage is reviewed visually. Local rules reject explicit
    # answer announcements, prohibited additions, and hidden numeric labels.
    import re
    text = "\n".join(labels + [scene.alt, scene.caption])
    if re.search(r"答案|正确选项|解题步骤|correct answer|answer\s*[:=]", text, re.I):
        raise IllustrationError("joint_review_failed")
    for forbidden in contract.prohibited_additions:
        if forbidden in text:
            raise IllustrationError("joint_review_failed")
    for fact in contract.facts:
        if fact.display_policy in {"depict_only", "hidden"} and isinstance(fact.value, (int, float)):
            number = f"{fact.value:g}"
            if any(re.search(rf"(?<![\d.]){re.escape(number)}\s*{re.escape(fact.unit)}(?![\d.])", label)
                   for label in labels + [scene.alt, scene.caption]) and not any(
                   number in mark for mark in contract.required_marks):
                raise IllustrationError("joint_review_failed")


async def compose(llm, contract, brief, bundle, *, thumbnails=None):
    from app.diagrams.guidance import bundle_view
    def compatible(fact, spec, key):
        if key == "fill" and fact.type == "state" and fact.predicate == "liquid_present" and (
                fact.value is True and fact.display_policy != "hidden" and
                not contract.presentation_constraints.to_scale):
            return True
        if fact.display_policy == "hidden" or spec.get("unit") and fact.unit != spec["unit"]:
            return False
        if fact.predicate and fact.predicate != key:
            return False
        kind = spec.get("type")
        return (kind == "boolean" and fact.type == "state" and isinstance(fact.value, bool) or
            kind in {"number", "integer"} and fact.type == "scalar" and not isinstance(fact.value, bool) or
            kind in {"string", "enum"} and fact.type in {"function", "label"} or
            kind in {"array", "list"} and fact.type in {"data", "range", "label"} and isinstance(fact.value, list))
    binding_choices = {card["asset_id"]: {key: [fact.id for fact in contract.facts
        if compatible(fact, spec, key)]
        for key, spec in card["parameters"].items()} for card in bundle.assets}
    payload = {"visual_contract": contract.composer_view(), "visual_brief": brief.model_dump(mode="json"),
        "candidate_bundle": bundle_view(bundle, "compose"), "scene_schema": SceneDraftV2.model_json_schema(),
        "parameter_fact_choices": binding_choices,
        "request_schema": MaterialRequest.model_json_schema()}
    content = [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    content.extend(thumbnails or [])
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get("quiz_illustration_composer", "2.1.0").text},
        {"role": "user", "content": content if thumbnails else content[0]["text"]}],
        temperature=.2, max_tokens=5500, disable_thinking=True)
    data = structured(raw)
    try:
        if data.get("action") == "request_materials":
            return MaterialRequest.model_validate(data)
        if data.get("action") == "cannot_complete":
            return CannotComplete.model_validate(data)
        scene = SceneDraftV2.model_validate(data)
    except ValueError as exc:
        raise IllustrationError("scene_schema_invalid") from exc
    validate_scene(scene, contract, brief, bundle)
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
                spec = parameter_semantics(row["asset_id"]).get(operation.key, {})
                if not operation.fact_id and spec.get("non_quantitative_allowed") and not contract.presentation_constraints.to_scale and operation.key not in row["fact_bindings"]:
                    if operation.key not in row["non_quantitative"]:
                        row["non_quantitative"].append(operation.key)
                    continue
                # A patch can rebind to the same frozen fact, never change it.
                if row["fact_bindings"].get(operation.key) != operation.fact_id:
                    raise IllustrationError("parameter_unbound")
                row["params"].pop(operation.key, None)
            elif operation.op == "replace_asset":
                row = instances[operation.instance_id]
                if not bundle.allowed(row["need_id"], operation.asset_id, operation.version):
                    raise IllustrationError("scene_asset_not_authorized")
                row.update(asset_id=operation.asset_id, version=operation.version)
            elif operation.op == "move_annotation":
                annotations[operation.annotation_id]["placement"] = operation.placement
            elif operation.op == "replace_relation_route":
                relations[operation.relation_id]["route"] = operation.route
        except (KeyError, ValueError) as exc:
            raise IllustrationError("scene_schema_invalid") from exc
    try:
        updated = SceneDraftV2.model_validate(value)
    except ValueError as exc:
        raise IllustrationError("scene_schema_invalid") from exc
    validate_scene(updated, contract, brief, bundle)
    return updated


async def repair(llm, scene, issues, *, contract, brief, bundle, png_message=None):
    from app.diagrams.guidance import bundle_view
    content = [{"type": "text", "text": json.dumps({"scene": scene.model_dump(mode="json"),
        "base_scene_hash": digest(scene.model_dump(mode="json")), "issues": issues,
        "contract": contract.composer_view(), "candidate_bundle": bundle_view(bundle, "compose"),
        "patch_schema": ScenePatchV2.model_json_schema()}, ensure_ascii=False)}]
    if png_message:
        content.append(png_message)
    raw, _ = await llm.complete(messages=[
        {"role": "system", "content": get("quiz_illustration_composer", "2.1.0").text +
         "\n这是局部修订轮，只返回 ScenePatchV2；禁止改变题目事实。"
         "仅当参数schema允许non_quantitative时，set_param的fact_id=''可以声明定性默认。禁止用此方式处理必须绑定的真实条件。"},
        {"role": "user", "content": content if png_message else content[0]["text"]}],
        temperature=.1, max_tokens=1800, disable_thinking=True)
    try:
        patch = ScenePatchV2.model_validate(structured(raw))
    except ValueError as exc:
        raise IllustrationError("scene_schema_invalid") from exc
    return apply_patch(scene, patch, contract=contract, brief=brief, bundle=bundle), patch

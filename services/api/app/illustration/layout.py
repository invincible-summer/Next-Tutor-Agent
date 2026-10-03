"""Instance geometry, conservative relation solving, layers, measured text/routes."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import SimpleNamespace

from app.core.quiz_illustration import QuestionIllustration, _hash, normalize_svg
from app.diagrams.catalog import catalog, digest
from app.diagrams.drawing import Drawing, num
from app.diagrams.semantics import (RECIPES, METADATA_VERSION, V2_RENDERER_VERSION,
    InstanceGeometry, instantiate_asset, parameter_semantics)
from app.diagrams.schema import DiagramError

from .composition import validate_scene
from .contracts import DiagramSourceV2, IllustrationError, LayoutReport, SceneDraftV2, SceneRelation, Target
from .preview import measure
from .requirements import allowed_label

LAYERS = ["background", "support", "body", "content", "connection", "measurement_marks",
          "geometry_marks", "labels", "emphasis"]


@dataclass
class Placed:
    id: str
    entity: str
    geometry: InstanceGeometry
    x: float
    y: float
    scale: float
    rotation: float
    layer: str
    recipe: str = ""
    local_bounds: list = field(default_factory=list)

    def point(self, xy):
        cx, cy = self.geometry.drawing.width/2, self.geometry.drawing.height/2
        a = math.radians(self.rotation)
        dx, dy = xy[0]-cx, xy[1]-cy
        return [self.x+self.scale*(cx+dx*math.cos(a)-dy*math.sin(a)),
                self.y+self.scale*(cy+dx*math.sin(a)+dy*math.cos(a))]

    def box(self, box=None):
        x, y, w, h = self.local_bounds if box is None else box
        points = [self.point(p) for p in [(x, y), (x+w, y), (x, y+h), (x+w, y+h)]]
        left, top = min(p[0] for p in points), min(p[1] for p in points)
        return [left, top, max(p[0] for p in points)-left, max(p[1] for p in points)-top]

    def port(self, name):
        if name not in self.geometry.ports:
            raise IllustrationError("relation_unrealizable", target=self.id)
        return self.point(self.geometry.ports[name]["point"])

    def region(self, name):
        if name not in self.geometry.regions:
            raise IllustrationError("relation_unrealizable", target=self.id)
        bounds = self.geometry.regions[name]["bounds"]
        if name == "body" and bounds == [0, 0, self.geometry.drawing.width, self.geometry.drawing.height]:
            # The catalogue adapter's full canvas denotes the whole figure.
            # Position its labels against measured paint, not blank margins.
            return self.box()
        return self.box(bounds)


def overlaps(a, b, margin=0):
    return min(a[0]+a[2], b[0]+b[2])-max(a[0], b[0]) > margin and min(
        a[1]+a[3], b[1]+b[3])-max(a[1], b[1]) > margin


def contains(outer, inner, margin=0):
    return outer[0]+margin <= inner[0] and outer[1]+margin <= inner[1] and (
        inner[0]+inner[2] <= outer[0]+outer[2]-margin and inner[1]+inner[3] <= outer[1]+outer[3]-margin)


def _parameters(instance, contract):
    schema = parameter_semantics(instance.asset_id)
    if (set(instance.params) | set(instance.fact_bindings) | set(instance.non_quantitative)) - set(schema):
        raise IllustrationError("parameter_unbound", target=instance.instance_id)
    facts = {f.id: f for f in contract.facts}
    params, bindings = dict(instance.params), {}
    for key, spec in schema.items():
        ref = instance.fact_bindings.get(key)
        if ref:
            fact = facts[ref]
            if fact.entity_id and instance.entity_id and fact.entity_id != instance.entity_id:
                raise IllustrationError("parameter_unbound", target=instance.instance_id)
            unit = spec.get("unit", "")
            qualitative_liquid = key == "fill" and fact.type == "state" and fact.value is True and (
                fact.predicate == "liquid_present") and not contract.presentation_constraints.to_scale
            if qualitative_liquid:
                value = params.get(key, spec.get("default") or .5)
                if not isinstance(value, (int, float)) or not 0 < value <= 1 or fact.unit:
                    raise IllustrationError("parameter_unbound", target=instance.instance_id)
                params[key] = value
                bindings[key] = {**fact.model_dump(mode="json"), "non_quantitative": True,
                                 "rendered_height_fraction": value}
                continue
            if unit and fact.unit != unit:
                raise IllustrationError("parameter_unbound", target=instance.instance_id)
            if key in {"values", "points", "x_range", "y_range", "interval"} and fact.type not in {"data", "range"}:
                raise IllustrationError("parameter_unbound", target=instance.instance_id)
            if key == "labels" and (fact.type not in {"data", "label"} or not isinstance(fact.value, list)):
                raise IllustrationError("parameter_unbound", target=instance.instance_id)
            if key == "function" and fact.type != "function":
                raise IllustrationError("parameter_unbound", target=instance.instance_id)
            if key in params and params[key] != fact.value:
                raise IllustrationError("parameter_unbound", target=instance.instance_id)
            params[key] = fact.value
            bindings[key] = fact.model_dump(mode="json")
        elif spec.get("condition_bearing"):
            if key not in params and "default" not in spec and not spec.get("required"):
                # Optional data is absent, not a model supplied condition.
                # The registered renderer derives e.g. sequential edges from
                # the bound ordered item list, never gallery sample values.
                continue
            # False appearance toggles add no marks; calibrated scales derive
            # from bound readings/ranges rather than a model invented value.
            value = params.get(key, spec.get("default"))
            # Library captions are optional presentation, never new question
            # facts. Omit unrequested default labels across every renderer.
            if key in {"show_labels", "show_poles"} and key not in params:
                value = False
                params[key] = False
            safe_off = key in {"show_labels", "show_poles", "show_values", "show_angle", "scale_labels", "show_scale"} and value is False
            calibrated = key in {"scale_labels", "show_scale"} and value is True and (
                {"reading", "maximum"} <= set(instance.fact_bindings) or
                instance.asset_id in {"apparatus.thermometer", "recipe.thermal"} and "reading" in instance.fact_bindings or
                instance.asset_id == "vessel.cylinder" and "capacity" in instance.fact_bindings)
            no_auxiliary = key == "construction" and value == "none"
            calibrated_axis = instance.asset_id.startswith("function.") and {
                "function", "x_range", "y_range"} <= set(instance.fact_bindings) and (
                key == "show_ticks" and value is True or
                key == "x_label" and value == "x" or key == "y_label" and value == "y")
            if safe_off or calibrated or no_auxiliary or calibrated_axis:
                continue
            if key not in instance.non_quantitative or not spec.get("non_quantitative_allowed"):
                raise IllustrationError("missing_fact_binding", target=instance.instance_id+":"+key,
                    repairable=bool(spec.get("non_quantitative_allowed") and not contract.presentation_constraints.to_scale))
            if contract.presentation_constraints.to_scale or key in {"values", "points", "function"}:
                raise IllustrationError("missing_fact_binding", target=instance.instance_id)
            bindings[key] = {"non_quantitative": True, "value": value}
            if key not in params:
                params[key] = value
    return params, bindings


def instantiate_scene(scene, contract):
    placed, bindings, internal = {}, {}, []
    mono = contract.presentation_constraints.style == "monochrome_line"
    for node in scene.asset_instances:
        params, bound = _parameters(node, contract)
        bindings[node.instance_id] = bound
        if node.asset_id in RECIPES:
            if node.rotation:
                raise IllustrationError("relation_unrealizable", target=node.instance_id)
            recipe = RECIPES[node.asset_id]
            roles = {row[0] for row in recipe.children}
            if set(node.entity_map) - roles:
                raise IllustrationError("scene_asset_not_authorized", target=node.instance_id)
            for role, asset_id, x, y, scale in recipe.children:
                child_params = {key: params[parent] for parent, (child, key) in recipe.bindings.items()
                                if child == role and parent in params}
                child_refs = {key: node.fact_bindings[parent] for parent, (child, key) in recipe.bindings.items()
                              if child == role and parent in node.fact_bindings}
                child_qualitative = [key for parent, (child, key) in recipe.bindings.items()
                                     if child == role and parent in node.non_quantitative]
                if role in recipe.part_selectors:
                    child_params = {"radius": params.get("radius", 54)}
                    child_qualitative = ["radius"] if "radius" in node.non_quantitative else []
                entity = node.entity_map.get(role, node.entity_id if len(roles) == 1 else "")
                child_id = node.instance_id + ":" + role
                child_params, child_bound = _parameters(SimpleNamespace(asset_id=asset_id,
                    params=child_params, fact_bindings=child_refs, non_quantitative=child_qualitative,
                    entity_id=entity, instance_id=child_id), contract)
                bindings[child_id] = child_bound
                geometry = instantiate_asset(asset_id, catalog()[1][asset_id].version, child_params, monochrome=mono)
                if role in recipe.part_selectors:
                    geometry.drawing.parts = [geometry.drawing.parts[recipe.part_selectors[role]]]
                    geometry.parts = {"body": geometry.drawing.parts}
                    geometry.derived_facts["construction_part"] = role
                    geometry.derived_facts["construction_radius"] = geometry.params["radius"]
                placed[child_id] = Placed(child_id, entity,
                    geometry, node.x+x*node.scale, node.y+y*node.scale, node.scale*scale, 0,
                    "support" if "plane" in asset_id else node.layer, recipe=node.instance_id)
            for index, (kind, start, ap, end, bp, medium) in enumerate(recipe.relations):
                region = kind in {"inside", "immersed_in"}
                internal.append(SceneRelation(relation_id=f"{node.instance_id}:r{index}", type=kind,
                    start=Target(instance=node.instance_id+":"+start, **{"region" if region else "port": ap}),
                    end=Target(instance=node.instance_id+":"+end, **{"region" if region else "port": bp}),
                    medium=medium, route="orthogonal" if kind == "series" else "straight"))
        else:
            geometry = instantiate_asset(node.asset_id, node.version, params, monochrome=mono)
            if node.asset_id.startswith("material."):
                # Multiple uses of the same immutable SVG must not share IDs
                # or accidentally resolve each other's local arrow markers.
                from hashlib import sha256
                prefix = "m" + sha256(node.instance_id.encode()).hexdigest()[:10]
                mapping = {part.get("id"): prefix+"_"+str(index) for index, part in enumerate(
                    part for root in geometry.drawing.parts for part in root.iter()) if part.get("id")}
                for root in geometry.drawing.parts:
                    for part in root.iter():
                        if part.get("id"):
                            part.set("id", mapping[part.get("id")])
                        for key in ("marker-start", "marker-mid", "marker-end"):
                            if part.get(key):
                                for old, new in mapping.items():
                                    part.set(key, part.get(key).replace(f"#{old})", f"#{new})"))
            if node.rotation and geometry.domain in {"measurement", "chart", "chemistry"}:
                raise IllustrationError("relation_unrealizable", target=node.instance_id)
            placed[node.instance_id] = Placed(node.instance_id, node.entity_id, geometry,
                node.x, node.y, node.scale, node.rotation, node.layer)
    return placed, bindings, internal


def segment_intersects(a, b, box):
    # Liang-Barsky clipping, excluding a boundary contact.
    x, y, w, h = box
    lo, hi = 0.0, 1.0
    dx, dy = b[0]-a[0], b[1]-a[1]
    for p, q in [(-dx, a[0]-x), (dx, x+w-a[0]), (-dy, a[1]-y), (dy, y+h-a[1])]:
        if abs(p) < 1e-8:
            if q <= 0:
                return False
        elif p < 0:
            lo = max(lo, q/p)
        else:
            hi = min(hi, q/p)
    return lo < hi and hi > 1e-5 and lo < 1-1e-5


def route(start, end, obstacles, kind):
    candidates = [[start, end]] if kind == "straight" else []
    middle = (start[0]+end[0])/2
    candidates += [[start, [middle, start[1]], [middle, end[1]], end],
        [start, [start[0], end[1]], end], [start, [end[0], start[1]], end]]
    for box in obstacles:
        x, y, w, h = box
        for y0 in [y-14, y+h+14]:
            candidates.append([start, [start[0], y0], [end[0], y0], end])
        for x0 in [x-14, x+w+14]:
            candidates.append([start, [x0, start[1]], [x0, end[1]], end])
    for points in candidates:
        points = [p for i, p in enumerate(points) if i == 0 or math.dist(p, points[i-1]) > .01]
        if len(points) > 1 and all(not segment_intersects(a, b, box)
                for a, b in zip(points, points[1:]) for box in obstacles):
            return points
    raise IllustrationError("relation_unrealizable", repairable=True)


@dataclass
class CompiledIllustration:
    illustration: QuestionIllustration
    source: DiagramSourceV2
    facts: list[dict]


def compile_scene(scene: SceneDraftV2, *, contract, brief, bundle) -> CompiledIllustration:
    validate_scene(scene, contract, brief, bundle)
    try:
        placed, bindings, internal = instantiate_scene(scene, contract)
    except DiagramError as exc:
        raise IllustrationError("parameter_unbound") from exc
    texts = [{"text": a.text, "size": 18} for a in scene.annotations]
    metrics = measure([p.geometry.drawing.svg() for p in placed.values()], texts)
    for p, box in zip(placed.values(), metrics["bounds"]):
        p.local_bounds = box
    report = LayoutReport()
    # A recipe is one rigid assembly. Uniformly fit its whole measured hull
    # into the selected canvas before solving contacts; never move parts or
    # annotations independently, and retain the correction in provenance.
    for node in scene.asset_instances:
        members = [p for p in placed.values() if p.recipe == node.instance_id]
        card = next(row for row in bundle.assets if row["asset_id"] == node.asset_id)
        if not members and (card["kind"] == "construction" or len(scene.asset_instances) == 1) and not contract.presentation_constraints.to_scale:
            members = [placed[node.instance_id]]
        if not members:
            continue
        boxes = [p.box() for p in members]
        if len(scene.asset_instances) == 1 and not contract.presentation_constraints.to_scale:
            left = min(box[0] for box in boxes)
            top = min(box[1] for box in boxes)
            width = max(box[0]+box[2] for box in boxes)-left
            height = max(box[1]+box[3] for box in boxes)-top
            factor = min(2 if card["kind"] == "construction" else 1,
                (scene.canvas.width-96)/width, (scene.canvas.height-96)/height)
            dx = (scene.canvas.width-width*factor)/2
            dy = (scene.canvas.height-height*factor)/2
            for member in members:
                member.x = dx+(member.x-left)*factor
                member.y = dy+(member.y-top)*factor
                member.scale *= factor
            report.adjustments.append({"instance": node.instance_id, "scale_factor": factor,
                "dx": dx-left, "dy": dy-top, "reason": "center_isolated_assembly"})
            continue
        right = max(box[0]+box[2] for box in boxes)-node.x
        bottom = max(box[1]+box[3] for box in boxes)-node.y
        factor = min(1, (scene.canvas.width-6-node.x)/right,
                        (scene.canvas.height-6-node.y)/bottom)
        if .5 <= factor < 1 and min(node.x, node.y) >= 4:
            for member in members:
                member.x = node.x+(member.x-node.x)*factor
                member.y = node.y+(member.y-node.y)*factor
                member.scale *= factor
            report.adjustments.append({"instance": node.instance_id,
                "scale_factor": factor, "reason": "fit_rigid_assembly"})
    relations = list(scene.relations)
    # Recipe relations are structural constraints, even if omitted by a model.
    def expressed(relation):
        return any(relation.type == other.type and (
            {relation.start.instance, relation.end.instance} == {other.start.instance, other.end.instance}
            if relation.type == "series" else relation.start == other.start and relation.end == other.end)
            for other in relations)
    relations += [relation for relation in internal if not expressed(relation)]
    from .validators import validate_relations, validate_material
    validate_relations(placed, relations, contract, report)
    validate_material(placed, bindings, scene, contract, report)
    w, h = scene.canvas.width, scene.canvas.height
    for p in placed.values():
        box = p.box()
        if not contains([4, 4, w-8, h-8], box):
            raise IllustrationError("geometry_out_of_bounds", target=p.id, repairable=True)
        if p.geometry.domain in {"measurement", "chart"} and p.scale*contract.presentation_constraints.target_width/w < .65:
            raise IllustrationError("text_not_legible", target=p.id, repairable=True)
        report.bounds[p.id] = box
        report.ports[p.id] = {key: p.port(key) for key in p.geometry.ports}
    allowed_overlaps = set()
    for instance in scene.asset_instances:
        if instance.asset_id in RECIPES:
            for a, b in RECIPES[instance.asset_id].allowed_overlaps:
                allowed_overlaps.add(frozenset([instance.instance_id+":"+a, instance.instance_id+":"+b]))
    for relation in relations:
        if relation.type in {"inside", "immersed_in", "supported_by"}:
            allowed_overlaps.add(frozenset([relation.start.instance, relation.end.instance]))
    for rule in scene.occlusion_rules:
        if rule.top not in placed or rule.bottom not in placed:
            raise IllustrationError("collision_unresolved")
        pair = frozenset([rule.top, rule.bottom])
        if pair not in allowed_overlaps:
            raise IllustrationError("collision_unresolved")
    values = list(placed.values())
    for i, a in enumerate(values):
        for b in values[i+1:]:
            if overlaps(a.box(), b.box(), margin=3) and frozenset([a.id, b.id]) not in allowed_overlaps:
                raise IllustrationError("collision_unresolved", target=a.id, repairable=True)
            for sensitive, other in [(a, b), (b, a)]:
                for name, region in sensitive.geometry.regions.items():
                    if frozenset([a.id, b.id]) in allowed_overlaps and other.geometry.asset_id in {
                            "vessel.beaker", "vessel.tall_beaker"}:
                        # A transparent container's bounding rectangle is not
                        # paint. Its opaque rims are checked in the other pass.
                        continue
                    paint = [other.region(key) for key, value in other.geometry.regions.items()
                             if value["occlusion"] == "paint"] or [other.box()]
                    if region["occlusion"] == "never_cover" and any(overlaps(sensitive.region(name), box, margin=1) for box in paint):
                        raise IllustrationError("collision_unresolved", target=sensitive.id, repairable=True)
    drawing = Drawing(w, h, contract.presentation_constraints.style == "monochrome_line")
    layer_parts = {name: [] for name in LAYERS}
    for p in values:
        cx, cy = p.geometry.drawing.width/2, p.geometry.drawing.height/2
        for part_name, parts in p.geometry.parts.items():
            local = Drawing(w, h, drawing.monochrome)
            group = local.element("g", transform=f"translate({num(p.x)} {num(p.y)}) scale({num(p.scale)}) rotate({num(p.rotation)} {num(cx)} {num(cy)})")
            group.extend(parts)
            layer = "background" if part_name == "background" else "measurement_marks" if part_name == "front" else "labels" if part_name == "labels" else p.layer
            layer_parts[layer].extend(local.parts)
    paths = []
    for relation in relations:
        if relation.type not in {"connected", "series", "suspended_from"}:
            continue
        a, b = placed[relation.start.instance], placed[relation.end.instance]
        start, end = a.port(relation.start.port), b.port(relation.end.port)
        if math.dist(start, end) < 1:
            continue
        containers = {r.end.instance for r in relations if r.type in {"immersed_in", "inside"} and r.start.instance in {a.id, b.id}}
        obstacles = [p.box() for p in values if p.id not in {a.id, b.id} | containers]
        # Endpoints are excluded as obstacles, their sensitive regions are not.
        for p in [a, b] + [placed[node] for node in containers]:
            obstacles.extend(p.region(key) for key, region in p.geometry.regions.items() if region["occlusion"] == "never_cover")
        points = route(start, end, obstacles, relation.route)
        if any(not 4 <= x <= w-4 or not 4 <= y <= h-4 for x, y in points):
            raise IllustrationError("relation_unrealizable", target=relation.relation_id, repairable=True)
        # Non-junction wire crossings can change a circuit's meaning.
        if relation.medium == "wire":
            for previous in paths:
                for a0, b0 in zip(points, points[1:]):
                    for c0, d0 in zip(previous, previous[1:]):
                        if _crosses(a0, b0, c0, d0):
                            raise IllustrationError("relation_unrealizable", target=relation.relation_id, repairable=True)
        paths.append(points)
        local = Drawing(w, h, drawing.monochrome)
        local.poly(points, width=3 if relation.medium in {"rope", "tube"} else 2)
        layer_parts[relation.layer].extend(local.parts)
    label_boxes = []
    for annotation, metric in zip(scene.annotations, metrics["textMetrics"]):
        p = placed.get(annotation.target.instance)
        if p is None:
            raise IllustrationError("relation_unrealizable", target=annotation.annotation_id)
        box = p.region(annotation.target.region) if annotation.target.region else p.box()
        x, y, bw, bh = box
        tw, th = metric["width"], max(18, metric["ascent"]+metric["descent"])
        positions = {"outside_right": (x+bw+12, y+bh/2-th/2), "outside_left": (x-tw-12, y+bh/2-th/2),
            "outside_top": (x+bw/2-tw/2, y-th-12), "outside_bottom": (x+bw/2-tw/2, y+bh+12),
            "outside_top_right": (x+bw+12, y-th-12), "outside_right_lower": (x+bw+12, y+bh-th)}
        if annotation.target.port:
            # A terminal label belongs to its actual transformed port, not
            # the centre of the whole instrument. Keep alternate placements
            # next to that same port, outside the measured painted bounds.
            px, py = p.port(annotation.target.port)
            positions = {"outside_left": (px-tw-12, py-th/2),
                "outside_right": (px+12, py-th/2),
                "outside_top": (px-tw/2, y-th-12),
                "outside_bottom": (px-tw/2, y+bh+12),
                "outside_top_right": (px+12, y-th-12),
                "outside_right_lower": (px+12, y+bh+12)}
        if annotation.text in p.geometry.intrinsic_marks:
            report.adjustments.append({"annotation": annotation.annotation_id,
                "instance": p.id, "reason": "reuse_intrinsic_mark"})
            continue
        choices = [annotation.placement] + [key for key in positions if key != annotation.placement]
        def clear_label(candidate):
            return contains([4, 4, w-8, h-8], candidate) and not any(
                overlaps(candidate, other, margin=-3) for other in label_boxes) and not any(
                overlaps(candidate, node.box(), margin=1) for node in values) and not any(
                segment_intersects(a, b, candidate) for points in paths for a, b in zip(points, points[1:]))
        selected = next((key for key in choices if clear_label([*positions[key], tw, th])), None)
        if selected is None:
            raise IllustrationError("collision_unresolved", target=annotation.annotation_id, repairable=True)
        lx, ly = positions[selected]
        lbox = [lx, ly, tw, th]
        if selected != annotation.placement:
            report.adjustments.append({"annotation": annotation.annotation_id,
                "placement": selected, "reason": "avoid_label_collision"})
        if 18*contract.presentation_constraints.target_width/w < 11:
            raise IllustrationError("text_not_legible", target=annotation.annotation_id)
        local = Drawing(w, h, drawing.monochrome)
        local.text(annotation.text, lx, ly+metric["ascent"], size=18, anchor="start")
        layer_parts["labels"].extend(local.parts)
        label_boxes.append(lbox)
        report.bounds[annotation.annotation_id] = lbox
    for layer in LAYERS:
        drawing.parts.extend(layer_parts[layer])
    normalized = normalize_svg(drawing.svg(), alt=scene.alt, caption=scene.caption, components=True)
    illustration = QuestionIllustration(kind="svg", schema_version=3, sanitizer_version=3,
        svg=normalized.svg, alt=scene.alt, caption=scene.caption, width=normalized.width,
        height=normalized.height, content_hash=_hash(normalized.svg, scene.alt, scene.caption, 3))
    report.domains = sorted({p.geometry.domain for p in values})
    report.remaining_slack = min(min(b[0], b[1], w-b[0]-b[2], h-b[1]-b[3]) for b in report.bounds.values())
    source = DiagramSourceV2(catalog_version=bundle.catalog_version, metadata_version=METADATA_VERSION,
        renderer_version=V2_RENDERER_VERSION, scene_hash=digest(scene.model_dump(mode="json")),
        asset_versions={p.geometry.asset_id: p.geometry.version for p in values} | {
            p.asset_id: p.version for p in scene.asset_instances if p.asset_id in RECIPES},
        scene=scene, fact_bindings=bindings, layout_report=report)
    from app.diagrams.guidance import for_asset
    source.guidance_versions = {asset_id: guide["version"] for asset_id, version in source.asset_versions.items()
        if (guide := for_asset(asset_id, version))["hints"]}
    return CompiledIllustration(illustration, source, [p.geometry.derived_facts for p in values])


def _crosses(a, b, c, d):
    def cross(p, q, r):
        return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    return cross(a, b, c)*cross(a, b, d) < -1e-4 and cross(c, d, a)*cross(c, d, b) < -1e-4

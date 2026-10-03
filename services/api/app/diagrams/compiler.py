"""Compile authorized component references into a frozen, portable safe SVG."""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.quiz_illustration import QuestionIllustration, _hash, normalize_svg

from .catalog import RENDERER_VERSION, catalog, digest
from .drawing import Drawing, NS, num
from .schema import DiagramError, DiagramSource, SceneSpec
from .legacy_layout import wire_networks, wire_route


@dataclass
class CompiledDiagram:
    illustration: QuestionIllustration
    source: DiagramSource
    facts: list[dict]


def compile_scene(raw, *, allowed_assets: set[str] | None = None) -> CompiledDiagram:
    try:
        scene = SceneSpec.model_validate(raw)
    except ValueError as exc:
        raise DiagramError("diagram_invalid_scene") from exc
    version, library = catalog()
    drawing = Drawing(scene.width, scene.height, scene.profile == "monochrome")
    annotations = Drawing(scene.width, scene.height, drawing.monochrome)
    layers = {key: [] for key in ("background", "body", "front", "labels")}
    def label(text, x, y, size=18, anchor="middle"):
        width = len(text)*size
        left = x-(width/2 if anchor == "middle" else width if anchor == "end" else 0)
        if left < 4 or left+width > scene.width-4 or not size+2 <= y <= scene.height-8:
            raise DiagramError("diagram_label_out_of_bounds")
        annotations.text(text, x, y, size=size, anchor=anchor)
    anchors, versions, facts, protected, bodies, port_kinds = {}, {}, [], [], {}, {}
    from .semantics import instantiate_asset
    for node in scene.nodes:
        if allowed_assets is not None and node.asset_id not in allowed_assets:
            raise DiagramError("diagram_asset_not_retrieved")
        asset = library.get(node.asset_id)
        if asset is None or asset.version != node.version:
            raise DiagramError("diagram_asset_version_missing")
        if asset.review.get("status") != "passed":
            raise DiagramError("diagram_asset_not_reviewed")
        if node.rotation and not asset.card()["rotation_allowed"]:
            raise DiagramError("diagram_rotation_not_allowed")
        try:
            geometry = instantiate_asset(node.asset_id, node.version, node.params, monochrome=drawing.monochrome)
            part = geometry.drawing
        except DiagramError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, OverflowError, ZeroDivisionError) as exc:
            raise DiagramError("diagram_invalid_parameter") from exc
        # Rotation is around the component's centre; positions refer to its
        # unrotated upper-left corner. Check all four transformed corners.
        angle = math.radians(node.rotation)
        cx, cy = part.width/2, part.height/2
        def point(x, y):
            rx, ry = x-cx, y-cy
            return (node.x + node.scale*(cx+rx*math.cos(angle)-ry*math.sin(angle)),
                    node.y + node.scale*(cy+rx*math.sin(angle)+ry*math.cos(angle)))
        for x, y in [(0, 0), (part.width, 0), (part.width, part.height), (0, part.height)]:
            px, py = point(x, y)
            if not 4 <= px <= scene.width-4 or not 4 <= py <= scene.height-4:
                raise DiagramError("diagram_component_out_of_bounds")
        transform = (f"translate({num(node.x)} {num(node.y)}) "
                     f"scale({num(node.scale)}) rotate({num(node.rotation)} {num(cx)} {num(cy)})")
        for name, parts in geometry.parts.items():
            group = drawing.element("g", transform=transform)
            drawing.parts.remove(group)
            group.extend(parts)
            layer = name if name in {"background", "front", "labels"} else "body"
            layers[layer].append((node.id, group))
        anchors[node.id] = {key: point(*xy) for key, xy in part.anchors.items()}
        anchors[node.id].update({key: point(*value["point"]) for key, value in geometry.ports.items()})
        if any(connection.kind == "wire" for connection in scene.connections):
            port_kinds[node.id] = {key: value["kind"] for key, value in geometry.ports.items()}
            for name, region in geometry.regions.items():
                if region.get("occlusion") not in {"never_cover", "forbidden"} or name != "body" and region.get("occlusion") != "never_cover":
                    continue
                x, y, w, h = region["bounds"]
                corners = [point(px, py) for px, py in ((x, y), (x+w, y), (x+w, y+h), (x, y+h))]
                left, top = min(p[0] for p in corners), min(p[1] for p in corners)
                bounds = [left, top, max(p[0] for p in corners)-left, max(p[1] for p in corners)-top]
                protected.append({"node": node.id, "region": name, "bounds": bounds})
                if name == "body":
                    bodies[node.id] = bounds
        if node.label:
            px, py = point(cx, part.height)
            if py+22 > scene.height-4:
                raise DiagramError("diagram_label_out_of_bounds")
            label(node.label, px, py+20, size=16)
        versions[asset.id] = asset.version
        facts.append({"node": node.id, "asset_id": asset.id, "parameters": asset.parameters(node.params),
                      "computed": part.facts})
    networks = wire_networks([row.model_dump(mode="json") for row in scene.connections])
    occupied = []
    for connection in scene.connections:
        if connection.kind == "wire" and any(port_kinds.get(endpoint.node, {}).get(endpoint.anchor) != "wire"
                for endpoint in (connection.start, connection.end)):
            raise DiagramError("diagram_anchor_missing")
        try:
            start = anchors[connection.start.node][connection.start.anchor]
            end = anchors[connection.end.node][connection.end.anchor]
        except KeyError as exc:
            raise DiagramError("diagram_anchor_missing") from exc
        if math.dist(start, end) < 1:
            raise DiagramError("diagram_empty_connection")
        color = drawing.blue if connection.kind == "tube" else drawing.ink
        width = 3 if connection.kind in {"rope", "tube"} else 2
        if connection.kind == "wire":
            points = wire_route(start, end, route=connection.route, blockers=protected,
                width=scene.width, height=scene.height,
                endpoint_bodies=[(connection.start.node, bodies[connection.start.node]),
                                 (connection.end.node, bodies[connection.end.node])],
                occupied=occupied, network=networks[(connection.start.node, connection.start.anchor)])
            occupied.append({"network": networks[(connection.start.node, connection.start.anchor)], "points": points})
            drawing.poly(points, color=color, width=width)
        elif connection.route == "orthogonal":
            middle = (start[0]+end[0])/2
            points = [start, (middle, start[1]), (middle, end[1]), end]
            drawing.poly(points, color=color, width=width)
            if connection.kind == "arrow":
                drawing.arrow(middle, end[1], *end, color=color)
        elif connection.kind == "arrow":
            drawing.arrow(*start, *end)
        else:
            drawing.line(*start, *end, color=color, width=width, dashed=connection.kind == "dashed")
        if connection.label:
            label(connection.label, (start[0]+end[0])/2, (start[1]+end[1])/2-10, size=15)
    for item in scene.labels:
        # Reserve room for the text at the selected anchor, rather than
        # permitting baseline coordinates right on the page border.
        label(item.text, item.x, item.y, anchor=item.anchor)
    # Actual material part layers keep container fills behind their contents.
    # A target is painted before its dependant, so its rim cannot erase the
    # foreground marks of a contained measuring part, regardless of node order.
    dependencies = {}
    for relation in scene.layout_relations:
        dependencies.setdefault(relation.source.node, []).append(relation.target.node)
    ordered, visited, active = [], set(), set()
    def visit(node):
        if node in visited or node in active:
            return
        active.add(node)
        for target in dependencies.get(node, []):
            visit(target)
        active.remove(node)
        visited.add(node)
        ordered.append(node)
    for node in scene.nodes:
        visit(node.id)
    order = {node: index for index, node in enumerate(ordered)}
    def painted(name):
        return [part for _, part in sorted(layers[name], key=lambda row: order[row[0]])]
    drawing.parts = [*painted("background"), *painted("body"), *drawing.parts,
                     *painted("front"), *painted("labels"), *annotations.parts]
    normalized = normalize_svg(drawing.svg(), alt=scene.alt, caption=scene.caption, components=True)
    illustration = QuestionIllustration(kind="svg", schema_version=2, sanitizer_version=3,
        svg=normalized.svg, alt=scene.alt, caption=scene.caption,
        width=normalized.width, height=normalized.height,
        content_hash=_hash(normalized.svg, scene.alt, scene.caption, 2))
    source = DiagramSource(catalog_version=version, renderer_version=RENDERER_VERSION,
        scene_hash=digest(scene.model_dump(mode="json")), asset_versions=versions, scene=scene)
    return CompiledDiagram(illustration, source, facts)


def preview_asset(asset_id: str, params: dict | None = None, profile="textbook") -> QuestionIllustration:
    asset = catalog()[1].get(asset_id)
    if asset is None:
        raise DiagramError("diagram_asset_missing")
    from .semantics import instantiate_asset
    part = instantiate_asset(asset_id, asset.version, {**asset.sample_params, **(params or {})},
                             monochrome=profile == "monochrome").drawing
    if part.width <= 200 and part.height <= 200:
        sheet = Drawing(320, 320, part.monochrome)
        sheet.add(part, 16, 16, 1.8)
        part = sheet
    normalized = normalize_svg(part.svg(), alt=asset.title, components=True)
    return QuestionIllustration(kind="svg", schema_version=2, sanitizer_version=3,
        svg=normalized.svg, alt=asset.title, caption="", width=normalized.width, height=normalized.height,
        content_hash=_hash(normalized.svg, asset.title, "", 2))

"""Hard material/physical checks independent of the composition and review models."""
from __future__ import annotations

import math
import re

from app.diagrams.semantics import capabilities

from .contracts import IllustrationError
from .requirements import allowed_label

DOMAINS = {"physics", "chemistry", "circuit", "measurement", "geometry", "chart",
    "biology", "earth", "graph", "systems", "mathematics", "statistics", "geography",
    "language", "history", "economics", "engineering", "astronomy", "music",
    "visual_art", "sports", "agriculture", "environment", "custom"}


def validate_relations(placed, relations, contract, report):
    from .layout import contains
    represented, circuit_edges = set(), []
    supports = {r.start.instance: r.end.instance for r in relations if r.type == "supported_by"}
    def depth(node, visiting=None):
        visiting = set() if visiting is None else visiting
        if node in visiting:
            raise IllustrationError("relation_unrealizable", target=node)
        return 1+depth(supports[node], visiting | {node}) if node in supports else 0
    ordered = sorted(relations, key=lambda r: (r.type != "supported_by", depth(r.start.instance) if r.type == "supported_by" else 0))
    for relation in ordered:
        try:
            a, b = placed[relation.start.instance], placed[relation.end.instance]
        except KeyError as exc:
            raise IllustrationError("relation_unrealizable", target=relation.relation_id) from exc
        if a.id == b.id:
            raise IllustrationError("relation_unrealizable", target=relation.relation_id)
        kind = relation.type
        if kind in {"inside", "immersed_in"}:
            if b.rotation or a.rotation:
                raise IllustrationError("relation_unrealizable", target=relation.relation_id)
            body = a.region(relation.start.region or "body")
            container = b.region(relation.end.region or ("liquid" if kind == "immersed_in" else "cavity"))
            construction = a.recipe and a.recipe == b.recipe and (
                a.geometry.derived_facts.get("construction_part") == "triangle" and
                b.geometry.derived_facts.get("construction_part") == "circle")
            if construction and (a.x, a.y, a.scale, a.geometry.params["radius"]) != (
                    b.x, b.y, b.scale, b.geometry.params["radius"]):
                raise IllustrationError("relation_unrealizable", target=relation.relation_id)
            if not construction and not contains(container, body, margin=2):
                raise IllustrationError("relation_unrealizable", target=relation.relation_id, repairable=True)
        elif kind == "supported_by":
            start, end = a.port(relation.start.port), b.port(relation.end.port)
            if a.geometry.ports[relation.start.port]["kind"] != "support" or b.geometry.ports[relation.end.port]["kind"] != "support":
                raise IllustrationError("relation_unrealizable", target=relation.relation_id)
            dx, dy = end[0]-start[0], end[1]-start[1]
            # Small contact corrections only; no rearrangement of a tableau.
            if abs(dx) > 32 or abs(dy) > 32:
                raise IllustrationError("relation_unrealizable", target=relation.relation_id, repairable=True)
            if abs(dx) > .01 or abs(dy) > .01:
                a.x += dx
                a.y += dy
                report.adjustments.append({"instance": a.id, "dx": dx, "dy": dy, "reason": "support_contact"})
        elif kind in {"connected", "series", "suspended_from"}:
            start, end = a.port(relation.start.port), b.port(relation.end.port)
            ak, bk = a.geometry.ports[relation.start.port]["kind"], b.geometry.ports[relation.end.port]["kind"]
            medium = "wire" if kind == "series" else "rope" if kind == "suspended_from" else relation.medium
            if ak != medium or bk != medium or medium == "line":
                raise IllustrationError("relation_unrealizable", target=relation.relation_id)
            if kind == "suspended_from":
                if start[1] <= end[1] or abs(start[0]-end[0]) > 32:
                    raise IllustrationError("relation_unrealizable", target=relation.relation_id, repairable=True)
                if abs(start[0]-end[0]) > .01:
                    a.x += end[0]-start[0]
                    report.adjustments.append({"instance": a.id, "dx": end[0]-start[0], "dy": 0, "reason": "suspension_vertical"})
            if kind == "series":
                circuit_edges.append((a.id, relation.start.port, b.id, relation.end.port))
        elif kind in {"parallel_to", "perpendicular"}:
            # Axis direction must be exposed by an actual pair of ports.
            def direction(node):
                ports = node.geometry.ports
                pair = next(((x, y) for x, y in [("terminal_left", "terminal_right"),
                    ("spring_start", "spring_end"), ("support_top", "support_bottom")] if x in ports and y in ports), None)
                if pair is None:
                    raise IllustrationError("relation_unrealizable", target=relation.relation_id)
                x, y = node.port(pair[0]), node.port(pair[1])
                return math.degrees(math.atan2(y[1]-x[1], y[0]-x[0]))
            angle = abs((direction(a)-direction(b)) % 180)
            error = min(angle, 180-angle) if kind == "parallel_to" else abs(angle-90)
            if error > .1:
                raise IllustrationError("relation_unrealizable", target=relation.relation_id, repairable=True)
        elif kind == "ordered_left_to_right":
            if a.box()[0]+a.box()[2] >= b.box()[0]:
                raise IllustrationError("relation_unrealizable", target=relation.relation_id, repairable=True)
        matches = [required for required in contract.required_relations if required.type == kind and (
            required.from_entity == a.entity and required.to_entity == b.entity or
            kind in {"series", "parallel_to", "perpendicular", "connected"} and
            required.from_entity == b.entity and required.to_entity == a.entity)]
        if relation.contract_relation_id and not any(required.id == relation.contract_relation_id for required in matches):
            raise IllustrationError("relation_unrealizable", target=relation.relation_id)
        represented.update(required.id for required in matches)
        for required in matches:
            report.verified_facts.extend(required.fact_refs)
        # An undeclared scientific relation may introduce a new condition.
        if not matches and not (a.recipe and a.recipe == b.recipe):
            raise IllustrationError("relation_unrealizable", target=relation.relation_id)
    missing = {required.id for required in contract.required_relations} - represented
    if missing:
        raise IllustrationError("relation_unrealizable", target=sorted(missing)[0], repairable=True)
    # A later suspension correction must not invalidate an earlier contact or
    # containment relation. All locked constraints are checked once more.
    for relation in relations:
        a, b = placed[relation.start.instance], placed[relation.end.instance]
        if relation.type == "supported_by" and math.dist(a.port(relation.start.port), b.port(relation.end.port)) > .05:
            raise IllustrationError("relation_unrealizable", target=relation.relation_id, repairable=True)
        construction = a.geometry.derived_facts.get("construction_part") == "triangle" and b.geometry.derived_facts.get("construction_part") == "circle" and a.recipe == b.recipe
        if relation.type in {"inside", "immersed_in"} and not construction and not contains(
                b.region(relation.end.region or ("liquid" if relation.type == "immersed_in" else "cavity")),
                a.region(relation.start.region or "body"), margin=2):
            raise IllustrationError("relation_unrealizable", target=relation.relation_id, repairable=True)
    if circuit_edges:
        degrees, terminals, graph = {}, set(), {}
        for a, ap, b, bp in circuit_edges:
            for endpoint in [(a, ap), (b, bp)]:
                if endpoint in terminals:
                    raise IllustrationError("relation_unrealizable", target=endpoint[0])
                terminals.add(endpoint)
            for start, end in [(a, b), (b, a)]:
                degrees[start] = degrees.get(start, 0)+1
                graph.setdefault(start, set()).add(end)
        stack, visited = [next(iter(graph))], set()
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            stack.extend(graph[node]-visited)
        if len(visited) != len(graph) or any(degree > 2 for degree in degrees.values()):
            raise IllustrationError("relation_unrealizable")
        circuit_nodes = {node.id for node in placed.values() if "wire_terminal" in capabilities(node.geometry.asset_id)}
        if contract.visual_role == "essential" and (circuit_nodes - visited or any(v != 2 for v in degrees.values())):
            raise IllustrationError("relation_unrealizable")
    report.verified_relations = sorted(represented)


def validate_material(placed, bindings, scene, contract, report):
    if contract.visual_role == "essential" and any(node.geometry.domain not in DOMAINS for node in placed.values()):
        raise IllustrationError("unsupported_domain")
    drawn = [node.entity for node in placed.values() if node.entity]
    entities = {entity.id for entity in contract.entities}
    if entities - set(drawn) or len(drawn) != len(set(drawn)):
        raise IllustrationError("missing_material")
    if any(not node.entity for node in placed.values()):
        raise IllustrationError("parameter_unbound")
    shown = set(report.verified_facts)
    for node, params in bindings.items():
        shown.update(value["id"] for value in params.values() if "id" in value)
    labels = [annotation.text for annotation in scene.annotations] + [text for node in placed.values()
        for text in node.geometry.intrinsic_marks]
    for annotation in scene.annotations:
        shown.update(annotation.fact_refs)
    if set(contract.required_marks) - set(labels):
        raise IllustrationError("missing_material")
    # A scalar already explicitly printed in the stem need not also appear in
    # a supplemental picture, unless it is a required mark. Depicted facts do.
    necessary = {fact.id for fact in contract.facts if fact.display_policy == "depict_only"}
    # Explicit conditions printed in the public stem are already material;
    # essential does not mean duplicating every scalar on the picture.
    for fact in contract.facts:
        if fact.display_policy == "explicit" and fact.source_quote and fact.source_quote in contract.source_text(fact.source_ref):
            shown.add(fact.id)
    if necessary - shown:
        raise IllustrationError("missing_fact_binding", target=sorted(necessary-shown)[0])
    for node in placed.values():
        if node.geometry.asset_id == "geometry.circumcircle" and not re.search(
                r"正三角形|等边|equilateral", contract.public_question.stem, re.I):
            # This registered construction is specifically equilateral; it
            # must never silently impose equal angles on a general triangle.
            raise IllustrationError("unsupported_domain", target=node.id)
        for text in node.geometry.intrinsic_marks:
            # Scale values are allowed on calibrated instruments, not on
            # labels announcing a reading. Chart values require explicit data.
            own = bindings.get(node.id, {})
            numeric_scale = node.geometry.domain == "measurement" and ("capacity" in own or
                "reading" in own and ("maximum" in own or node.geometry.asset_id == "apparatus.thermometer"))
            chart_data = node.geometry.domain == "chart" and ("values" in own or "points" in own or
                {"function", "x_range", "y_range"} <= set(own))
            units = {f.unit for f in contract.facts if f.display_policy != "hidden"}
            circuit_symbol = text == "A" and node.geometry.asset_id == "circuit.ammeter"
            if not (allowed_label(text, contract) or numeric_scale or chart_data or text in units or circuit_symbol):
                raise IllustrationError("parameter_unbound", target=node.id)
    report.verified_facts = sorted(shown)

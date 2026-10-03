"""Deterministic presentation fixes for the legacy component compiler."""
from __future__ import annotations

import copy
import heapq
import math
import re

from .schema import DiagramError


def crosses(start, end, bounds):
    """Whether a segment enters a rectangle's interior (edges may touch)."""
    x, y, w, h = bounds
    lower, upper = 0.0, 1.0
    for origin, delta, low, high in ((start[0], end[0]-start[0], x, x+w),
                                    (start[1], end[1]-start[1], y, y+h)):
        if abs(delta) < 1e-9:
            if not low < origin < high:
                return False
        else:
            enter, leave = sorted(((low-origin)/delta, (high-origin)/delta))
            lower, upper = max(lower, enter), min(upper, leave)
            if lower >= upper:
                return False
    return lower < upper


def _compact(points):
    compact = []
    for point in points:
        if compact and point == compact[-1]:
            continue
        if len(compact) >= 2 and ((compact[-2][0] == compact[-1][0] == point[0]) or
                                 (compact[-2][1] == compact[-1][1] == point[1])):
            compact[-1] = point
        else:
            compact.append(point)
    return compact


def wire_networks(connections):
    """Only explicit wire links identify shared physical terminal networks."""
    parents = {}
    def root(key):
        parents.setdefault(key, key)
        if parents[key] != key:
            parents[key] = root(parents[key])
        return parents[key]
    for connection in connections:
        if connection["kind"] != "wire":
            continue
        keys = [(connection[end]["node"], connection[end].get("anchor", "center")) for end in ("start", "end")]
        parents[root(keys[1])] = root(keys[0])
    return {key: root(key) for key in parents}


def wire_route(start, end, *, route, blockers, width, height, endpoint_bodies=None, occupied=(), network=None):
    """Route between the same real terminals without crossing protected ink."""
    start, end = tuple(start), tuple(end)
    if endpoint_bodies:
        blockers = list(blockers)
        for index, prior in enumerate(occupied):
            if prior["network"] == network:
                continue
            for a, b in zip(prior["points"], prior["points"][1:]):
                blockers.append({"node": "wire:"+str(index), "region": "connection",
                    "bounds": [min(a[0], b[0])-1, min(a[1], b[1])-1, abs(a[0]-b[0])+2, abs(a[1]-b[1])+2]})
    if endpoint_bodies:
        regions = blockers
        (source_owner, source_body), (target_owner, target_body) = endpoint_bodies
        def sides(point, body):
            x, y, w, h = body
            distances = {"left": abs(point[0]-x), "right": abs(point[0]-x-w),
                         "top": abs(point[1]-y), "bottom": abs(point[1]-y-h)}
            closest = min(distances.values())
            return {side for side, distance in distances.items() if abs(distance-closest) < .01}
        source_sides, target_sides = sides(start, source_body), sides(end, target_body)
        facing = (abs(start[1]-end[1]) < .01 and (
            start[0] < end[0] and "right" in source_sides and "left" in target_sides or
            start[0] > end[0] and "left" in source_sides and "right" in target_sides)) or (
            abs(start[0]-end[0]) < .01 and (
            start[1] < end[1] and "bottom" in source_sides and "top" in target_sides or
            start[1] > end[1] and "top" in source_sides and "bottom" in target_sides))
        other = [row["bounds"] if row["node"] in {source_owner, target_owner} else
                 [row["bounds"][0]-3, row["bounds"][1]-3, row["bounds"][2]+6, row["bounds"][3]+6]
                 for row in regions
                 if row["node"] not in {source_owner, target_owner} or row["region"] != "body"]
        if facing and not any(crosses(start, end, bounds) for bounds in other):
            return [start, end]
        def exits(point, owner, body):
            x, y, w, h = body
            # Ports escape outward through their own lead area. All other
            # bodies and this object's sensitive regions remain obstacles.
            other = [row["bounds"] if row["node"] == owner else
                     [row["bounds"][0]-3, row["bounds"][1]-3, row["bounds"][2]+6, row["bounds"][3]+6]
                     for row in regions if row["node"] != owner or row["region"] != "body"]
            choices = [(x-6, point[1]), (x+w+6, point[1]), (point[0], y-6), (point[0], y+h+6)]
            closest = min(math.dist(point, item) for item in choices)
            return [choice for choice in choices if abs(math.dist(point, choice)-closest) < .01
                    if 4 <= choice[0] <= width-4 and 4 <= choice[1] <= height-4
                    and not any(crosses(point, choice, bounds) for bounds in other)]
        starts, ends = exits(start, source_owner, source_body), exits(end, target_owner, target_body)
        bounds = [row["bounds"] for row in regions]
        choices = []
        for escaped_start in starts:
            for escaped_end in ends:
                try:
                    middle = wire_route(escaped_start, escaped_end, route=route, blockers=bounds,
                                        width=width, height=height)
                except DiagramError:
                    continue
                points = _compact([start, *middle, end])
                # At each terminal the wire must extend outward, not back
                # across its own component. This avoids a hidden bypass.
                choices.append((sum(math.dist(a, b) for a, b in zip(points, points[1:]))+
                                len(points)*2, points))
        if not choices:
            raise DiagramError("diagram_connection_blocked")
        return min(choices, key=lambda row: row[0])[1]
    blockers = [[x-3, y-3, w+6, h+6] for x, y, w, h in blockers]
    middle = (start[0]+end[0])/2
    proposed = [start, (middle, start[1]), (middle, end[1]), end] if route == "orthogonal" else [start, end]
    if not any(crosses(a, b, bounds) for a, b in zip(proposed, proposed[1:]) for bounds in blockers):
        return proposed
    # A small rectilinear visibility grid, bounded by the existing scene's
    # protected regions. No editable waypoints, terminals or physical values.
    xs = sorted({start[0], end[0], 6.0, width-6.0, *(max(6.0, x-6) for x, _, _, _ in blockers),
                 *(min(width-6.0, x+w+6) for x, _, w, _ in blockers)})
    ys = sorted({start[1], end[1], 6.0, height-6.0, *(max(6.0, y-6) for _, y, _, _ in blockers),
                 *(min(height-6.0, y+h+6) for _, y, _, h in blockers)})
    initial = (xs.index(start[0]), ys.index(start[1]), 0)
    target = (xs.index(end[0]), ys.index(end[1]))
    queue, distances, previous = [(0.0, initial)], {initial: 0.0}, {}
    final = None
    while queue:
        cost, node = heapq.heappop(queue)
        if cost != distances.get(node):
            continue
        ix, iy, direction = node
        if (ix, iy) == target:
            final = node
            break
        for dx, dy, following_direction in ((-1, 0, 1), (1, 0, 1), (0, -1, 2), (0, 1, 2)):
            nx, ny = ix+dx, iy+dy
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            a, b = (xs[ix], ys[iy]), (xs[nx], ys[ny])
            if any(crosses(a, b, bounds) for bounds in blockers):
                continue
            following = (nx, ny, following_direction)
            next_cost = cost + math.dist(a, b) + (2 if direction and direction != following_direction else 0)
            if next_cost < distances.get(following, math.inf):
                distances[following], previous[following] = next_cost, node
                heapq.heappush(queue, (next_cost, following))
    if final is None:
        raise DiagramError("diagram_connection_blocked")
    points = []
    while final is not None:
        points.append((xs[final[0]], ys[final[1]]))
        final = previous.get(final)
    points.reverse()
    return _compact(points)


_RELATION_MARKERS = {
    "inside": r"内部|里面|之内|在.{0,30}中|inside|within|contained",
    "immersed_in": r"浸没|浸入|immersed|submerged",
    "supported_by": r"支撑|放在[^，,。；;]{1,30}上|置于[^，,。；;]{1,30}上|supported|rests? on",
    "suspended_from": r"悬挂|吊在|悬于|suspended|hangs? from",
}


def public_relation_supported(relation, public_stem):
    quote = relation.get("source_quote", "")
    pattern = _RELATION_MARKERS.get(relation.get("type"))
    marker_in_quote = re.search(pattern, quote, re.I) if pattern else None
    if not marker_in_quote or not quote or public_stem.count(quote) != 1:
        return False
    # Check the whole surrounding clause, including negations preceding the
    # quoted fragment. A later unrelated prohibition is not this relation.
    begin = public_stem.index(quote)+marker_in_quote.start()
    left = max([public_stem.rfind(mark, 0, begin) for mark in "，,。；;!?！？"])+1
    right = min([index for index, char in enumerate(public_stem[begin:], begin)
                 if char in "，,。；;!?！？"] or [len(public_stem)])
    clause = public_stem[left:right]
    marker = re.search(pattern, clause, re.I)
    return bool(marker) and not re.search(r"不|未|没有|避免|禁止|不要|不得|\bnot\b|\bnever\b|without",
                                          clause[:marker.start()], re.I)


def public_relation_kinds(public_stem):
    kinds = set()
    for clause in re.split(r"[，,。；;!?！？]", public_stem):
        clause_kinds = set()
        for kind in _RELATION_MARKERS:
            if kind == "inside" and not re.search(r"内部|里面|之内|inside|within|contained", clause, re.I):
                # An ordinary background phrase ("在实验中") is not a
                # material containment condition requiring another entity.
                continue
            if public_relation_supported({"type": kind, "source_quote": clause}, public_stem):
                clause_kinds.add(kind)
        # An immersion already expresses containment of the specified part;
        # it does not put the entire object inside the surrounding vessel.
        if "immersed_in" in clause_kinds:
            clause_kinds.discard("inside")
        kinds.update(clause_kinds)
    return kinds


def _target_bounds(nodes, reference):
    node = nodes[reference["node"]]
    if reference.get("region"):
        return node["regions"][reference["region"]]["bounds"]
    x, y = node["anchors"][reference["anchor"]]
    return [x, y, 0, 0]


def _relation_cycle(relations):
    dependencies = {}
    for relation in relations:
        dependencies.setdefault(relation["source"]["node"], []).append(relation["target"]["node"])
    visited, active = set(), set()
    def visit(node):
        if node in active:
            return True
        if node in visited:
            return False
        active.add(node)
        if any(visit(target) for target in dependencies.get(node, [])):
            return True
        active.remove(node)
        visited.add(node)
        return False
    return any(visit(node) for node in dependencies)


def relation_issues(geometry, public_stem):
    nodes = {node["id"]: node for node in geometry["nodes"]}
    issues = []
    if _relation_cycle(geometry.get("layout_relations", [])):
        issues.append({"code": "layout_relation_cycle"})
    if len(nodes) > 1:
        declared = {row["type"] for row in geometry.get("layout_relations", [])}
        issues.extend({"code": "missing_layout_relation", "type": kind}
                      for kind in sorted(public_relation_kinds(public_stem)-declared))
    for index, relation in enumerate(geometry.get("layout_relations", [])):
        source, target = relation["source"], relation["target"]
        if not public_relation_supported(relation, public_stem):
            issues.append({"code": "invalid_public_relation", "relation": index})
            continue
        if relation["type"] in {"inside", "immersed_in"} and not (source.get("region") and target.get("region")):
            issues.append({"code": "relation_region_required", "relation": index})
            continue
        try:
            subject = _target_bounds(nodes, source)
            container = _target_bounds(nodes, target)
        except KeyError:
            issues.append({"code": "unknown_relation_region", "relation": index})
            continue
        x, y, w, h = subject
        tx, ty, tw, th = container
        if relation["type"] in {"inside", "immersed_in"}:
            valid = min(w, h, tw, th) > 0 and tx+1 <= x and ty+1 <= y and x+w <= tx+tw-1 and y+h <= ty+th-1
        else:
            valid = x <= tx+tw and x+w >= tx and abs(
                y+h-ty if relation["type"] == "supported_by" else y-(ty+th)) <= 1
        if not valid:
            issues.append({"code": "layout_relation_mismatch", "relation": index,
                           "source_bounds": subject, "target_bounds": container})
    return issues


def fit_public_relations(raw, geometry, public_stem):
    """Translate declared regions to satisfy public spatial conditions.

    This uses the same region protocol for every material, with no asset IDs,
    scientific parameter defaults, inferred entity mapping or resizing.
    """
    nodes = {row["id"]: copy.deepcopy(row) for row in geometry["nodes"]}
    fitted = copy.deepcopy(raw)
    originals = {row["id"]: row for row in fitted["nodes"]}
    changed = False
    relations = geometry.get("layout_relations", [])
    if _relation_cycle(relations):
        return raw
    # Move the supporting/container target before its dependants. The model
    # may list a support chain in either order. Cycles were rejected above.
    by_source = {}
    for index, relation in enumerate(relations):
        by_source.setdefault(relation["source"]["node"], []).append(index)
    ordered, visited, active = [], set(), set()
    def visit(index):
        if index in visited or index in active:
            return
        active.add(index)
        for dependency in by_source.get(relations[index]["target"]["node"], []):
            visit(dependency)
        active.remove(index)
        visited.add(index)
        ordered.append(relations[index])
    for index in range(len(relations)):
        visit(index)
    for relation in ordered:
        if not public_relation_supported(relation, public_stem):
            continue
        source, target = relation["source"], relation["target"]
        if relation["type"] in {"inside", "immersed_in"} and not (source.get("region") and target.get("region")):
            continue
        try:
            subject = _target_bounds(nodes, source)
            container = _target_bounds(nodes, target)
        except KeyError:
            continue
        x, y, w, h = subject
        tx, ty, tw, th = container
        if relation["type"] in {"inside", "immersed_in"}:
            margin = 4.0
            if min(w, h, tw, th) <= 0 or tw < w+2*margin or th < h+2*margin or (tx+margin <= x and ty+margin <= y and
                                                    x+w <= tx+tw-margin and y+h <= ty+th-margin):
                continue
            dx, dy = tx+(tw-w)/2-x, ty+(th-h)/2-y
        else:
            dx = tx+(tw-w)/2-x if not (x <= tx+tw and x+w >= tx) else 0
            dy = ty-y-h if relation["type"] == "supported_by" else ty+th-y
        if abs(dx)+abs(dy) < .01:
            continue
        row = nodes[source["node"]]
        # No presentation fix can move the component outside the canvas.
        bx, by, bw, bh = row["bounds"] if "bounds" in row else row["regions"]["body"]["bounds"]
        if not (4 <= bx+dx and bx+dx+bw <= raw.get("width", 640)-4 and
                4 <= by+dy and by+dy+bh <= raw.get("height", 400)-4):
            continue
        original = originals[source["node"]]
        original["x"], original["y"] = original.get("x", 0)+dx, original.get("y", 0)+dy
        for region in row["regions"].values():
            region["bounds"][0] += dx
            region["bounds"][1] += dy
        for anchor in row.get("anchors", {}).values():
            anchor[0] += dx
            anchor[1] += dy
        if "bounds" in row:
            row["bounds"][0] += dx
            row["bounds"][1] += dy
        changed = True
    return fitted if changed else raw

import test from "node:test";
import assert from "node:assert/strict";
import {
  intersectLineLine, intersectLineCircle, intersectCircleCircle, circumcenter, angleBisectorDirection,
  polygonArea, polygonPerimeter, projectOnSegment, lineParamAllowed, resolveGeometry2D,
  type LinePrimitive, type CirclePrimitive,
} from "../src/math-workbench/index.ts";
import type { GeometryDefinition2D } from "../src/math-workbench/index.ts";

const line = (px: number, py: number, dx: number, dy: number): LinePrimitive => ({ point: { x: px, y: py }, direction: { x: dx, y: dy } });
const circle = (cx: number, cy: number, r: number): CirclePrimitive => ({ center: { x: cx, y: cy }, radius: r });
const near = (a: number, b: number, tol = 1e-8): boolean => Math.abs(a - b) <= tol * Math.max(1, Math.abs(a), Math.abs(b));

test("line-line intersection: crossing, parallel, coincident", () => {
  const hit = intersectLineLine(line(0, 0, 1, 1), line(2, 0, -1, 1));
  assert.equal(hit.length, 1);
  if (hit[0]) { assert.ok(near(hit[0].x, 1)); assert.ok(near(hit[0].y, 1)); }
  assert.deepEqual(intersectLineLine(line(0, 0, 1, 0), line(0, 1, 2, 0)), []);
  assert.deepEqual(intersectLineLine(line(0, 0, 1, 0), line(3, 0, 7, 0)), []);
});

test("line-circle: 0/1/2 solutions and tangent degeneracy", () => {
  const two = intersectLineCircle(line(0, 0, 1, 0), circle(0, 0, 2));
  assert.equal(two.length, 2);
  if (two[0] && two[1]) {
    assert.ok(near(Math.min(two[0].x, two[1].x), -2));
    assert.ok(near(Math.max(two[0].x, two[1].x), 2));
  }
  const tangent = intersectLineCircle(line(0, 2, 1, 0), circle(0, 0, 2));
  assert.equal(tangent.length, 2);
  if (tangent[0] && tangent[1]) assert.ok(near(tangent[0].x, tangent[1].x, 1e-9));
  assert.deepEqual(intersectLineCircle(line(0, 3, 1, 0), circle(0, 0, 2)), []);
});

test("circle-circle: two/one/none/contained/concentric", () => {
  const two = intersectCircleCircle(circle(-1, 0, 2), circle(1, 0, 2));
  assert.equal(two.length, 2);
  if (two[0] && two[1]) {
    assert.ok(near(two[0].x, 0) && near(two[0].y, Math.sqrt(3)));
    assert.ok(near(two[1].x, 0) && near(two[1].y, -Math.sqrt(3)));
  }
  const one = intersectCircleCircle(circle(0, 0, 2), circle(4, 0, 2));
  assert.equal(one.length, 2);
  if (one[0]) assert.ok(near(one[0].x, 2) && near(one[0].y, 0));
  assert.deepEqual(intersectCircleCircle(circle(0, 0, 1), circle(5, 0, 1)), []);
  assert.deepEqual(intersectCircleCircle(circle(0, 0, 5), circle(1, 0, 1)), []); // contained
  assert.deepEqual(intersectCircleCircle(circle(0, 0, 2), circle(0, 0, 3)), []); // concentric
});

test("circumcenter and polygon measures", () => {
  const c = circumcenter({ x: 0, y: 0 }, { x: 4, y: 0 }, { x: 0, y: 4 });
  assert.ok(c);
  if (c) { assert.ok(near(c.center.x, 2)); assert.ok(near(c.center.y, 2)); assert.ok(near(c.radius, Math.hypot(2, 2))); }
  assert.equal(circumcenter({ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 2 }), null);
  const square = [{ x: 0, y: 0 }, { x: 3, y: 0 }, { x: 3, y: 3 }, { x: 0, y: 3 }];
  assert.ok(near(polygonArea(square), 9));
  assert.ok(near(polygonPerimeter(square), 12));
  const p = projectOnSegment({ x: 1, y: 5 }, { a: { x: 0, y: 0 }, b: { x: 4, y: 0 } });
  assert.ok(near(p.x, 1) && near(p.y, 0));
  assert.equal(lineParamAllowed("segment", 0.5), true);
  assert.equal(lineParamAllowed("segment", 1.5), false);
  assert.equal(lineParamAllowed("ray", -0.5), false);
  assert.equal(lineParamAllowed("line", -5), true);
});

test("angle bisector direction is unit and symmetric", () => {
  const d = angleBisectorDirection({ x: 0, y: 0 }, { x: -1, y: 0 }, { x: 0, y: 1 });
  assert.ok(near(Math.hypot(d.x, d.y), 1));
  // bisector of the right angle at origin between -x and +y axes points at 135°
  assert.ok(near(d.x, -Math.SQRT1_2) && near(d.y, Math.SQRT1_2));
});

function doc2d(objects: GeometryDefinition2D[]): GeometryDefinition2D[] { return objects; }

const style = { color: "#2f7d6e", width: 2, opacity: 1, dashed: false };
const freePoint = (id: string, label: string, x: number, y: number): GeometryDefinition2D =>
  ({ kind2d: "point", id, label, visible: true, locked: false, style, construction: { kind: "free", x, y } });

test("construction DAG resolves midpoints, bisectors and intersections", () => {
  const objects = doc2d([
    freePoint("A", "A", 0, 0),
    freePoint("B", "B", 4, 0),
    { kind2d: "point", id: "M", label: "M", visible: true, locked: false, style, construction: { kind: "midpoint", aId: "A", bId: "B" } },
    { kind2d: "line", id: "L", label: "L", visible: true, locked: false, style, lineKind: "segment", aId: "A", bId: "B" },
    { kind2d: "constructedLine", id: "PB", label: "PB", visible: true, locked: false, style, construction: { kind: "perpendicularBisector", aId: "A", bId: "B" } },
    { kind2d: "circle", id: "C", label: "C", visible: true, locked: false, style, construction: { kind: "centerRadius", centerId: "M", radius: 2 } },
    { kind2d: "point", id: "I", label: "I", visible: true, locked: false, style, construction: { kind: "intersection", aId: "PB", bId: "C", branch: 1 } },
  ]);
  const resolved = resolveGeometry2D(objects);
  const m = resolved.points.get("M");
  assert.ok(m);
  if (m) { assert.ok(near(m.position.x, 2)); assert.ok(near(m.position.y, 0)); }
  const pb = resolved.constructedLines.get("PB");
  assert.ok(pb);
  if (pb) {
    assert.ok(near(pb.point.y, 0) && near(pb.point.x, 2));
    assert.ok(near(Math.abs(pb.direction.y), 1)); // vertical
  }
  const i = resolved.points.get("I");
  assert.ok(i);
  if (i) assert.ok(near(i.position.x, 2) && near(Math.abs(i.position.y), 2));
});

test("moving a root point recomputes only downstream truth", () => {
  const base = doc2d([
    freePoint("A", "A", 0, 0),
    freePoint("B", "B", 6, 0),
    { kind2d: "point", id: "M", label: "M", visible: true, locked: false, style, construction: { kind: "midpoint", aId: "A", bId: "B" } },
  ]);
  const before = resolveGeometry2D(base).points.get("M")?.position;
  const moved = base.map((o) => (o.id === "B" ? freePoint("B", "B", 10, 0) : o));
  const after = resolveGeometry2D(moved as GeometryDefinition2D[]).points.get("M")?.position;
  assert.ok(before && after);
  if (before && after) {
    assert.ok(near(before.x, 3) && near(after.x, 5));
  }
});

test("branch anchoring keeps the intersection solution stable", () => {
  const mk = (branch: 1 | 2, anchor?: { x: number; y: number }): GeometryDefinition2D =>
    ({ kind2d: "point", id: "I", label: "I", visible: true, locked: false, style, construction: { kind: "intersection", aId: "L", bId: "C", branch, ...(anchor ? { branchAnchor: anchor } : {}) } });
  const lineObj: GeometryDefinition2D = { kind2d: "line", id: "L", label: "L", visible: true, locked: false, style, lineKind: "line", aId: "A", bId: "B" };
  const circleObj: GeometryDefinition2D = { kind2d: "circle", id: "C", label: "C", visible: true, locked: false, style, construction: { kind: "centerRadius", centerId: "O", radius: 2 } };
  const common = [freePoint("O", "O", 0, 0), freePoint("A", "A", -3, 0), freePoint("B", "B", 3, 0), lineObj, circleObj];
  const b1 = resolveGeometry2D([...common, mk(1)]).points.get("I")?.position;
  const b2 = resolveGeometry2D([...common, mk(2)]).points.get("I")?.position;
  assert.ok(b1 && b2 && near(b1.y + b2.y, 0, 1e-12));
  // Anchor near the negative-x solution forces branch 2 onto that solution too.
  const anchored = resolveGeometry2D([...common, mk(2, { x: -2, y: 0 })]).points.get("I")?.position;
  assert.ok(anchored && near(anchored.x, -2));
});

test("degenerate constructions warn instead of emitting NaN", () => {
  const objects = doc2d([
    freePoint("A", "A", 1, 1),
    freePoint("B", "B", 3, 3),
    freePoint("C", "C", 5, 5),
    { kind2d: "circle", id: "bad", label: "bad", visible: true, locked: false, style, construction: { kind: "threePoints", aId: "A", bId: "B", cId: "C" } },
  ]);
  const resolved = resolveGeometry2D(objects);
  assert.equal(resolved.circles.size, 0);
  assert.ok(resolved.diagnostics.some((d) => d.code === "collinear_points"));
  for (const p of resolved.points.values()) {
    assert.ok(Number.isFinite(p.position.x) && Number.isFinite(p.position.y));
  }
});

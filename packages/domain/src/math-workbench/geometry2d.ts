/**
 * Plane analytic geometry: primitives, constructors, intersections, measures
 * and transforms, plus the construction-DAG resolver for 2D objects.
 *
 * All primitives are math-space ({x,y} world units). Intersection solvers
 * return BOTH solutions with stable ordering so callers can pick a branch and
 * keep it stable across edits (plan F5: branch anchoring, no unexplained
 * flipping near tangency).
 */

import { err, geometryEpsilon, nearlyEqual, warn, type MathDiagnostic } from "./diagnostics.ts";
import type {
  Circle2DDefinition, ConstructedLine2DDefinition, GeometryDefinition2D, Line2DDefinition, Pt2,
  ResolvedArc2D, ResolvedCircle2D, ResolvedGeometry2D, ResolvedLine2D, ResolvedPoint2D, ResolvedPolygon2D,
} from "./model.ts";

export interface LinePrimitive { point: Pt2; direction: Pt2 }
export interface CirclePrimitive { center: Pt2; radius: number }
export interface SegmentPrimitive { a: Pt2; b: Pt2 }

export function sub2(a: Pt2, b: Pt2): Pt2 { return { x: a.x - b.x, y: a.y - b.y }; }
export function add2(a: Pt2, b: Pt2): Pt2 { return { x: a.x + b.x, y: a.y + b.y }; }
export function scale2(a: Pt2, k: number): Pt2 { return { x: a.x * k, y: a.y * k }; }
export function dot2(a: Pt2, b: Pt2): number { return a.x * b.x + a.y * b.y; }
export function cross2(a: Pt2, b: Pt2): number { return a.x * b.y - a.y * b.x; }
export function length2(a: Pt2): number { return Math.hypot(a.x, a.y); }
export function distance2(a: Pt2, b: Pt2): number { return Math.hypot(a.x - b.x, a.y - b.y); }
export function normalize2(a: Pt2): Pt2 {
  const len = length2(a);
  if (len < 1e-15) return { x: 0, y: 0 };
  return { x: a.x / len, y: a.y / len };
}
export function perpendicular2(a: Pt2): Pt2 { return { x: -a.y, y: a.x }; }

/** angle abc in radians (0..π for the internal angle). */
export function angleAt(b: Pt2, a: Pt2, c: Pt2): number {
  const u = normalize2(sub2(a, b));
  const v = normalize2(sub2(c, b));
  return Math.acos(Math.min(1, Math.max(-1, dot2(u, v))));
}

/* ------------------------------- intersections ------------------------------- */

/** 0 (parallel/coincident), 1 or 2 solutions; ordered by x then y. */
export function intersectLineLine(l1: LinePrimitive, l2: LinePrimitive): Pt2[] {
  const denom = cross2(l1.direction, l2.direction);
  const scale = Math.max(length2(l1.direction), length2(l2.direction), 1);
  if (Math.abs(denom) <= geometryEpsilon(scale)) return [];
  const diff = sub2(l2.point, l1.point);
  const t = cross2(diff, l2.direction) / denom;
  return [add2(l1.point, scale2(l1.direction, t))];
}

export function intersectLineCircle(line: LinePrimitive, circle: CirclePrimitive): Pt2[] {
  const f = sub2(line.point, circle.center);
  const dir = line.direction;
  const a = dot2(dir, dir);
  const b = 2 * dot2(f, dir);
  const c = dot2(f, f) - circle.radius * circle.radius;
  const disc = b * b - 4 * a * c;
  const eps = geometryEpsilon(circle.radius);
  if (disc < -eps) return [];
  const discClamped = Math.max(disc, 0);
  const root = Math.sqrt(discClamped);
  const t1 = (-b - root) / (2 * a);
  const t2 = (-b + root) / (2 * a);
  if (nearlyEqual(t1, t2, 1e-12, eps)) {
    const p = add2(line.point, scale2(dir, t1));
    return [p, p]; // tangent: two coincident solutions
  }
  const p1 = add2(line.point, scale2(dir, t1));
  const p2 = add2(line.point, scale2(dir, t2));
  return [p1, p2];
}

export function intersectCircleCircle(c1: CirclePrimitive, c2: CirclePrimitive): Pt2[] {
  const d = distance2(c1.center, c2.center);
  const eps = geometryEpsilon(Math.max(c1.radius, c2.radius, d));
  if (d <= eps) return []; // concentric (or same center): no well-defined pair
  if (d > c1.radius + c2.radius + eps) return [];
  if (d + Math.min(c1.radius, c2.radius) < Math.max(c1.radius, c2.radius) - eps) return []; // contained
  const a = (c1.radius * c1.radius - c2.radius * c2.radius + d * d) / (2 * d);
  const h2 = c1.radius * c1.radius - a * a;
  const h = Math.sqrt(Math.max(h2, 0));
  const base = add2(c1.center, scale2(normalize2(sub2(c2.center, c1.center)), a));
  const perp = perpendicular2(normalize2(sub2(c2.center, c1.center)));
  if (h <= eps) return [base, base];
  const p1 = add2(base, scale2(perp, h));
  const p2 = add2(base, scale2(perp, -h));
  return [p1, p2];
}

/** Point-to-segment closest point; used for hit-testing and measurements. */
export function projectOnSegment(p: Pt2, s: SegmentPrimitive): Pt2 {
  const ab = sub2(s.b, s.a);
  const denom = dot2(ab, ab);
  if (denom < 1e-30) return s.a;
  const t = Math.min(1, Math.max(0, dot2(sub2(p, s.a), ab) / denom));
  return add2(s.a, scale2(ab, t));
}

export function pointOnSegment(p: Pt2, s: SegmentPrimitive, tol: number): boolean {
  return distance2(p, projectOnSegment(p, s)) <= tol;
}

/** Clamp a parameter to the parametric domain of a line kind. */
export function lineParamAllowed(lineKind: "line" | "segment" | "ray", t: number, tol = 1e-9): boolean {
  if (lineKind === "segment") return t >= -tol && t <= 1 + tol;
  if (lineKind === "ray") return t >= -tol;
  return true;
}

/* -------------------------------- constructors -------------------------------- */

export function circumcenter(a: Pt2, b: Pt2, c: Pt2): { center: Pt2; radius: number } | null {
  const d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y));
  if (Math.abs(d) < 1e-12) return null; // collinear
  const ux = ((a.x * a.x + a.y * a.y) * (b.y - c.y) + (b.x * b.x + b.y * b.y) * (c.y - a.y) + (c.x * c.x + c.y * c.y) * (a.y - b.y)) / d;
  const uy = ((a.x * a.x + a.y * a.y) * (c.x - b.x) + (b.x * b.x + b.y * b.y) * (a.x - c.x) + (c.x * c.x + c.y * c.y) * (b.x - a.x)) / d;
  const center = { x: ux, y: uy };
  return { center, radius: distance2(center, a) };
}

/** Angle bisector direction of ∠abc; branch 2 is the external bisector. */
export function angleBisectorDirection(b: Pt2, a: Pt2, c: Pt2, branch: 1 | 2 = 1): Pt2 {
  const u = normalize2(sub2(a, b));
  const v = normalize2(sub2(c, b));
  let dir = normalize2(add2(u, v));
  if (length2(dir) < 1e-12) dir = normalize2(perpendicular2(u)); // straight angle fallback
  return branch === 1 ? dir : perpendicular2(dir);
}

/* --------------------------- measures and transforms --------------------------- */

export interface Measurement {
  kind: "distance" | "angle" | "perimeter" | "area" | "slope" | "radius";
  value: number;
}

export function segmentLength(a: Pt2, b: Pt2): number { return distance2(a, b); }
export function polygonPerimeter(points: readonly Pt2[]): number {
  let sum = 0;
  for (let i = 0; i < points.length; i++) sum += distance2(points[i] as Pt2, points[(i + 1) % points.length] as Pt2);
  return sum;
}
/** Signed shoelace area (positive = CCW ordering). */
export function polygonArea(points: readonly Pt2[]): number {
  let sum = 0;
  for (let i = 0; i < points.length; i++) {
    const p = points[i] as Pt2;
    const q = points[(i + 1) % points.length] as Pt2;
    sum += cross2(p, q);
  }
  return Math.abs(sum) / 2;
}
export function lineSlope(line: LinePrimitive): number | null {
  if (Math.abs(line.direction.x) < 1e-15) return null;
  return line.direction.y / line.direction.x;
}

export function translatePoint(p: Pt2, dx: number, dy: number): Pt2 { return { x: p.x + dx, y: p.y + dy }; }
export function rotatePoint(p: Pt2, center: Pt2, radians: number): Pt2 {
  const cos = Math.cos(radians), sin = Math.sin(radians);
  const dx = p.x - center.x, dy = p.y - center.y;
  return { x: center.x + dx * cos - dy * sin, y: center.y + dx * sin + dy * cos };
}
export function reflectPointOverLine(p: Pt2, line: LinePrimitive): Pt2 {
  const dir = normalize2(line.direction);
  const f = sub2(p, line.point);
  const proj = add2(line.point, scale2(dir, dot2(f, dir)));
  return add2(scale2(proj, 2), scale2(p, -1));
}
export function dilatePoint(p: Pt2, center: Pt2, factor: number): Pt2 {
  return { x: center.x + (p.x - center.x) * factor, y: center.y + (p.y - center.y) * factor };
}

/* --------------------------------- resolver --------------------------------- */

interface ResolveState {
  points: Map<string, ResolvedPoint2D>;
  lines: Map<string, ResolvedLine2D>;
  constructedLines: Map<string, ResolvedLine2D>;
  circles: Map<string, ResolvedCircle2D>;
  arcs: Map<string, ResolvedArc2D>;
  polygons: Map<string, ResolvedPolygon2D>;
  order: string[];
  diagnostics: MathDiagnostic[];
}

function pickBranch(solutions: readonly Pt2[], branch: 1 | 2, anchor: Pt2 | undefined): Pt2 {
  const first = solutions[0] ?? { x: 0, y: 0 };
  const second = solutions[1] ?? first;
  if (anchor) {
    // Anchor wins: keeps the intersection on the same branch across edits.
    return distance2(first, anchor) <= distance2(second, anchor) ? first : second;
  }
  return branch === 1 ? first : second;
}

/**
 * Evaluate a document's 2D objects in dependency order. Root-point moves only
 * recompute reachable downstream nodes (the Web layer calls this per edit and
 * diffs outputs). Degenerate constructions surface diagnostics, not NaNs.
 */
export function resolveGeometry2D(objects: readonly GeometryDefinition2D[]): ResolvedGeometry2D {
  const state: ResolveState = {
    points: new Map(), lines: new Map(), constructedLines: new Map(), circles: new Map(),
    arcs: new Map(), polygons: new Map(), order: [], diagnostics: [],
  };
  const byId = new Map(objects.map((o) => [o.id, o]));
  const resolvedIds = new Set<string>();

  const getPoint = (id: string): Pt2 | null => {
    const p = state.points.get(id);
    if (p) return p.position;
    const def = byId.get(id);
    if (def && def.kind2d === "point" && !resolvedIds.has(id)) resolveOne(def);
    const after = state.points.get(id);
    return after ? after.position : null;
  };

  const lineLike = (id: string): LinePrimitive | null => {
    const l = state.lines.get(id) ?? state.constructedLines.get(id);
    if (l) return { point: l.point, direction: l.direction };
    const def = byId.get(id);
    if (def && (def.kind2d === "line" || def.kind2d === "constructedLine") && !resolvedIds.has(id)) resolveOne(def);
    const after = state.lines.get(id) ?? state.constructedLines.get(id);
    return after ? { point: after.point, direction: after.direction } : null;
  };

  const circleLike = (id: string): CirclePrimitive | null => {
    const c = state.circles.get(id);
    if (c) return { center: c.center, radius: c.radius };
    const def = byId.get(id);
    if (def && def.kind2d === "circle" && !resolvedIds.has(id)) resolveOne(def);
    const after = state.circles.get(id);
    return after ? { center: after.center, radius: after.radius } : null;
  };

  const asLine = (lineKind: "line" | "segment" | "ray" | "line", a: Pt2, b: Pt2): ResolvedLine2D => {
    const directionRaw = sub2(b, a);
    const length = length2(directionRaw);
    const direction = length < 1e-15 ? { x: 1, y: 0 } : scale2(directionRaw, 1 / length);
    return { id: "", label: "", kind: lineKind, point: a, direction, length: lineKind === "segment" ? length : null };
  };

  function resolveOne(obj: GeometryDefinition2D): void {
    if (resolvedIds.has(obj.id)) return;
    resolvedIds.add(obj.id);
    switch (obj.kind2d) {
      case "point": {
        let position: Pt2 | null = null;
        if (obj.construction.kind === "free") position = { x: obj.construction.x, y: obj.construction.y };
        else if (obj.construction.kind === "midpoint") {
          const a = getPoint(obj.construction.aId);
          const b = getPoint(obj.construction.bId);
          if (a && b) position = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
          else state.diagnostics.push(err("unresolved_reference", { objectId: obj.id }));
        } else {
          const aId = obj.construction.aId, bId = obj.construction.bId;
          const lineA = lineLike(aId), lineB = lineLike(bId);
          const circA = circleLike(aId), circB = circleLike(bId);
          let solutions: Pt2[] = [];
          if (lineA && lineB) solutions = intersectLineLine(lineA, lineB);
          else if (lineA && circB) solutions = intersectLineCircle(lineA, circB);
          else if (circA && lineB) solutions = intersectLineCircle(lineB, circA);
          else if (circA && circB) solutions = intersectCircleCircle(circA, circB);
          if (solutions.length === 0) {
            state.diagnostics.push(warn("intersection_undefined", { objectId: obj.id }));
            const fallback = getPoint(aId) ?? { x: 0, y: 0 };
            position = fallback;
          } else {
            position = pickBranch(solutions, obj.construction.branch, obj.construction.branchAnchor);
          }
        }
        if (!position) position = { x: 0, y: 0 };
        state.points.set(obj.id, { id: obj.id, label: obj.label, position, definition: obj });
        state.order.push(obj.id);
        break;
      }
      case "line": {
        const a = getPoint(obj.aId);
        const b = getPoint(obj.bId);
        if (!a || !b) { state.diagnostics.push(err("unresolved_reference", { objectId: obj.id })); break; }
        const resolved = asLine(obj.lineKind, a, b);
        state.lines.set(obj.id, { ...resolved, id: obj.id, label: obj.label });
        state.order.push(obj.id);
        break;
      }
      case "constructedLine": {
        const c = obj.construction;
        let resolved: ResolvedLine2D | null = null;
        if (c.kind === "parallel" || c.kind === "perpendicular") {
          const p = c.pointId ? getPoint(c.pointId) : null;
          const base = c.lineId ? lineLike(c.lineId) : null;
          if (p && base) {
            const dir = c.kind === "parallel" ? base.direction : perpendicular2(base.direction);
            resolved = { id: obj.id, label: obj.label, kind: "line", point: p, direction: normalize2(dir), length: null };
          }
        } else if (c.kind === "perpendicularBisector") {
          const a = c.aId ? getPoint(c.aId) : null;
          const b = c.bId ? getPoint(c.bId) : null;
          if (a && b) {
            const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
            const dir = perpendicular2(sub2(b, a));
            if (length2(dir) < 1e-15) state.diagnostics.push(warn("degenerate_construction", { objectId: obj.id }));
            else resolved = { id: obj.id, label: obj.label, kind: "line", point: mid, direction: normalize2(dir), length: null };
          }
        } else if (c.kind === "angleBisector") {
          const a = c.aId ? getPoint(c.aId) : null;
          const b = c.bId ? getPoint(c.bId) : null;
          const cc = c.cId ? getPoint(c.cId) : null;
          if (a && b && cc) {
            const dir = angleBisectorDirection(b, a, cc, c.branch ?? 1);
            resolved = { id: obj.id, label: obj.label, kind: "line", point: b, direction: dir, length: null };
          }
        }
        if (resolved) { state.constructedLines.set(obj.id, resolved); state.order.push(obj.id); }
        else state.diagnostics.push(err("unresolved_reference", { objectId: obj.id }));
        break;
      }
      case "circle": {
        const c = obj.construction;
        if (c.kind === "centerRadius") {
          const center = getPoint(c.centerId);
          if (center) { state.circles.set(obj.id, { id: obj.id, label: obj.label, center, radius: c.radius }); state.order.push(obj.id); }
          else state.diagnostics.push(err("unresolved_reference", { objectId: obj.id }));
        } else if (c.kind === "centerPoint") {
          const center = getPoint(c.centerId);
          const point = getPoint(c.pointId);
          if (center && point) {
            const radius = distance2(center, point);
            if (radius < 1e-12) state.diagnostics.push(warn("degenerate_construction", { objectId: obj.id }));
            state.circles.set(obj.id, { id: obj.id, label: obj.label, center, radius });
            state.order.push(obj.id);
          } else state.diagnostics.push(err("unresolved_reference", { objectId: obj.id }));
        } else {
          const a = getPoint(c.aId), b = getPoint(c.bId), cc = getPoint(c.cId);
          if (a && b && cc) {
            const circum = circumcenter(a, b, cc);
            if (!circum) state.diagnostics.push(warn("collinear_points", { objectId: obj.id }));
            else {
              state.circles.set(obj.id, { id: obj.id, label: obj.label, center: circum.center, radius: circum.radius });
              state.order.push(obj.id);
            }
          } else state.diagnostics.push(err("unresolved_reference", { objectId: obj.id }));
        }
        break;
      }
      case "arc": {
        const center = getPoint(obj.centerId);
        const from = getPoint(obj.fromId);
        const to = getPoint(obj.toId);
        if (center && from && to) {
          const radius = distance2(center, from);
          const startAngle = Math.atan2(from.y - center.y, from.x - center.x);
          const endAngle = Math.atan2(to.y - center.y, to.x - center.x);
          state.arcs.set(obj.id, { id: obj.id, label: obj.label, center, radius, startAngle, endAngle });
          state.order.push(obj.id);
        } else state.diagnostics.push(err("unresolved_reference", { objectId: obj.id }));
        break;
      }
      case "polygon": {
        const points: Pt2[] = [];
        let ok = true;
        for (const pid of obj.pointIds) {
          const p = getPoint(pid);
          if (!p) { ok = false; break; }
          points.push(p);
        }
        if (ok) {
          state.polygons.set(obj.id, { id: obj.id, label: obj.label, points, closed: true });
          state.order.push(obj.id);
        } else state.diagnostics.push(err("unresolved_reference", { objectId: obj.id }));
        break;
      }
      case "regularPolygon": {
        const center = getPoint(obj.centerId);
        const vertex = getPoint(obj.vertexId);
        if (center && vertex) {
          const points: Pt2[] = [];
          const start = Math.atan2(vertex.y - center.y, vertex.x - center.x);
          const radius = distance2(center, vertex);
          for (let k = 0; k < obj.sides; k++) {
            const angle = start + (2 * Math.PI * k) / obj.sides;
            points.push({ x: center.x + radius * Math.cos(angle), y: center.y + radius * Math.sin(angle) });
          }
          state.polygons.set(obj.id, { id: obj.id, label: obj.label, points, closed: true });
          state.order.push(obj.id);
        } else state.diagnostics.push(err("unresolved_reference", { objectId: obj.id }));
        break;
      }
    }
  }

  for (const obj of objects) resolveOne(obj);
  for (const obj of objects) {
    if (!resolvedIds.has(obj.id)) state.diagnostics.push(err("unresolved_object", { objectId: obj.id }));
  }
  return {
    order: state.order,
    points: state.points,
    lines: state.lines,
    constructedLines: state.constructedLines,
    circles: state.circles,
    arcs: state.arcs,
    polygons: state.polygons,
    diagnostics: state.diagnostics,
  };
}

/** Convenience accessors used by measurement UI and tests. */
export function lineFromDefinition(def: Line2DDefinition, a: Pt2, b: Pt2): LinePrimitive {
  const direction = normalize2(sub2(b, a));
  return { point: a, direction: length2(direction) < 1e-15 ? { x: 1, y: 0 } : direction };
}

export function circleFromDefinition(def: Circle2DDefinition, resolve: (id: string) => Pt2 | null): CirclePrimitive | null {
  const c = def.construction;
  if (c.kind === "centerRadius") {
    const center = resolve(c.centerId);
    return center ? { center, radius: c.radius } : null;
  }
  if (c.kind === "centerPoint") {
    const center = resolve(c.centerId);
    const point = resolve(c.pointId);
    return center && point ? { center, radius: distance2(center, point) } : null;
  }
  const a = resolve(c.aId), b = resolve(c.bId), cc = resolve(c.cId);
  if (!a || !b || !cc) return null;
  const circum = circumcenter(a, b, cc);
  return circum ? { center: circum.center, radius: circum.radius } : null;
}

export function constructedLineFromDefinition(def: ConstructedLine2DDefinition, resolvePoint: (id: string) => Pt2 | null, resolveLine: (id: string) => LinePrimitive | null): LinePrimitive | null {
  const c = def.construction;
  if (c.kind === "parallel" || c.kind === "perpendicular") {
    const p = c.pointId ? resolvePoint(c.pointId) : null;
    const base = c.lineId ? resolveLine(c.lineId) : null;
    if (!p || !base) return null;
    return { point: p, direction: normalize2(c.kind === "parallel" ? base.direction : perpendicular2(base.direction)) };
  }
  if (c.kind === "perpendicularBisector") {
    const a = c.aId ? resolvePoint(c.aId) : null;
    const b = c.bId ? resolvePoint(c.bId) : null;
    if (!a || !b) return null;
    return { point: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, direction: normalize2(perpendicular2(sub2(b, a))) };
  }
  const a = c.aId ? resolvePoint(c.aId) : null;
  const b = c.bId ? resolvePoint(c.bId) : null;
  const cc = c.cId ? resolvePoint(c.cId) : null;
  if (!a || !b || !cc) return null;
  return { point: b, direction: angleBisectorDirection(b, a, cc, c.branch ?? 1) };
}

/** Deterministic debug/axis polyline for an arc (CCW from start to end). */
export function arcPoints(arc: ResolvedArc2D, segments: number): Pt2[] {
  const pts: Pt2[] = [];
  let sweep = arc.endAngle - arc.startAngle;
  while (sweep <= 0) sweep += Math.PI * 2;
  const n = Math.max(2, Math.min(256, segments));
  for (let i = 0; i <= n; i++) {
    const angle = arc.startAngle + (sweep * i) / n;
    pts.push({ x: arc.center.x + arc.radius * Math.cos(angle), y: arc.center.y + arc.radius * Math.sin(angle) });
  }
  return pts;
}

/**
 * Math workbench document lifecycle: creation, allowlist validation, command
 * reduction, dependency DAG, serialization and semantic fingerprinting.
 * Imported JSON is treated as `unknown` and fully revalidated — precompiled
 * ASTs, scene graphs or unknown fields never survive an import.
 */

import {
  BUILTIN_FUNCTIONS, RESERVED_VARIABLES, compileExpression, collectReferences,
  type ExprNode, type SymbolTable, type UserFunctionEntry,
} from "./expression.ts";
import { MATH_LIMITS, err, mathFail, mathOk, type MathDiagnostic, type MathResult } from "./diagnostics.ts";
import {
  DRAWING_MODES, type AnnotationDefinition, type Camera3DSettings, type GeometryDefinition2D,
  type MathCommand, type MathWorkbenchDocument, type NamedFunctionDefinition, type ParameterDefinition,
  type PlotDefinition2D, type PlotDefinition3D, type Render3DSettings, type StyleSettings, type View2D, isSemanticCommand,
} from "./model.ts";
import { resolveGeometry2D } from "./geometry2d.ts";
import type { ResolvedGeometry, ResolvedGeometry2D } from "./model.ts";

const ID_RE = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const NAME_RE = /^[a-zA-Z][a-zA-Z0-9_]{0,31}$/;
const COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const LABEL_MAX = 64;
const TEXT_MAX = 500;
const DOC_NAME_MAX = 100;

let idCounter = 0;

/** Object ids are unique within a document; a session counter suffices. */
export function newDefinitionId(prefix: string): string {
  idCounter += 1;
  return `${prefix}${idCounter.toString(36)}${Math.floor(Math.random() * 46656).toString(36)}`;
}

export function newDocumentId(): string {
  return `doc${Date.now().toString(36)}${Math.floor(Math.random() * 46656).toString(36)}`;
}

export const DEFAULT_VIEW_2D: View2D = { centerX: 0, centerY: 0, scale: 0.028, showGrid: true, snapToGrid: false, snapSize: 0.5 };
export const DEFAULT_CAMERA_3D: Camera3DSettings = { kind: "perspective", azimuth: Math.PI / 5, elevation: Math.PI / 6, distance: 12, target: { x: 0, y: 0, z: 0 } };
export const DEFAULT_RENDER_3D: Render3DSettings = { showGridXY: true, showGridXZ: false, showGridYZ: false, showAxes: true, edges: "visible" };
export const DEFAULT_STYLE: StyleSettings = { color: "#2f7d6e", width: 2, opacity: 1, dashed: false };

export function createMathDocument(name = "", mode: MathWorkbenchDocument["activeMode"] = "functions2d"): MathWorkbenchDocument {
  return {
    schemaVersion: 1,
    id: newDocumentId(),
    name,
    revision: 0,
    activeMode: mode,
    parameters: [],
    functions: [],
    objects2d: [],
    plots2d: [],
    plots3d: [],
    annotations: [],
    view2d: { ...DEFAULT_VIEW_2D },
    camera3d: { ...DEFAULT_CAMERA_3D, target: { ...DEFAULT_CAMERA_3D.target } },
    render3d: { ...DEFAULT_RENDER_3D },
  };
}

/* ------------------------------ field validators ------------------------------ */

function isObj(v: unknown): v is Record<string, unknown> { return typeof v === "object" && v !== null && !Array.isArray(v); }
function finite(v: unknown): v is number { return typeof v === "number" && Number.isFinite(v); }
function str(v: unknown, max: number): v is string { return typeof v === "string" && v.length <= max; }

function readStyle(raw: unknown): MathResult<StyleSettings> {
  if (!isObj(raw)) return mathFail([err("invalid_style")]);
  const { color, width, opacity, dashed } = raw;
  if (typeof color !== "string" || !COLOR_RE.test(color)) return mathFail([err("invalid_color", { args: { value: String(color).slice(0, 24) } })]);
  if (!finite(width) || width < 0.5 || width > 8) return mathFail([err("invalid_style", { args: { field: "width" } })]);
  if (!finite(opacity) || opacity < 0 || opacity > 1) return mathFail([err("invalid_style", { args: { field: "opacity" } })]);
  if (typeof dashed !== "boolean") return mathFail([err("invalid_style", { args: { field: "dashed" } })]);
  return mathOk({ color, width, opacity, dashed });
}

function readBase(raw: Record<string, unknown>): MathResult<{ id: string; label: string; visible: boolean; locked: boolean; style: StyleSettings }> {
  const diags: MathDiagnostic[] = [];
  const id = raw.id;
  if (typeof id !== "string" || !ID_RE.test(id)) return mathFail([err("invalid_id", { args: { value: String(id).slice(0, 24) } })]);
  const label = raw.label;
  if (typeof label !== "string" || label.length === 0 || label.length > LABEL_MAX) return mathFail([err("invalid_label", { objectId: id })]);
  const visible = raw.visible;
  const locked = raw.locked;
  if (typeof visible !== "boolean" || typeof locked !== "boolean") return mathFail([err("invalid_flag", { objectId: id })]);
  const style = readStyle(raw.style);
  if (!style.ok) return mathFail(style.diagnostics.map((d) => ({ ...d, objectId: id })));
  diags.push(...style.diagnostics);
  return mathOk({ id, label, visible, locked, style: style.value });
}

function refId(raw: unknown, field: string, objectId: string): MathResult<string> {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 64) return mathFail([err("invalid_reference", { objectId, args: { field } })]);
  return mathOk(raw);
}

function readDomain(raw: unknown, field: string): MathResult<{ min: number; max: number }> {
  if (!isObj(raw)) return mathFail([err("invalid_domain", { args: { field } })]);
  const min = raw.min, max = raw.max;
  if (!finite(min) || !finite(max) || !(min < max)) return mathFail([err("invalid_domain", { args: { field } })]);
  if (max - min > 1e7) return mathFail([err("domain_too_wide", { args: { field } })]);
  return mathOk({ min, max });
}

const KINDS_2D = new Set(["line", "constructedLine", "circle", "arc", "polygon", "regularPolygon"]);
const LINE_KINDS = new Set(["line", "segment", "ray"]);
const CONSTRUCTED_KINDS = new Set(["parallel", "perpendicular", "perpendicularBisector", "angleBisector"]);
const CIRCLE_KINDS = new Set(["centerRadius", "centerPoint", "threePoints"]);
const QUALITIES = new Set(["low", "normal", "high"]);
const CAMERA_KINDS = new Set(["perspective", "orthographic"]);
const EDGE_PRESETS = new Set(["visible", "hidden", "off"]);

function validateObject2D(raw: unknown): MathResult<GeometryDefinition2D> {
  if (!isObj(raw)) return mathFail([err("invalid_object")]);
  const base = readBase(raw);
  if (!base.ok) return base;
  const { id, label, visible, locked, style } = base.value;
  const kind2d = raw.kind2d;
  const common = { id, label, visible, locked, style };
  if (kind2d === "point") {
    const construction = raw.construction;
    if (!isObj(construction)) return mathFail([err("invalid_construction", { objectId: id })]);
    if (construction.kind === "free") {
      if (!finite(construction.x) || !finite(construction.y)) return mathFail([err("invalid_coordinates", { objectId: id })]);
      return mathOk({ ...common, kind2d: "point" as const, construction: { kind: "free" as const, x: construction.x, y: construction.y } });
    }
    if (construction.kind === "midpoint") {
      const a = refId(construction.aId, "aId", id); if (!a.ok) return a;
      const b = refId(construction.bId, "bId", id); if (!b.ok) return b;
      return mathOk({ ...common, kind2d: "point" as const, construction: { kind: "midpoint" as const, aId: a.value, bId: b.value } });
    }
    if (construction.kind === "intersection") {
      const a = refId(construction.aId, "aId", id); if (!a.ok) return a;
      const b = refId(construction.bId, "bId", id); if (!b.ok) return b;
      const branch = construction.branch === 2 ? 2 : 1;
      let branchAnchor: { x: number; y: number } | undefined;
      if (isObj(construction.branchAnchor)) {
        if (finite(construction.branchAnchor.x) && finite(construction.branchAnchor.y)) {
          branchAnchor = { x: construction.branchAnchor.x, y: construction.branchAnchor.y };
        }
      }
      return mathOk({ ...common, kind2d: "point" as const, construction: { kind: "intersection" as const, aId: a.value, bId: b.value, branch, ...(branchAnchor ? { branchAnchor } : {}) } });
    }
    return mathFail([err("invalid_construction", { objectId: id })]);
  }
  if (kind2d === "line") {
    const a = refId(raw.aId, "aId", id); if (!a.ok) return a;
    const b = refId(raw.bId, "bId", id); if (!b.ok) return b;
    if (raw.lineKind !== "line" && raw.lineKind !== "segment" && raw.lineKind !== "ray") return mathFail([err("invalid_line_kind", { objectId: id })]);
    return mathOk({ ...common, kind2d: "line" as const, lineKind: raw.lineKind, aId: a.value, bId: b.value });
  }
  if (kind2d === "constructedLine") {
    const construction = raw.construction;
    if (!isObj(construction) || typeof construction.kind !== "string" || !CONSTRUCTED_KINDS.has(construction.kind)) {
      return mathFail([err("invalid_construction", { objectId: id })]);
    }
    const kind = construction.kind as "parallel" | "perpendicular" | "perpendicularBisector" | "angleBisector";
    const readOpt = (field: string): MathResult<string | undefined> => {
      const v = construction[field];
      if (v === undefined) return mathOk(undefined);
      return refId(v, field, id);
    };
    const pointId = readOpt("pointId"); if (!pointId.ok) return pointId;
    const lineId = readOpt("lineId"); if (!lineId.ok) return lineId;
    const aId = readOpt("aId"); if (!aId.ok) return aId;
    const bId = readOpt("bId"); if (!bId.ok) return bId;
    const cId = readOpt("cId"); if (!cId.ok) return cId;
    const branch = construction.branch === 2 ? 2 : 1;
    const built: Record<string, unknown> = { kind };
    if (pointId.value !== undefined) built.pointId = pointId.value;
    if (lineId.value !== undefined) built.lineId = lineId.value;
    if (aId.value !== undefined) built.aId = aId.value;
    if (bId.value !== undefined) built.bId = bId.value;
    if (cId.value !== undefined) built.cId = cId.value;
    if (kind !== "angleBisector") delete built.branch;
    else built.branch = branch;
    return mathOk({ ...common, kind2d: "constructedLine" as const, construction: built as never });
  }
  if (kind2d === "circle") {
    const construction = raw.construction;
    if (!isObj(construction) || typeof construction.kind !== "string" || !CIRCLE_KINDS.has(construction.kind)) {
      return mathFail([err("invalid_construction", { objectId: id })]);
    }
    if (construction.kind === "centerRadius") {
      const center = refId(construction.centerId, "centerId", id); if (!center.ok) return center;
      if (!finite(construction.radius) || construction.radius <= 0 || construction.radius > 1e6) return mathFail([err("invalid_radius", { objectId: id })]);
      return mathOk({ ...common, kind2d: "circle" as const, construction: { kind: "centerRadius" as const, centerId: center.value, radius: construction.radius } });
    }
    if (construction.kind === "centerPoint") {
      const center = refId(construction.centerId, "centerId", id); if (!center.ok) return center;
      const point = refId(construction.pointId, "pointId", id); if (!point.ok) return point;
      return mathOk({ ...common, kind2d: "circle" as const, construction: { kind: "centerPoint" as const, centerId: center.value, pointId: point.value } });
    }
    const a = refId(construction.aId, "aId", id); if (!a.ok) return a;
    const b = refId(construction.bId, "bId", id); if (!b.ok) return b;
    const c = refId(construction.cId, "cId", id); if (!c.ok) return c;
    return mathOk({ ...common, kind2d: "circle" as const, construction: { kind: "threePoints" as const, aId: a.value, bId: b.value, cId: c.value } });
  }
  if (kind2d === "arc") {
    const center = refId(raw.centerId, "centerId", id); if (!center.ok) return center;
    const from = refId(raw.fromId, "fromId", id); if (!from.ok) return from;
    const to = refId(raw.toId, "toId", id); if (!to.ok) return to;
    return mathOk({ ...common, kind2d: "arc" as const, centerId: center.value, fromId: from.value, toId: to.value });
  }
  if (kind2d === "polygon") {
    if (!Array.isArray(raw.pointIds) || raw.pointIds.length < 3 || raw.pointIds.length > 64) return mathFail([err("invalid_polygon", { objectId: id })]);
    const ids: string[] = [];
    for (const p of raw.pointIds) { const r = refId(p, "pointIds", id); if (!r.ok) return r; ids.push(r.value); }
    return mathOk({ ...common, kind2d: "polygon" as const, pointIds: ids });
  }
  if (kind2d === "regularPolygon") {
    const center = refId(raw.centerId, "centerId", id); if (!center.ok) return center;
    const vertex = refId(raw.vertexId, "vertexId", id); if (!vertex.ok) return vertex;
    if (!finite(raw.sides) || !Number.isInteger(raw.sides) || raw.sides < 3 || raw.sides > 64) return mathFail([err("invalid_polygon", { objectId: id })]);
    return mathOk({ ...common, kind2d: "regularPolygon" as const, centerId: center.value, vertexId: vertex.value, sides: raw.sides });
  }
  if (typeof kind2d === "string" && KINDS_2D.has(kind2d)) return mathFail([err("invalid_construction", { objectId: id })]);
  return mathFail([err("unknown_object_kind", { args: { kind: String(kind2d).slice(0, 24) } })]);
}

/* --------------------------- expressions & symbols --------------------------- */

export interface EvaluationContext {
  parameters: Readonly<Record<string, number>>;
  functions: ReadonlyMap<string, UserFunctionEntry>;
  diagnostics: MathDiagnostic[];
}

/** Axis variables each plot kind treats as free input. */
export function axisVariables(plot: { kind: string }): readonly string[] {
  switch (plot.kind) {
    case "explicit": return ["x"];
    case "inverse": return ["y"];
    case "parametric": return ["t"];
    case "polar": return ["theta"];
    case "implicit": return ["x", "y"];
    case "explicitSurface": return ["x", "y"];
    case "parametricSurface": return ["u", "v"];
    case "implicitSurface": return ["x", "y", "z"];
    default: return ["t"];
  }
}

function compileFunctionBodies(functions: readonly NamedFunctionDefinition[], parameters: readonly ParameterDefinition[]): MathResult<ReadonlyMap<string, UserFunctionEntry>> {
  const paramNames = new Set(parameters.map((p) => p.name));
  // Pass 1: parse every body with the FULL declared function set so forward
  // references validate; arity comes from the declaration, not resolution order.
  const declared = new Map(functions.map((fn) => [fn.name, { arity: fn.params.length, params: fn.params, body: null as unknown as ExprNode }]));
  const bodies = new Map<string, ExprNode>();
  const references = new Map<string, Set<string>>();
  for (const fn of functions) {
    const localSymbols: SymbolTable = { variables: [...fn.params, ...paramNames], functions: declared };
    const body = compileExpression(fn.expression, localSymbols);
    if (!body.ok) return mathFail(body.diagnostics.map((d) => ({ ...d, objectId: fn.id })));
    bodies.set(fn.name, body.value.ast);
    const refs = { variables: new Set<string>(), functions: new Set<string>() };
    collectReferences(body.value.ast, refs);
    references.set(fn.name, refs.functions);
  }
  // Pass 2: topological order; recursion (self or mutual) is a cycle.
  const done = new Map<string, UserFunctionEntry>();
  const state = new Map<string, 0 | 1 | 2>(functions.map((fn) => [fn.name, 0 as const]));
  const order: string[] = [];
  const visit = (name: string): boolean => {
    const s = state.get(name) ?? 2;
    if (s === 1) return false;
    if (s === 2) return true;
    state.set(name, 1);
    for (const dep of references.get(name) ?? []) if (!visit(dep)) return false;
    state.set(name, 2);
    order.push(name);
    return true;
  };
  for (const fn of functions) if (!visit(fn.name)) return mathFail([err("function_dependency_cycle", { args: { names: fn.name } })]);
  for (const name of order) {
    const fn = functions.find((f) => f.name === name);
    const body = bodies.get(name);
    if (!fn || !body) return mathFail([err("internal_error")]);
    done.set(name, { arity: fn.params.length, params: fn.params, body });
  }
  return mathOk(done);
}

export function buildEvaluationContext(doc: MathWorkbenchDocument): MathResult<EvaluationContext> {
  const parameters: Record<string, number> = {};
  for (const p of doc.parameters) parameters[p.name] = p.value;
  const functions = compileFunctionBodies(doc.functions, doc.parameters);
  if (!functions.ok) return functions;
  return mathOk({ parameters, functions: functions.value, diagnostics: [] });
}

/* ------------------------------ plot validation ------------------------------ */

interface PlotCommon { id: string; label: string; visible: boolean; style: StyleSettings }

function readPlotCommon(raw: Record<string, unknown>): MathResult<PlotCommon> {
  // Plots have no `locked` flag (only construction objects do).
  const diags: MathDiagnostic[] = [];
  const id = raw.id;
  if (typeof id !== "string" || !ID_RE.test(id)) return mathFail([err("invalid_id", { args: { value: String(id).slice(0, 24) } })]);
  const label = raw.label;
  if (typeof label !== "string" || label.length === 0 || label.length > LABEL_MAX) return mathFail([err("invalid_label", { objectId: id })]);
  const visible = raw.visible;
  if (typeof visible !== "boolean") return mathFail([err("invalid_flag", { objectId: id })]);
  const style = readStyle(raw.style);
  if (!style.ok) return mathFail(style.diagnostics.map((d) => ({ ...d, objectId: id })));
  diags.push(...style.diagnostics);
  return mathOk({ id, label, visible, style: style.value });
}

function compilePlotExpression(source: unknown, axes: readonly string[], ctx: EvaluationContext, id: string): MathResult<{ expression: string; ast: ExprNode }> {
  if (typeof source !== "string" || source.length === 0 || source.length > MATH_LIMITS.maxExpressionChars) {
    return mathFail([err("invalid_expression", { objectId: id })]);
  }
  const compiled = compileExpression(source, { variables: [...axes, ...Object.keys(ctx.parameters)], functions: ctx.functions });
  if (!compiled.ok) return mathFail(compiled.diagnostics.map((d) => ({ ...d, objectId: id })));
  return mathOk({ expression: compiled.value.source, ast: compiled.value.ast });
}

function validatePlot2D(raw: unknown, ctx: EvaluationContext): MathResult<PlotDefinition2D> {
  if (!isObj(raw)) return mathFail([err("invalid_plot")]);
  const common = readPlotCommon(raw);
  if (!common.ok) return common;
  const { id, label, visible, style } = common.value;
  const wrap = { id, label, visible, style };
  const kind = raw.kind;
  switch (kind) {
    case "explicit": case "inverse": {
      const expr = compilePlotExpression(raw.expression, axisVariables({ kind }), ctx, id);
      if (!expr.ok) return expr;
      let domain: { min: number; max: number } | undefined;
      if (raw[`${kind === "explicit" ? "x" : "y"}Domain`] !== undefined) {
        const d = readDomain(raw[`${kind === "explicit" ? "x" : "y"}Domain`], `${kind}Domain`);
        if (!d.ok) return mathFail(d.diagnostics.map((x) => ({ ...x, objectId: id })));
        domain = d.value;
      }
      return kind === "explicit"
        ? mathOk({ ...wrap, kind: "explicit" as const, expression: expr.value.expression, ...(domain ? { xDomain: domain } : {}) })
        : mathOk({ ...wrap, kind: "inverse" as const, expression: expr.value.expression, ...(domain ? { yDomain: domain } : {}) });
    }
    case "parametric": {
      const x = compilePlotExpression(raw.xExpression, ["t"], ctx, id); if (!x.ok) return x;
      const y = compilePlotExpression(raw.yExpression, ["t"], ctx, id); if (!y.ok) return y;
      const d = readDomain(raw.tDomain, "tDomain"); if (!d.ok) return mathFail(d.diagnostics.map((x2) => ({ ...x2, objectId: id })));
      return mathOk({ ...wrap, kind: "parametric" as const, xExpression: x.value.expression, yExpression: y.value.expression, tDomain: d.value });
    }
    case "polar": {
      const r = compilePlotExpression(raw.rExpression, ["theta"], ctx, id); if (!r.ok) return r;
      const d = readDomain(raw.thetaDomain, "thetaDomain"); if (!d.ok) return mathFail(d.diagnostics.map((x2) => ({ ...x2, objectId: id })));
      return mathOk({ ...wrap, kind: "polar" as const, rExpression: r.value.expression, thetaDomain: d.value });
    }
    case "implicit": {
      const expr = compilePlotExpression(raw.expression, ["x", "y"], ctx, id); if (!expr.ok) return expr;
      if (!finite(raw.level)) return mathFail([err("invalid_level", { objectId: id })]);
      const dom = isObj(raw.domain) ? raw.domain : {};
      const dx = readDomain(dom.x, "domain.x"); if (!dx.ok) return mathFail(dx.diagnostics.map((x2) => ({ ...x2, objectId: id })));
      const dy = readDomain(dom.y, "domain.y"); if (!dy.ok) return mathFail(dy.diagnostics.map((x2) => ({ ...x2, objectId: id })));
      return mathOk({ ...wrap, kind: "implicit" as const, expression: expr.value.expression, level: raw.level, domain: { x: dx.value, y: dy.value } });
    }
    default:
      return mathFail([err("unknown_plot_kind", { args: { kind: String(kind).slice(0, 24) } })]);
  }
}

function validatePlot3D(raw: unknown, ctx: EvaluationContext): MathResult<PlotDefinition3D> {
  if (!isObj(raw)) return mathFail([err("invalid_plot")]);
  const common = readPlotCommon(raw);
  if (!common.ok) return common;
  const { id, label, visible, style } = common.value;
  const wrap = { id, label, visible, style };
  const quality = (v: unknown): "low" | "normal" | "high" | null =>
    v === "low" || v === "normal" || v === "high" ? v : null;
  const opacity = (v: unknown): MathResult<number> => {
    if (!finite(v) || v < 0 || v > 1) return mathFail([err("invalid_style", { objectId: id, args: { field: "opacity" } })]);
    return mathOk(v);
  };
  switch (raw.kind) {
    case "explicitSurface": {
      const expr = compilePlotExpression(raw.expression, ["x", "y"], ctx, id); if (!expr.ok) return expr;
      const dx = readDomain(raw.xDomain, "xDomain"); if (!dx.ok) return mathFail(dx.diagnostics.map((d) => ({ ...d, objectId: id })));
      const dy = readDomain(raw.yDomain, "yDomain"); if (!dy.ok) return mathFail(dy.diagnostics.map((d) => ({ ...d, objectId: id })));
      const q = quality(raw.quality); if (!q) return mathFail([err("invalid_quality", { objectId: id })]);
      const op = opacity(raw.opacity); if (!op.ok) return op;
      return mathOk({ ...wrap, kind: "explicitSurface" as const, expression: expr.value.expression, xDomain: dx.value, yDomain: dy.value, quality: q, opacity: op.value, showGrid: raw.showGrid === true });
    }
    case "parametricSurface": {
      const x = compilePlotExpression(raw.xExpression, ["u", "v"], ctx, id); if (!x.ok) return x;
      const y = compilePlotExpression(raw.yExpression, ["u", "v"], ctx, id); if (!y.ok) return y;
      const z = compilePlotExpression(raw.zExpression, ["u", "v"], ctx, id); if (!z.ok) return z;
      const du = readDomain(raw.uDomain, "uDomain"); if (!du.ok) return mathFail(du.diagnostics.map((d) => ({ ...d, objectId: id })));
      const dv = readDomain(raw.vDomain, "vDomain"); if (!dv.ok) return mathFail(dv.diagnostics.map((d) => ({ ...d, objectId: id })));
      const q = quality(raw.quality); if (!q) return mathFail([err("invalid_quality", { objectId: id })]);
      const op = opacity(raw.opacity); if (!op.ok) return op;
      return mathOk({
        ...wrap, kind: "parametricSurface" as const,
        xExpression: x.value.expression, yExpression: y.value.expression, zExpression: z.value.expression,
        uDomain: du.value, vDomain: dv.value, wrapU: raw.wrapU === true, wrapV: raw.wrapV === true,
        quality: q, opacity: op.value, showGrid: raw.showGrid === true,
      });
    }
    case "implicitSurface": {
      const expr = compilePlotExpression(raw.expression, ["x", "y", "z"], ctx, id); if (!expr.ok) return expr;
      if (!finite(raw.iso)) return mathFail([err("invalid_level", { objectId: id })]);
      const box = isObj(raw.box) ? raw.box : {};
      const dx = readDomain(box.x, "box.x"); if (!dx.ok) return mathFail(dx.diagnostics.map((d) => ({ ...d, objectId: id })));
      const dy = readDomain(box.y, "box.y"); if (!dy.ok) return mathFail(dy.diagnostics.map((d) => ({ ...d, objectId: id })));
      const dz = readDomain(box.z, "box.z"); if (!dz.ok) return mathFail(dz.diagnostics.map((d) => ({ ...d, objectId: id })));
      if (!Number.isInteger(raw.resolution) || (raw.resolution as number) < 6 || (raw.resolution as number) > MATH_LIMITS.maxImplicitGrid3d) {
        return mathFail([err("invalid_resolution", { objectId: id })]);
      }
      const op = opacity(raw.opacity); if (!op.ok) return op;
      return mathOk({ ...wrap, kind: "implicitSurface" as const, expression: expr.value.expression, iso: raw.iso, box: { x: dx.value, y: dy.value, z: dz.value }, resolution: raw.resolution as number, opacity: op.value });
    }
    case "parametricCurve": {
      const x = compilePlotExpression(raw.xExpression, ["t"], ctx, id); if (!x.ok) return x;
      const y = compilePlotExpression(raw.yExpression, ["t"], ctx, id); if (!y.ok) return y;
      const z = compilePlotExpression(raw.zExpression, ["t"], ctx, id); if (!z.ok) return z;
      const dt = readDomain(raw.tDomain, "tDomain"); if (!dt.ok) return mathFail(dt.diagnostics.map((d) => ({ ...d, objectId: id })));
      if (!Number.isInteger(raw.samples) || (raw.samples as number) < 16 || (raw.samples as number) > MATH_LIMITS.maxSamples2d) {
        return mathFail([err("invalid_samples", { objectId: id })]);
      }
      return mathOk({ ...wrap, kind: "parametricCurve" as const, xExpression: x.value.expression, yExpression: y.value.expression, zExpression: z.value.expression, tDomain: dt.value, samples: raw.samples as number });
    }
    default:
      return mathFail([err("unknown_plot_kind", { args: { kind: String(raw.kind).slice(0, 24) } })]);
  }
}

/* ---------------------------- document validation ---------------------------- */

export function validateMathDocument(raw: unknown): MathResult<MathWorkbenchDocument> {
  if (!isObj(raw)) return mathFail([err("invalid_document")]);
  const diagnostics: MathDiagnostic[] = [];
  if (raw.schemaVersion !== 1) return mathFail([err("unsupported_schema_version", { args: { version: String(raw.schemaVersion) } })]);
  const id = raw.id;
  if (typeof id !== "string" || !ID_RE.test(id)) return mathFail([err("invalid_id")]);
  if (!str(raw.name, DOC_NAME_MAX) || typeof raw.name !== "string") return mathFail([err("invalid_name")]);
  if (!finite(raw.revision) || !Number.isInteger(raw.revision) || raw.revision < 0 || raw.revision > 1e9) return mathFail([err("invalid_revision")]);
  // Legacy "geometry3d" documents (solid-geometry mode removed in ADR-0022)
  // migrate to "functions3d"; their construction objects were already dropped.
  const rawMode = raw.activeMode === "geometry3d" ? "functions3d" : raw.activeMode;
  if (typeof rawMode !== "string" || !DRAWING_MODES.includes(rawMode as never)) return mathFail([err("invalid_mode")]);
  const activeMode = rawMode as MathWorkbenchDocument["activeMode"];

  // parameters
  if (!Array.isArray(raw.parameters) || raw.parameters.length > MATH_LIMITS.maxParameters) return mathFail([err("limit_parameters_exceeded")]);
  const parameters: ParameterDefinition[] = [];
  const paramNames = new Set<string>();
  for (const p of raw.parameters) {
    if (!isObj(p)) return mathFail([err("invalid_parameter")]);
    if (typeof p.id !== "string" || !ID_RE.test(p.id)) return mathFail([err("invalid_id")]);
    if (typeof p.name !== "string" || !NAME_RE.test(p.name) || RESERVED_VARIABLES.includes(p.name) || Object.prototype.hasOwnProperty.call(BUILTIN_FUNCTIONS, p.name) || p.name === "pi" || p.name === "e" || p.name === "tau") {
      return mathFail([err("invalid_parameter_name", { args: { name: String(p.name).slice(0, 24) } })]);
    }
    if (paramNames.has(p.name)) return mathFail([err("duplicate_parameter_name", { args: { name: p.name } })]);
    paramNames.add(p.name);
    if (!finite(p.value) || !finite(p.min) || !finite(p.max) || !finite(p.step) || p.min >= p.max || p.step <= 0) {
      return mathFail([err("invalid_parameter", { args: { name: p.name } })]);
    }
    parameters.push({ id: p.id, name: p.name, value: p.value, min: p.min, max: p.max, step: p.step });
  }

  // functions
  if (!Array.isArray(raw.functions) || raw.functions.length > MATH_LIMITS.maxNamedFunctions) return mathFail([err("limit_functions_exceeded")]);
  const functions: NamedFunctionDefinition[] = [];
  const fnIds = new Set<string>();
  const fnNames = new Set<string>();
  for (const fn of raw.functions) {
    if (!isObj(fn)) return mathFail([err("invalid_function")]);
    if (typeof fn.id !== "string" || !ID_RE.test(fn.id) || fnIds.has(fn.id)) return mathFail([err("invalid_id")]);
    if (typeof fn.name !== "string" || !NAME_RE.test(fn.name) || Object.prototype.hasOwnProperty.call(BUILTIN_FUNCTIONS, fn.name) || RESERVED_VARIABLES.includes(fn.name) || paramNames.has(fn.name)) {
      return mathFail([err("invalid_function_name", { args: { name: String(fn.name).slice(0, 24) } })]);
    }
    if (fnNames.has(fn.name)) return mathFail([err("duplicate_function_name", { args: { name: fn.name } })]);
    if (!Array.isArray(fn.params) || fn.params.length < 1 || fn.params.length > 4) return mathFail([err("invalid_function", { args: { name: fn.name } })]);
    const params: string[] = [];
    for (const prm of fn.params) {
      if (typeof prm !== "string" || !NAME_RE.test(prm) || Object.prototype.hasOwnProperty.call(BUILTIN_FUNCTIONS, prm) || prm === "pi" || prm === "e" || prm === "tau" || paramNames.has(prm) || params.includes(prm)) {
        return mathFail([err("invalid_function", { args: { name: fn.name } })]);
      }
      params.push(prm);
    }
    if (typeof fn.expression !== "string" || fn.expression.length === 0 || fn.expression.length > MATH_LIMITS.maxExpressionChars) {
      return mathFail([err("invalid_expression")]);
    }
    fnIds.add(fn.id);
    fnNames.add(fn.name);
    functions.push({ id: fn.id, name: fn.name, params, expression: fn.expression });
  }

  const ctx = compileFunctionBodies(functions, parameters);
  if (!ctx.ok) return ctx;
  const evaluationCtx: EvaluationContext = { parameters: Object.fromEntries(parameters.map((p) => [p.name, p.value])), functions: ctx.value, diagnostics: [] };

  // objects 2d
  if (!Array.isArray(raw.objects2d) || raw.objects2d.length > MATH_LIMITS.maxObjects2d) return mathFail([err("limit_objects2d_exceeded")]);
  const objects2d: GeometryDefinition2D[] = [];
  const ids2d = new Set<string>();
  for (const o of raw.objects2d) {
    const v = validateObject2D(o);
    if (!v.ok) return v;
    if (ids2d.has(v.value.id)) return mathFail([err("duplicate_id", { args: { id: v.value.id } })]);
    ids2d.add(v.value.id);
    objects2d.push(v.value);
  }
  // objects 3d: construction objects are gone (ADR-0022); legacy payloads
  // may still carry `objects3d` — it is accepted and silently dropped here.
  // reference existence + DAG cycles (2d)
  const depCheck2d = checkReferences2D(objects2d);
  if (!depCheck2d.ok) return depCheck2d;

  // plots
  if (!Array.isArray(raw.plots2d) || raw.plots2d.length > MATH_LIMITS.maxPlots2d) return mathFail([err("limit_plots2d_exceeded")]);
  const plots2d: PlotDefinition2D[] = [];
  const plotIds2d = new Set<string>();
  for (const p of raw.plots2d) {
    const v = validatePlot2D(p, evaluationCtx);
    if (!v.ok) return v;
    if (plotIds2d.has(v.value.id)) return mathFail([err("duplicate_id", { args: { id: v.value.id } })]);
    plotIds2d.add(v.value.id);
    plots2d.push(v.value);
  }
  if (!Array.isArray(raw.plots3d) || raw.plots3d.length > MATH_LIMITS.maxPlots3d) return mathFail([err("limit_plots3d_exceeded")]);
  const plots3d: PlotDefinition3D[] = [];
  const plotIds3d = new Set<string>();
  for (const p of raw.plots3d) {
    const v = validatePlot3D(p, evaluationCtx);
    if (!v.ok) return v;
    if (plotIds3d.has(v.value.id)) return mathFail([err("duplicate_id", { args: { id: v.value.id } })]);
    plotIds3d.add(v.value.id);
    plots3d.push(v.value);
  }

  // annotations
  if (!Array.isArray(raw.annotations) || raw.annotations.length > 200) return mathFail([err("limit_annotations_exceeded")]);
  const annotations: AnnotationDefinition[] = [];
  const annIds = new Set<string>();
  for (const a of raw.annotations) {
    if (!isObj(a)) return mathFail([err("invalid_annotation")]);
    if (typeof a.id !== "string" || !ID_RE.test(a.id) || annIds.has(a.id)) return mathFail([err("invalid_id")]);
    if (typeof a.text !== "string" || a.text.length === 0 || a.text.length > TEXT_MAX) return mathFail([err("invalid_annotation")]);
    if (!finite(a.x) || !finite(a.y)) return mathFail([err("invalid_annotation")]);
    if (a.space !== "2d" && a.space !== "3d") return mathFail([err("invalid_annotation")]);
    if (a.space === "3d" && a.z !== undefined && !finite(a.z)) return mathFail([err("invalid_annotation")]);
    annIds.add(a.id);
    if (a.space === "3d") {
      const ann: AnnotationDefinition = { id: a.id, text: a.text, x: a.x, y: a.y, visible: a.visible !== false, space: "3d" };
      if (finite(a.z)) ann.z = a.z;
      annotations.push(ann);
    } else {
      annotations.push({ id: a.id, text: a.text, x: a.x, y: a.y, visible: a.visible !== false, space: "2d" });
    }
  }

  // view / camera / render
  const v2 = raw.view2d;
  if (!isObj(v2) || !finite(v2.centerX) || !finite(v2.centerY) || !finite(v2.scale) || v2.scale <= 0 || v2.scale > 1e3
    || typeof v2.showGrid !== "boolean" || typeof v2.snapToGrid !== "boolean" || !finite(v2.snapSize) || v2.snapSize <= 0) {
    return mathFail([err("invalid_view")]);
  }
  const view2d: View2D = { centerX: v2.centerX, centerY: v2.centerY, scale: v2.scale, showGrid: v2.showGrid, snapToGrid: v2.snapToGrid, snapSize: v2.snapSize };
  const c3 = raw.camera3d;
  if (!isObj(c3) || !CAMERA_KINDS.has(c3.kind as string) || !finite(c3.azimuth) || !finite(c3.elevation) || !finite(c3.distance) || c3.distance <= 0 || c3.distance > 1e5
    || !isObj(c3.target) || !finite(c3.target.x) || !finite(c3.target.y) || !finite(c3.target.z)) {
    return mathFail([err("invalid_camera")]);
  }
  const camera3d: Camera3DSettings = { kind: c3.kind as Camera3DSettings["kind"], azimuth: c3.azimuth, elevation: c3.elevation, distance: c3.distance, target: { x: c3.target.x, y: c3.target.y, z: c3.target.z } };
  const r3 = raw.render3d;
  if (!isObj(r3) || typeof r3.showGridXY !== "boolean" || typeof r3.showGridXZ !== "boolean" || typeof r3.showGridYZ !== "boolean" || typeof r3.showAxes !== "boolean" || !EDGE_PRESETS.has(r3.edges as string)) {
    return mathFail([err("invalid_render_settings")]);
  }
  const render3d: Render3DSettings = { showGridXY: r3.showGridXY, showGridXZ: r3.showGridXZ, showGridYZ: r3.showGridYZ, showAxes: r3.showAxes, edges: r3.edges as Render3DSettings["edges"] };

  return mathOk({
    schemaVersion: 1, id, name: raw.name, revision: raw.revision, activeMode,
    parameters, functions, objects2d, plots2d, plots3d, annotations,
    view2d, camera3d, render3d,
  }, diagnostics);
}

/* ------------------------------ dependency edges ------------------------------ */

export function objectReferences2D(obj: GeometryDefinition2D): readonly string[] {
  const out: string[] = [];
  switch (obj.kind2d) {
    case "point":
      if (obj.construction.kind === "midpoint") out.push(obj.construction.aId, obj.construction.bId);
      else if (obj.construction.kind === "intersection") out.push(obj.construction.aId, obj.construction.bId);
      break;
    case "line": out.push(obj.aId, obj.bId); break;
    case "constructedLine":
      for (const v of [obj.construction.pointId, obj.construction.lineId, obj.construction.aId, obj.construction.bId, obj.construction.cId]) if (v) out.push(v);
      break;
    case "circle":
      if (obj.construction.kind === "centerRadius") out.push(obj.construction.centerId);
      else if (obj.construction.kind === "centerPoint") out.push(obj.construction.centerId, obj.construction.pointId);
      else out.push(obj.construction.aId, obj.construction.bId, obj.construction.cId);
      break;
    case "arc": out.push(obj.centerId, obj.fromId, obj.toId); break;
    case "polygon": out.push(...obj.pointIds); break;
    case "regularPolygon": out.push(obj.centerId, obj.vertexId); break;
  }
  return out;
}

function checkReferences2D(objects: readonly GeometryDefinition2D[]): MathResult<void> {
  const byId = new Set(objects.map((o) => o.id));
  const pointIds = new Set(objects.filter((o) => o.kind2d === "point").map((o) => o.id));
  const lineLikeIds = new Set(objects.filter((o) => o.kind2d === "line" || o.kind2d === "constructedLine").map((o) => o.id));
  const allIds = new Set(objects.map((o) => o.id));
  for (const obj of objects) {
    for (const ref of objectReferences2D(obj)) {
      if (!allIds.has(ref)) return mathFail([err("dangling_reference", { objectId: obj.id, args: { ref } })]);
    }
    // Kind-aware expectations: constructions only accept point inputs except
    // intersection (line/circle) and parallel/perpendicular (line).
    if (obj.kind2d === "point") continue;
    if (obj.kind2d === "constructedLine" && (obj.construction.kind === "parallel" || obj.construction.kind === "perpendicular")) {
      if (obj.construction.lineId && !lineLikeIds.has(obj.construction.lineId)) return mathFail([err("reference_kind_mismatch", { objectId: obj.id })]);
      if (obj.construction.pointId && !pointIds.has(obj.construction.pointId)) return mathFail([err("reference_kind_mismatch", { objectId: obj.id })]);
      continue;
    }
    for (const ref of objectReferences2D(obj)) {
      if (!pointIds.has(ref)) return mathFail([err("reference_kind_mismatch", { objectId: obj.id, args: { ref } })]);
    }
  }
  // Cycle check (imports could contain one even though commands prevent it).
  const color = new Map<string, 0 | 1 | 2>();
  const edges = new Map(objects.map((o) => [o.id, objectReferences2D(o).filter((r) => byId.has(r))]));
  const visit = (id: string): boolean => {
    const state = color.get(id) ?? 0;
    if (state === 1) return false;
    if (state === 2) return true;
    color.set(id, 1);
    for (const next of edges.get(id) ?? []) if (!visit(next)) return false;
    color.set(id, 2);
    return true;
  };
  for (const o of objects) if (!visit(o.id)) return mathFail([err("dependency_cycle", { objectId: o.id })]);
  return mathOk(undefined);
}

/* --------------------------------- commands --------------------------------- */

function cloneDocument(doc: MathWorkbenchDocument): MathWorkbenchDocument {
  return {
    ...doc,
    parameters: doc.parameters.map((p) => ({ ...p })),
    functions: doc.functions.map((f) => ({ ...f, params: [...f.params] })),
    objects2d: doc.objects2d.map((o) => ({ ...o })),
    plots2d: doc.plots2d.map((p) => ({ ...p })),
    plots3d: doc.plots3d.map((p) => ({ ...p })),
    annotations: doc.annotations.map((a) => ({ ...a })),
    view2d: { ...doc.view2d },
    camera3d: { ...doc.camera3d, target: { ...doc.camera3d.target } },
    render3d: { ...doc.render3d },
  };
}

function findIndexById<T extends { id: string }>(list: readonly T[], id: string): number {
  return list.findIndex((item) => item.id === id);
}

function dependentsOf2D(doc: MathWorkbenchDocument, id: string): ReadonlySet<string> {
  const direct = new Set<string>();
  for (const obj of doc.objects2d) if (objectReferences2D(obj).includes(id)) direct.add(obj.id);
  const transitive = new Set<string>(direct);
  let grew = true;
  while (grew) {
    grew = false;
    for (const obj of doc.objects2d) {
      if (transitive.has(obj.id)) continue;
      if (objectReferences2D(obj).some((r) => transitive.has(r))) { transitive.add(obj.id); grew = true; }
    }
  }
  return transitive;
}

function applySingle(doc: MathWorkbenchDocument, cmd: MathCommand): MathResult<MathWorkbenchDocument> {
  switch (cmd.kind) {
    case "add2D": {
      const v = validateObject2D(cmd.object);
      if (!v.ok) return v;
      if (findIndexById(doc.objects2d, v.value.id) >= 0) return mathFail([err("duplicate_id", { args: { id: v.value.id } })]);
      const next = cloneDocument(doc);
      next.objects2d = [...next.objects2d, v.value];
      return validateMathDocument(next);
    }
    case "update2D": {
      const idx = findIndexById(doc.objects2d, cmd.id);
      if (idx < 0) return mathFail([err("object_not_found", { args: { id: cmd.id } })]);
      const next = cloneDocument(doc);
      const current = next.objects2d[idx] as GeometryDefinition2D;
      next.objects2d[idx] = { ...current, ...cmd.patch, id: current.id, kind2d: current.kind2d } as GeometryDefinition2D;
      return validateMathDocument(next);
    }
    case "remove2D": {
      const idx = findIndexById(doc.objects2d, cmd.id);
      if (idx < 0) return mathFail([err("object_not_found", { args: { id: cmd.id } })]);
      const dependents = dependentsOf2D(doc, cmd.id);
      if (dependents.size > 0 && !cmd.cascade) {
        return mathFail([err("dependency_exists", { args: { id: cmd.id, count: dependents.size } })]);
      }
      const next = cloneDocument(doc);
      next.objects2d = next.objects2d.filter((o) => o.id !== cmd.id && !dependents.has(o.id));
      return validateMathDocument(next);
    }
    case "addPlot2D": {
      const ctx = buildEvaluationContext(doc);
      if (!ctx.ok) return ctx;
      const v = validatePlot2D(cmd.plot, ctx.value);
      if (!v.ok) return v;
      if (findIndexById(doc.plots2d, v.value.id) >= 0) return mathFail([err("duplicate_id", { args: { id: v.value.id } })]);
      const next = cloneDocument(doc);
      next.plots2d = [...next.plots2d, v.value];
      return validateMathDocument(next);
    }
    case "setPlot2D": {
      const idx = findIndexById(doc.plots2d, cmd.id);
      if (idx < 0) return mathFail([err("plot_not_found", { args: { id: cmd.id } })]);
      const next = cloneDocument(doc);
      const current = next.plots2d[idx] as Record<string, unknown>;
      next.plots2d[idx] = { ...current, ...cmd.patch, id: cmd.id, kind: current.kind } as PlotDefinition2D;
      return validateMathDocument(next);
    }
    case "removePlot2D": {
      const idx = findIndexById(doc.plots2d, cmd.id);
      if (idx < 0) return mathFail([err("plot_not_found", { args: { id: cmd.id } })]);
      const next = cloneDocument(doc);
      next.plots2d = next.plots2d.filter((p) => p.id !== cmd.id);
      return validateMathDocument(next);
    }
    case "addPlot3D": {
      const ctx = buildEvaluationContext(doc);
      if (!ctx.ok) return ctx;
      const v = validatePlot3D(cmd.plot, ctx.value);
      if (!v.ok) return v;
      if (findIndexById(doc.plots3d, v.value.id) >= 0) return mathFail([err("duplicate_id", { args: { id: v.value.id } })]);
      const next = cloneDocument(doc);
      next.plots3d = [...next.plots3d, v.value];
      return validateMathDocument(next);
    }
    case "setPlot3D": {
      const idx = findIndexById(doc.plots3d, cmd.id);
      if (idx < 0) return mathFail([err("plot_not_found", { args: { id: cmd.id } })]);
      const next = cloneDocument(doc);
      const current = next.plots3d[idx] as Record<string, unknown>;
      next.plots3d[idx] = { ...current, ...cmd.patch, id: cmd.id, kind: current.kind } as PlotDefinition3D;
      return validateMathDocument(next);
    }
    case "removePlot3D": {
      const idx = findIndexById(doc.plots3d, cmd.id);
      if (idx < 0) return mathFail([err("plot_not_found", { args: { id: cmd.id } })]);
      const next = cloneDocument(doc);
      next.plots3d = next.plots3d.filter((p) => p.id !== cmd.id);
      return validateMathDocument(next);
    }
    case "setParameter": {
      const next = cloneDocument(doc);
      const idx = findIndexById(next.parameters, cmd.parameter.id);
      const byName = next.parameters.find((p) => p.name === cmd.parameter.name && p.id !== cmd.parameter.id);
      if (byName) return mathFail([err("duplicate_parameter_name", { args: { name: cmd.parameter.name } })]);
      if (idx >= 0) next.parameters[idx] = { ...cmd.parameter };
      else next.parameters = [...next.parameters, { ...cmd.parameter }];
      return validateMathDocument(next);
    }
    case "removeParameter": {
      const idx = findIndexById(doc.parameters, cmd.id);
      if (idx < 0) return mathFail([err("parameter_not_found", { args: { id: cmd.id } })]);
      const next = cloneDocument(doc);
      next.parameters = next.parameters.filter((p) => p.id !== cmd.id);
      return validateMathDocument(next);
    }
    case "addOrUpdateFunction": {
      const next = cloneDocument(doc);
      const idx = findIndexById(next.functions, cmd.fn.id);
      const byName = next.functions.find((f) => f.name === cmd.fn.name && f.id !== cmd.fn.id);
      if (byName) return mathFail([err("duplicate_function_name", { args: { name: cmd.fn.name } })]);
      if (idx >= 0) next.functions[idx] = { ...cmd.fn, params: [...cmd.fn.params] };
      else next.functions = [...next.functions, { ...cmd.fn, params: [...cmd.fn.params] }];
      return validateMathDocument(next);
    }
    case "removeFunction": {
      const idx = findIndexById(doc.functions, cmd.id);
      if (idx < 0) return mathFail([err("function_not_found", { args: { id: cmd.id } })]);
      const removed = doc.functions[idx];
      const next = cloneDocument(doc);
      next.functions = next.functions.filter((f) => f.id !== cmd.id);
      return validateMathDocument(next).ok ? mathOk(next) : mathFail([err("function_in_use")]);
    }
    case "setStyle": {
      const next = cloneDocument(doc);
      const collection = next[cmd.target.collection] as Array<{ id: string; style?: StyleSettings }>;
      const idx = collection.findIndex((item) => item.id === cmd.target.id);
      if (idx < 0) return mathFail([err("object_not_found", { args: { id: cmd.target.id } })]);
      const current = collection[idx] as { style: StyleSettings };
      collection[idx] = { ...collection[idx] as object, style: { ...current.style, ...cmd.patch } } as never;
      return validateMathDocument(next);
    }
    case "setVisibility": {
      const next = cloneDocument(doc);
      const collection = next[cmd.target.collection] as Array<{ id: string; visible?: boolean }>;
      const idx = collection.findIndex((item) => item.id === cmd.target.id);
      if (idx < 0) return mathFail([err("object_not_found", { args: { id: cmd.target.id } })]);
      (collection[idx] as { visible: boolean }).visible = cmd.visible;
      return mathOk(next);
    }
    case "renameDocument": {
      if (typeof cmd.name !== "string" || cmd.name.length === 0 || cmd.name.length > DOC_NAME_MAX) return mathFail([err("invalid_name")]);
      const next = cloneDocument(doc);
      next.name = cmd.name;
      return mathOk(next);
    }
    case "addAnnotation": {
      const next = cloneDocument(doc);
      next.annotations = [...next.annotations, { ...cmd.annotation }];
      return validateMathDocument(next);
    }
    case "updateAnnotation": {
      const idx = findIndexById(doc.annotations, cmd.id);
      if (idx < 0) return mathFail([err("object_not_found", { args: { id: cmd.id } })]);
      const next = cloneDocument(doc);
      next.annotations[idx] = { ...next.annotations[idx] as AnnotationDefinition, ...cmd.patch, id: cmd.id };
      return validateMathDocument(next);
    }
    case "removeAnnotation": {
      const idx = findIndexById(doc.annotations, cmd.id);
      if (idx < 0) return mathFail([err("object_not_found", { args: { id: cmd.id } })]);
      const next = cloneDocument(doc);
      next.annotations = next.annotations.filter((a) => a.id !== cmd.id);
      return mathOk(next);
    }
    case "setView2D": {
      const next = cloneDocument(doc);
      next.view2d = { ...cmd.view };
      return mathOk(next);
    }
    case "setCamera3D": {
      const next = cloneDocument(doc);
      next.camera3d = { ...cmd.camera, target: { ...cmd.camera.target } };
      return mathOk(next);
    }
    case "setRender3D": {
      const next = cloneDocument(doc);
      next.render3d = { ...cmd.render };
      return mathOk(next);
    }
    case "batch": {
      let working = cloneDocument(doc);
      for (const sub of cmd.commands) {
        const applied = applySingle(working, sub);
        if (!applied.ok) return mathFail(applied.diagnostics);
        working = applied.value;
      }
      return mathOk(working);
    }
  }
}

/** Pure reducer: never mutates the input document; revalidates after edits. */
export function applyMathCommand(doc: MathWorkbenchDocument, cmd: MathCommand): MathResult<MathWorkbenchDocument> {
  const applied = applySingle(doc, cmd);
  if (!applied.ok) return applied;
  const next = applied.value;
  if (isSemanticCommand(cmd)) next.revision = doc.revision + 1;
  return mathOk(next, applied.diagnostics);
}

/* ------------------------------- serialization ------------------------------- */

export function serializeMathDocument(doc: MathWorkbenchDocument): string {
  return JSON.stringify(doc);
}

export function deserializeMathDocument(raw: string): MathResult<MathWorkbenchDocument> {
  if (raw.length > MATH_LIMITS.maxDocumentBytes) return mathFail([err("document_too_large", { args: { bytes: raw.length } })]);
  try {
    return validateMathDocument(JSON.parse(raw));
  } catch {
    return mathFail([err("invalid_json")]);
  }
}

/* -------------------------------- fingerprint -------------------------------- */

function fnv1a(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

/**
 * Fingerprint of the semantic document subset only. Deliberately excludes
 * `revision`, `activeMode`, `view2d`, `camera3d` (transient view state),
 * history stacks and local timestamps — camera moves must not flip the dirty
 * flag. Styles/visibility of drawn objects ARE part of the figure and stay in.
 */
export function semanticFingerprint(doc: MathWorkbenchDocument): string {
  const projection = {
    schemaVersion: doc.schemaVersion,
    id: doc.id,
    name: doc.name,
    parameters: [...doc.parameters].sort((a, b) => a.name.localeCompare(b.name)).map((p) => [p.name, p.value, p.min, p.max, p.step]),
    functions: [...doc.functions].sort((a, b) => a.name.localeCompare(b.name)).map((f) => [f.name, [...f.params].sort().join(","), f.expression.trim()]),
    objects2d: [...doc.objects2d].sort((a, b) => a.id.localeCompare(b.id)),
    plots2d: [...doc.plots2d].sort((a, b) => a.id.localeCompare(b.id)),
    plots3d: [...doc.plots3d].sort((a, b) => a.id.localeCompare(b.id)),
    annotations: [...doc.annotations].sort((a, b) => a.id.localeCompare(b.id)),
    render3d: doc.render3d,
  };
  return fnv1a(JSON.stringify(projection));
}

/* -------------------------------- resolution -------------------------------- */

export function resolveGeometry(doc: MathWorkbenchDocument): MathResult<ResolvedGeometry> {
  const diagnostics: MathDiagnostic[] = [];
  const geometry2d = resolveGeometry2D(doc.objects2d);
  diagnostics.push(...geometry2d.diagnostics);
  return mathOk({ geometry2d, diagnostics });
}

export type { ResolvedGeometry, ResolvedGeometry2D };

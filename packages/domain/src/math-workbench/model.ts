/**
 * Math workbench document model (schema v1). Serializable objects are pure
 * mathematical definitions — construction inputs, expression sources and
 * style — never Three scene graphs, GPU buffers or resolved coordinates.
 * `dependsOn` edges are derived from construction references and expression
 * ASTs; they are never stored as a second editable truth.
 */

import type { MathDiagnostic } from "./diagnostics.ts";

export type DrawingMode = "geometry2d" | "functions2d" | "functions3d";

export const DRAWING_MODES: readonly DrawingMode[] = ["functions2d", "geometry2d", "functions3d"];

export interface StyleSettings {
  /** Controlled `#rrggbb` format only; the UI palette hands these out. */
  color: string;
  /** Stroke width in CSS px (2D SVG and 3D Line2 use the same contract). */
  width: number;
  opacity: number;
  dashed: boolean;
}

export interface BaseDefinition {
  id: string;
  label: string;
  visible: boolean;
  locked: boolean;
  style: StyleSettings;
}

export interface ParameterDefinition {
  id: string;
  /** Single identifier, not shadowing reserved variables. */
  name: string;
  value: number;
  min: number;
  max: number;
  step: number;
}

export interface NamedFunctionDefinition {
  id: string;
  name: string;
  params: readonly string[];
  /** Raw expression source of the body; recompiled on every load/import. */
  expression: string;
}

/* ---------------------------------- 2D ---------------------------------- */

export type PointConstruction2D =
  | { kind: "free"; x: number; y: number }
  | { kind: "midpoint"; aId: string; bId: string }
  | { kind: "intersection"; aId: string; bId: string; branch: 1 | 2; branchAnchor?: { x: number; y: number } };

export interface Point2DDefinition extends BaseDefinition {
  kind2d: "point";
  construction: PointConstruction2D;
}

export type LineKind2D = "line" | "segment" | "ray";

export interface Line2DDefinition extends BaseDefinition {
  kind2d: "line";
  lineKind: LineKind2D;
  /** line/segment/ray through two point ids. */
  aId: string;
  bId: string;
}

export type ConstructedLineKind2D =
  | "parallel"          // through point, parallel to line
  | "perpendicular"     // through point, perpendicular to line
  | "perpendicularBisector" // of segment
  | "angleBisector";    // of angle abc, branch selects internal/external

export interface ConstructedLine2DDefinition extends BaseDefinition {
  kind2d: "constructedLine";
  construction: { kind: ConstructedLineKind2D; pointId?: string; lineId?: string; aId?: string; bId?: string; cId?: string; branch?: 1 | 2 };
}

export type CircleConstruction2D =
  | { kind: "centerRadius"; centerId: string; radius: number }
  | { kind: "centerPoint"; centerId: string; pointId: string }
  | { kind: "threePoints"; aId: string; bId: string; cId: string };

export interface Circle2DDefinition extends BaseDefinition {
  kind2d: "circle";
  construction: CircleConstruction2D;
}

export interface Arc2DDefinition extends BaseDefinition {
  kind2d: "arc";
  centerId: string;
  fromId: string;
  toId: string;
}

export interface Polygon2DDefinition extends BaseDefinition {
  kind2d: "polygon";
  pointIds: readonly string[];
}

export interface RegularPolygon2DDefinition extends BaseDefinition {
  kind2d: "regularPolygon";
  centerId: string;
  vertexId: string;
  sides: number;
}

export type GeometryDefinition2D =
  | Point2DDefinition
  | Line2DDefinition
  | ConstructedLine2DDefinition
  | Circle2DDefinition
  | Arc2DDefinition
  | Polygon2DDefinition
  | RegularPolygon2DDefinition;

export type Geometry2DKinds = GeometryDefinition2D["kind2d"];

export interface Domain2D {
  min: number;
  max: number;
}

export type PlotDefinition2D =
  | { kind: "explicit"; id: string; label: string; visible: boolean; style: StyleSettings; expression: string; xDomain?: Domain2D }
  | { kind: "inverse"; id: string; label: string; visible: boolean; style: StyleSettings; expression: string; yDomain?: Domain2D }
  | { kind: "parametric"; id: string; label: string; visible: boolean; style: StyleSettings; xExpression: string; yExpression: string; tDomain: Domain2D }
  | { kind: "polar"; id: string; label: string; visible: boolean; style: StyleSettings; rExpression: string; thetaDomain: Domain2D }
  | { kind: "implicit"; id: string; label: string; visible: boolean; style: StyleSettings; expression: string; level: number; domain: { x: Domain2D; y: Domain2D } };

export type Plot2DKinds = PlotDefinition2D["kind"];

/* ---------------------------------- 3D ---------------------------------- */
// Solid-geometry construction objects were removed (ADR-0022): the 3D stage
// is function plotting only. Documents keep `activeMode`/`camera3d`/`plots3d`.

export type SurfaceQuality = "low" | "normal" | "high";

export interface DomainPair {
  u: Domain2D;
  v: Domain2D;
}

export type PlotDefinition3D =
  | { kind: "explicitSurface"; id: string; label: string; visible: boolean; style: StyleSettings; expression: string; xDomain: Domain2D; yDomain: Domain2D; quality: SurfaceQuality; opacity: number; showGrid: boolean }
  | { kind: "parametricSurface"; id: string; label: string; visible: boolean; style: StyleSettings; xExpression: string; yExpression: string; zExpression: string; uDomain: Domain2D; vDomain: Domain2D; wrapU: boolean; wrapV: boolean; quality: SurfaceQuality; opacity: number; showGrid: boolean }
  | { kind: "implicitSurface"; id: string; label: string; visible: boolean; style: StyleSettings; expression: string; iso: number; box: { x: Domain2D; y: Domain2D; z: Domain2D }; resolution: number; opacity: number }
  | { kind: "parametricCurve"; id: string; label: string; visible: boolean; style: StyleSettings; xExpression: string; yExpression: string; zExpression: string; tDomain: Domain2D; samples: number };

export type Plot3DKinds = PlotDefinition3D["kind"];

/* ------------------------------ annotations ------------------------------ */

export interface AnnotationDefinition {
  id: string;
  /** Plain text only — SVG/HTML never round-trips through annotations. */
  text: string;
  x: number;
  y: number;
  visible: boolean;
  /** 3D annotations anchor in math space too; the stage projects them. */
  z?: number;
  space: "2d" | "3d";
}

/* --------------------------- view / render state -------------------------- */

export interface View2D {
  centerX: number;
  centerY: number;
  /** World units shown per CSS px (zoom). */
  scale: number;
  showGrid: boolean;
  snapToGrid: boolean;
  snapSize: number;
}

export type Camera3DKind = "perspective" | "orthographic";

export interface Camera3DSettings {
  kind: Camera3DKind;
  /** Orbit angles in radians (azimuth around math +z, elevation from xy plane). */
  azimuth: number;
  elevation: number;
  distance: number;
  target: { x: number; y: number; z: number };
}

export interface Render3DSettings {
  showGridXY: boolean;
  showGridXZ: boolean;
  showGridYZ: boolean;
  showAxes: boolean;
  /** global edge visibility preset */
  edges: "visible" | "hidden" | "off";
}

/* -------------------------------- document ------------------------------- */

export interface MathWorkbenchDocument {
  schemaVersion: 1;
  id: string;
  name: string;
  /** Bumped on every semantic commit; the worker stale guard trusts it. */
  revision: number;
  activeMode: DrawingMode;
  parameters: ParameterDefinition[];
  functions: NamedFunctionDefinition[];
  objects2d: GeometryDefinition2D[];
  plots2d: PlotDefinition2D[];
  plots3d: PlotDefinition3D[];
  annotations: AnnotationDefinition[];
  view2d: View2D;
  camera3d: Camera3DSettings;
  render3d: Render3DSettings;
}

/* -------------------------------- commands -------------------------------- */

export type MathCommand =
  | { kind: "add2D"; object: GeometryDefinition2D }
  | { kind: "update2D"; id: string; patch: Partial<Omit<Point2DDefinition, "id" | "kind2d">> }
  | { kind: "remove2D"; id: string; cascade?: boolean }
  | { kind: "addPlot2D"; plot: PlotDefinition2D }
  | { kind: "setPlot2D"; id: string; patch: Record<string, unknown> }
  | { kind: "removePlot2D"; id: string }
  | { kind: "addPlot3D"; plot: PlotDefinition3D }
  | { kind: "setPlot3D"; id: string; patch: Record<string, unknown> }
  | { kind: "removePlot3D"; id: string }
  | { kind: "setParameter"; parameter: ParameterDefinition }
  | { kind: "removeParameter"; id: string }
  | { kind: "addOrUpdateFunction"; fn: NamedFunctionDefinition }
  | { kind: "removeFunction"; id: string }
  | { kind: "setStyle"; target: { collection: "objects2d" | "plots2d" | "plots3d"; id: string }; patch: Partial<StyleSettings> }
  | { kind: "setVisibility"; target: { collection: "objects2d" | "plots2d" | "plots3d" | "annotations"; id: string }; visible: boolean }
  | { kind: "renameDocument"; name: string }
  | { kind: "addAnnotation"; annotation: AnnotationDefinition }
  | { kind: "updateAnnotation"; id: string; patch: Partial<AnnotationDefinition> }
  | { kind: "removeAnnotation"; id: string }
  | { kind: "setView2D"; view: View2D }
  | { kind: "setCamera3D"; camera: Camera3DSettings }
  | { kind: "setRender3D"; render: Render3DSettings }
  | { kind: "batch"; commands: readonly MathCommand[] };

/** Commands that change derived-math truth and must bump `revision`. */
export function isSemanticCommand(cmd: MathCommand): boolean {
  switch (cmd.kind) {
    case "setView2D":
    case "setCamera3D":
      return false;
    default:
      return true;
  }
}

/* --------------------------- resolved computation -------------------------- */

export interface Pt2 { x: number; y: number }

export interface ResolvedPoint2D { id: string; label: string; position: Pt2; definition: Point2DDefinition }
export interface ResolvedLine2D { id: string; label: string; kind: LineKind2D | "line"; point: Pt2; direction: Pt2; length: number | null }
export interface ResolvedCircle2D { id: string; label: string; center: Pt2; radius: number }
export interface ResolvedArc2D { id: string; label: string; center: Pt2; radius: number; startAngle: number; endAngle: number }
export interface ResolvedPolygon2D { id: string; label: string; points: Pt2[]; closed: boolean }

export interface ResolvedGeometry2D {
  /** Evaluation order honoring the dependency DAG. */
  order: readonly string[];
  points: ReadonlyMap<string, ResolvedPoint2D>;
  lines: ReadonlyMap<string, ResolvedLine2D>;
  constructedLines: ReadonlyMap<string, ResolvedLine2D>;
  circles: ReadonlyMap<string, ResolvedCircle2D>;
  arcs: ReadonlyMap<string, ResolvedArc2D>;
  polygons: ReadonlyMap<string, ResolvedPolygon2D>;
  diagnostics: MathDiagnostic[];
}

export interface Pt3 { x: number; y: number; z: number }

export interface ResolvedGeometry {
  geometry2d: ResolvedGeometry2D;
  diagnostics: MathDiagnostic[];
}

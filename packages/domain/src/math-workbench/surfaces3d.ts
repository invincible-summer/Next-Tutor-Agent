/**
 * 3D surface generation: explicit z=f(x,y) and parametric (u,v) surfaces,
 * plus 3D parametric curves. All math runs in double precision here; results
 * are checked finite before conversion to Float32Array. Degenerate/discontinuous
 * cells drop their triangles instead of bridging poles or asymptotes with fake
 * geometry. Surfaces are emitted in tiles (patches) so the renderer can do
 * approximate per-patch transparency sorting; shared boundary sampling keeps
 * tiles crack-free.
 */

import { err, mathFail, mathOk, warn, type MathDiagnostic, type MathResult, MATH_LIMITS } from "./diagnostics.ts";
import { evaluateExpression, type CompiledFunction, type SymbolTable } from "./expression.ts";
import type { Domain2D, SurfaceQuality } from "./model.ts";
import { v3, vcross, vnormalize, vsub, type Pt3 } from "./math3d.ts";

export interface Bounds3 { minX: number; maxX: number; minY: number; maxY: number; minZ: number; maxZ: number }

export interface SurfacePatchData {
  /** Index range into the mesh index buffer [offset, offset+count). */
  offset: number;
  count: number;
  bounds: Bounds3;
}

export interface SurfaceMeshData {
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
  bounds: Bounds3;
  /** Isoparametric grid lines (u=const / v=const), packed xyz triples. */
  parameterLines: Float32Array[];
  /** Domain boundary polylines, packed xyz triples. */
  boundaryLines: Float32Array[];
  patches: SurfacePatchData[];
}

export interface Curve3DData {
  positions: Float32Array;
  /** Tangent per sample (normalized); same length as positions. */
  tangents: Float32Array;
  length: number;
  bounds: Bounds3;
}

export function qualityGrid(quality: SurfaceQuality): number {
  if (quality === "low") return 32;
  if (quality === "high") return MATH_LIMITS.surfaceGridHigh;
  return MATH_LIMITS.surfaceGridNormal;
}

const EMPTY_BOUNDS: Bounds3 = { minX: 0, maxX: 0, minY: 0, maxY: 0, minZ: 0, maxZ: 0 };

function emptyMesh(): SurfaceMeshData {
  return { positions: new Float32Array(0), normals: new Float32Array(0), indices: new Uint32Array(0), bounds: EMPTY_BOUNDS, parameterLines: [], boundaryLines: [], patches: [] };
}

function expandBounds(b: Bounds3, p: Pt3): void {
  b.minX = Math.min(b.minX, p.x); b.maxX = Math.max(b.maxX, p.x);
  b.minY = Math.min(b.minY, p.y); b.maxY = Math.max(b.maxY, p.y);
  b.minZ = Math.min(b.minZ, p.z); b.maxZ = Math.max(b.maxZ, p.z);
}

class MeshBuilder {
  positions: number[] = [];
  normals: number[] = [];
  indices: number[] = [];
  bounds: Bounds3 = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity, minZ: Infinity, maxZ: -Infinity };

  vertex(p: Pt3, n: Pt3): number {
    this.positions.push(p.x, p.y, p.z);
    this.normals.push(n.x, n.y, n.z);
    expandBounds(this.bounds, p);
    return this.positions.length / 3 - 1;
  }

  triangle(a: number, b: number, c: number): void {
    this.indices.push(a, b, c);
  }

  count(): number { return this.positions.length / 3; }
}

function buildResult(builder: MeshBuilder, parameterLines: Float32Array[], boundaryLines: Float32Array[], patches: SurfacePatchData[]): MathResult<SurfaceMeshData> {
  const diagnostics: MathDiagnostic[] = [];
  const triangleCount = builder.indices.length / 3;
  if (triangleCount > MATH_LIMITS.maxTriangles3d) {
    return mathFail([err("mesh_limit_exceeded", { args: { triangles: triangleCount, limit: MATH_LIMITS.maxTriangles3d } })]);
  }
  if (builder.count() === 0) {
    diagnostics.push(warn("surface_empty", {}));
    return mathOk({ ...emptyMesh(), parameterLines, boundaryLines }, diagnostics);
  }
  return mathOk({
    positions: new Float32Array(builder.positions),
    normals: new Float32Array(builder.normals),
    indices: new Uint32Array(builder.indices),
    bounds: builder.bounds,
    parameterLines,
    boundaryLines,
    patches,
  }, diagnostics);
}

/**
 * Tile iteration order shared by both surface generators: cells are emitted
 * tile-by-tile so each patch owns a contiguous index range (approximate
 * transparency sorting needs no index shuffling later).
 */
function tileRanges(gridX: number, gridY: number, tiles: number): Array<{ x0: number; x1: number; y0: number; y1: number }> {
  const t = Math.max(1, Math.min(tiles, 4));
  const cellsX = gridX - 1;
  const cellsY = gridY - 1;
  const perX = Math.max(1, Math.ceil(cellsX / t));
  const perY = Math.max(1, Math.ceil(cellsY / t));
  const ranges: Array<{ x0: number; x1: number; y0: number; y1: number }> = [];
  for (let ty = 0; ty * perY < cellsY; ty++) {
    for (let tx = 0; tx * perX < cellsX; tx++) {
      ranges.push({
        x0: tx * perX,
        x1: Math.min(cellsX, (tx + 1) * perX),
        y0: ty * perY,
        y1: Math.min(cellsY, (ty + 1) * perY),
      });
    }
  }
  return ranges;
}

class PatchTracker {
  readonly patches: SurfacePatchData[] = [];

  begin(builder: MeshBuilder): void {
    this.start = builder.indices.length;
    this.bounds = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity, minZ: Infinity, maxZ: -Infinity };
    this.count = 0;
  }

  triangle(builder: MeshBuilder, a: number, b: number, c: number): void {
    builder.triangle(a, b, c);
    this.count += 3;
    for (const vi of [a, b, c]) {
      expandBounds(this.bounds, v3(builder.positions[vi * 3] as number, builder.positions[vi * 3 + 1] as number, builder.positions[vi * 3 + 2] as number));
    }
  }

  end(builder: MeshBuilder): void {
    if (this.count > 0) this.patches.push({ offset: this.start, count: this.count, bounds: this.bounds });
    void builder;
  }

  private start = 0;
  private count = 0;
  private bounds: Bounds3 = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity, minZ: Infinity, maxZ: -Infinity };
}

/**
 * Explicit surface z=f(x,y) on a rectangular domain. Cells whose corner
 * values are non-finite, or whose z-jump signals a discontinuity, drop their
 * triangles. Normals use analytic central differences when the expression
 * differentiates cleanly, else screen-independent finite differences.
 */
export function generateExplicitSurface(
  compiled: CompiledFunction,
  symbols: SymbolTable,
  xDomain: Domain2D,
  yDomain: Domain2D,
  quality: SurfaceQuality,
): MathResult<SurfaceMeshData> {
  const n = qualityGrid(quality);
  const gridX = n, gridY = n;
  const diagnostics: MathDiagnostic[] = [];
  const builder = new MeshBuilder();
  // Sample heights first; NaN marks undefined cells.
  const heights: Array<number | null> = new Array(gridX * gridY).fill(null);
  for (let j = 0; j < gridY; j++) {
    const y = yDomain.min + ((yDomain.max - yDomain.min) * j) / (gridY - 1);
    for (let i = 0; i < gridX; i++) {
      const x = xDomain.min + ((xDomain.max - xDomain.min) * i) / (gridX - 1);
      const v = evaluateExpression(compiled, { x, y }, symbols);
      const value = v.ok && Number.isFinite(v.value) ? v.value : null;
      // Guard absurd magnitudes: they are rendering poison, not "just big".
      heights[j * gridX + i] = value !== null && Math.abs(value) <= 1e6 ? value : null;
    }
  }
  const maxJump = 40 * Math.max(xDomain.max - xDomain.min, yDomain.max - yDomain.min);
  const at = (i: number, j: number): number | null => heights[j * gridX + i] ?? null;
  const normalAt = (i: number, j: number): Pt3 => {
    const x = xDomain.min + ((xDomain.max - xDomain.min) * i) / (gridX - 1);
    const y = yDomain.min + ((yDomain.max - yDomain.min) * j) / (gridY - 1);
    const h = Math.max((xDomain.max - xDomain.min) / (gridX - 1), 1e-9);
    const k = Math.max((yDomain.max - yDomain.min) / (gridY - 1), 1e-9);
    const evalXY = (xx: number, yy: number): number | null => {
      const v = evaluateExpression(compiled, { x: xx, y: yy }, symbols);
      return v.ok && Number.isFinite(v.value) ? v.value : null;
    };
    const xm = i > 0 ? at(i - 1, j) : evalXY(x - h, y);
    const xp = i < gridX - 1 ? at(i + 1, j) : evalXY(x + h, y);
    const ym = j > 0 ? at(i, j - 1) : evalXY(x, y - k);
    const yp = j < gridY - 1 ? at(i, j + 1) : evalXY(x, y + k);
    const dzdx = xm !== null && xp !== null ? (xp - xm) / (2 * h) : 0;
    const dzdy = ym !== null && yp !== null ? (yp - ym) / (2 * k) : 0;
    return vnormalize(v3(-dzdx, -dzdy, 1));
  };
  let dropped = 0;
  const tracker = new PatchTracker();
  for (const range of tileRanges(gridX, gridY, quality === "low" ? 2 : 3)) {
    tracker.begin(builder);
    for (let j = range.y0; j < range.y1; j++) {
      for (let i = range.x0; i < range.x1; i++) {
        const z00 = at(i, j), z10 = at(i + 1, j), z11 = at(i + 1, j + 1), z01 = at(i, j + 1);
        if (z00 === null || z10 === null || z11 === null || z01 === null) { dropped++; continue; }
        const jump = Math.max(Math.abs(z10 - z00), Math.abs(z11 - z10), Math.abs(z01 - z11), Math.abs(z00 - z01));
        if (jump > maxJump) { dropped++; continue; }
        const x0 = xDomain.min + ((xDomain.max - xDomain.min) * i) / (gridX - 1);
        const x1 = xDomain.min + ((xDomain.max - xDomain.min) * (i + 1)) / (gridX - 1);
        const y0 = yDomain.min + ((yDomain.max - yDomain.min) * j) / (gridY - 1);
        const y1 = yDomain.min + ((yDomain.max - yDomain.min) * (j + 1)) / (gridY - 1);
        const i00 = builder.vertex(v3(x0, y0, z00), normalAt(i, j));
        const i10 = builder.vertex(v3(x1, y0, z10), normalAt(i + 1, j));
        const i11 = builder.vertex(v3(x1, y1, z11), normalAt(i + 1, j + 1));
        const i01 = builder.vertex(v3(x0, y1, z01), normalAt(i, j + 1));
        tracker.triangle(builder, i00, i10, i11);
        tracker.triangle(builder, i00, i11, i01);
      }
    }
    tracker.end(builder);
  }
  if (dropped > 0) diagnostics.push(warn("surface_cells_dropped", { args: { dropped, total: (gridX - 1) * (gridY - 1) } }));
  const lines = isoLines(gridX, gridY, xDomain, yDomain, at);
  const result = buildResult(builder, lines.parameterLines, lines.boundaryLines, tracker.patches);
  if (result.ok) return mathOk(result.value, [...diagnostics, ...result.diagnostics]);
  return result;
}

function isoLines(
  gridX: number,
  gridY: number,
  xDomain: Domain2D,
  yDomain: Domain2D,
  at: (i: number, j: number) => number | null,
): { parameterLines: Float32Array[]; boundaryLines: Float32Array[] } {
  const parameterLines: Float32Array[] = [];
  const lineCount = Math.min(12, Math.max(4, Math.floor(gridX / 8)));
  const xStep = (xDomain.max - xDomain.min) / (lineCount - 1);
  const yStep = (yDomain.max - yDomain.min) / (lineCount - 1);
  for (let l = 0; l < lineCount; l++) {
    const ix = Math.round((l * xStep * (gridX - 1)) / (xDomain.max - xDomain.min));
    const iy = Math.round((l * yStep * (gridY - 1)) / (yDomain.max - yDomain.min));
    const xLine: number[] = [];
    for (let j = 0; j < gridY; j++) {
      const z = at(Math.min(gridX - 1, Math.max(0, ix)), j);
      const x = xDomain.min + ((xDomain.max - xDomain.min) * Math.min(gridX - 1, Math.max(0, ix))) / (gridX - 1);
      const y = yDomain.min + ((yDomain.max - yDomain.min) * j) / (gridY - 1);
      if (z !== null) xLine.push(x, y, z); else xLine.push(NaN, NaN, NaN);
    }
    parameterLines.push(new Float32Array(xLine));
    const yLine: number[] = [];
    for (let i2 = 0; i2 < gridX; i2++) {
      const z = at(i2, Math.min(gridY - 1, Math.max(0, iy)));
      const x = xDomain.min + ((xDomain.max - xDomain.min) * i2) / (gridX - 1);
      const y = yDomain.min + ((yDomain.max - yDomain.min) * Math.min(gridY - 1, Math.max(0, iy))) / (gridY - 1);
      if (z !== null) yLine.push(x, y, z); else yLine.push(NaN, NaN, NaN);
    }
    parameterLines.push(new Float32Array(yLine));
  }
  const boundaryLines: Float32Array[] = [];
  const edge = (fixed: "x" | "y", index: number): Float32Array => {
    const pts: number[] = [];
    const n = fixed === "x" ? gridY : gridX;
    for (let s = 0; s < n; s++) {
      const i = fixed === "x" ? index : s;
      const j = fixed === "x" ? s : index;
      const z = at(i, j);
      const x = xDomain.min + ((xDomain.max - xDomain.min) * i) / (gridX - 1);
      const y = yDomain.min + ((yDomain.max - yDomain.min) * j) / (gridY - 1);
      if (z !== null) pts.push(x, y, z); else pts.push(NaN, NaN, NaN);
    }
    return new Float32Array(pts);
  };
  boundaryLines.push(edge("x", 0), edge("x", gridX - 1), edge("y", 0), edge("y", gridY - 1));
  return { parameterLines, boundaryLines };
}

/**
 * Parametric surface r(u,v). wrapU/wrapV close the topology exactly (not by
 * guessing from near-equal endpoints); degenerate/pole triangles are culled.
 * Normals come from ∂r/∂u × ∂r/∂v finite differences with consistent winding.
 */
export function generateParametricSurface(
  xCompiled: CompiledFunction,
  yCompiled: CompiledFunction,
  zCompiled: CompiledFunction,
  symbols: SymbolTable,
  uDomain: Domain2D,
  vDomain: Domain2D,
  quality: SurfaceQuality,
  wrapU: boolean,
  wrapV: boolean,
): MathResult<SurfaceMeshData> {
  const n = qualityGrid(quality);
  const gridU = n, gridV = n;
  const builder = new MeshBuilder();
  const diagnostics: MathDiagnostic[] = [];
  const evalAt = (u: number, v: number): Pt3 | null => {
    const x = evaluateExpression(xCompiled, { u, v }, symbols);
    const y = evaluateExpression(yCompiled, { u, v }, symbols);
    const z = evaluateExpression(zCompiled, { u, v }, symbols);
    if (!x.ok || !y.ok || !z.ok) return null;
    if (!Number.isFinite(x.value) || !Number.isFinite(y.value) || !Number.isFinite(z.value)) return null;
    if (Math.abs(x.value) > 1e5 || Math.abs(y.value) > 1e5 || Math.abs(z.value) > 1e5) return null;
    return v3(x.value, y.value, z.value);
  };
  const points: Array<Pt3 | null> = new Array(gridU * gridV).fill(null);
  for (let j = 0; j < gridV; j++) {
    const v = vDomain.min + ((vDomain.max - vDomain.min) * j) / (gridV - 1);
    for (let i = 0; i < gridU; i++) {
      const u = uDomain.min + ((uDomain.max - uDomain.min) * i) / (gridU - 1);
      points[j * gridU + i] = evalAt(u, v);
    }
  }
  const at = (i: number, j: number): Pt3 | null => {
    const ii = wrapU ? ((i % gridU) + gridU) % gridU : i;
    const jj = wrapV ? ((j % gridV) + gridV) % gridV : j;
    if (ii < 0 || ii >= gridU || jj < 0 || jj >= gridV) return null;
    return points[jj * gridU + ii] ?? null;
  };
  const du = (uDomain.max - uDomain.min) / (gridU - 1);
  const dv = (vDomain.max - vDomain.min) / (gridV - 1);
  const normalAt = (i: number, j: number): Pt3 => {
    const u = uDomain.min + du * i;
    const v = vDomain.min + dv * j;
    const h = 1e-5;
    const pu1 = evalAt(u - h, v), pu2 = evalAt(u + h, v);
    const pv1 = evalAt(u, v - h), pv2 = evalAt(u, v + h);
    if (pu1 && pu2 && pv1 && pv2) {
      const nrm = vcross(vsub(pu2, pu1), vsub(pv2, pv1));
      const len = Math.hypot(nrm.x, nrm.y, nrm.z);
      if (len > 1e-14) return vnormalize(nrm);
    }
    return v3(0, 0, 1);
  };
  let degenerate = 0;
  const minArea = 1e-12 * Math.max(du * dv, 1e-9);
  const tracker = new PatchTracker();
  for (const range of tileRanges(gridU, gridV, quality === "low" ? 2 : 3)) {
    tracker.begin(builder);
    for (let j = range.y0; j < range.y1; j++) {
      for (let i = range.x0; i < range.x1; i++) {
        const p00 = at(i, j), p10 = at(i + 1, j), p11 = at(i + 1, j + 1), p01 = at(i, j + 1);
        if (!p00 || !p10 || !p11 || !p01) { degenerate++; continue; }
        const cross1 = vcross(vsub(p10, p00), vsub(p11, p00));
        const cross2 = vcross(vsub(p11, p00), vsub(p01, p00));
        if (Math.hypot(cross1.x, cross1.y, cross1.z) < minArea || Math.hypot(cross2.x, cross2.y, cross2.z) < minArea) {
          degenerate++;
          continue;
        }
        const i00 = builder.vertex(p00, normalAt(i, j));
        const i10 = builder.vertex(p10, normalAt(i + 1, j));
        const i11 = builder.vertex(p11, normalAt(i + 1, j + 1));
        const i01 = builder.vertex(p01, normalAt(i, j + 1));
        tracker.triangle(builder, i00, i10, i11);
        tracker.triangle(builder, i00, i11, i01);
      }
    }
    tracker.end(builder);
  }
  if (degenerate > 0) diagnostics.push(warn("surface_degenerate_cells", { args: { degenerate } }));
  // Parameter lines follow u/v isolines (never triangulation diagonals).
  const parameterLines: Float32Array[] = [];
  const lineCount = Math.min(12, Math.max(4, Math.floor(gridU / 8)));
  for (let l = 0; l < lineCount; l++) {
    const iu = Math.min(gridU - 1, Math.round((l * (gridU - 1)) / (lineCount - 1)));
    const iv = Math.min(gridV - 1, Math.round((l * (gridV - 1)) / (lineCount - 1)));
    const uLine: number[] = [];
    for (let j2 = 0; j2 < gridV; j2++) {
      const p = at(iu, j2);
      if (p) uLine.push(p.x, p.y, p.z); else uLine.push(NaN, NaN, NaN);
    }
    parameterLines.push(new Float32Array(uLine));
    const vLine: number[] = [];
    for (let i2 = 0; i2 < gridU; i2++) {
      const p = at(i2, iv);
      if (p) vLine.push(p.x, p.y, p.z); else vLine.push(NaN, NaN, NaN);
    }
    parameterLines.push(new Float32Array(vLine));
  }
  const boundaryLines: Float32Array[] = [];
  if (!wrapU) {
    const first: number[] = [], last: number[] = [];
    for (let j2 = 0; j2 < gridV; j2++) {
      const a = at(0, j2), b = at(gridU - 1, j2);
      if (a) first.push(a.x, a.y, a.z); else first.push(NaN, NaN, NaN);
      if (b) last.push(b.x, b.y, b.z); else last.push(NaN, NaN, NaN);
    }
    boundaryLines.push(new Float32Array(first), new Float32Array(last));
  }
  if (!wrapV) {
    const first: number[] = [], last: number[] = [];
    for (let i2 = 0; i2 < gridU; i2++) {
      const a = at(i2, 0), b = at(i2, gridV - 1);
      if (a) first.push(a.x, a.y, a.z); else first.push(NaN, NaN, NaN);
      if (b) last.push(b.x, b.y, b.z); else last.push(NaN, NaN, NaN);
    }
    boundaryLines.push(new Float32Array(first), new Float32Array(last));
  }
  const result = buildResult(builder, parameterLines, boundaryLines, tracker.patches);
  if (result.ok) return mathOk(result.value, [...diagnostics, ...result.diagnostics]);
  return result;
}

/** 3D parametric curve with tangent approximation and arc length. */
export function generateParametricCurve(
  xCompiled: CompiledFunction,
  yCompiled: CompiledFunction,
  zCompiled: CompiledFunction,
  symbols: SymbolTable,
  tDomain: Domain2D,
  samples: number,
): MathResult<Curve3DData> {
  const n = Math.min(Math.max(samples, 16), MATH_LIMITS.maxSamples2d);
  const pts: Pt3[] = [];
  for (let i = 0; i <= n; i++) {
    const t = tDomain.min + ((tDomain.max - tDomain.min) * i) / n;
    const x = evaluateExpression(xCompiled, { t }, symbols);
    const y = evaluateExpression(yCompiled, { t }, symbols);
    const z = evaluateExpression(zCompiled, { t }, symbols);
    if (x.ok && y.ok && z.ok && Number.isFinite(x.value) && Number.isFinite(y.value) && Number.isFinite(z.value)) {
      pts.push(v3(x.value, y.value, z.value));
    }
  }
  if (pts.length < 2) return mathFail([err("curve_all_undefined")]);
  const positions = new Float32Array(pts.length * 3);
  const tangents = new Float32Array(pts.length * 3);
  const bounds: Bounds3 = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity, minZ: Infinity, maxZ: -Infinity };
  let length = 0;
  pts.forEach((p, i) => {
    positions[i * 3] = p.x; positions[i * 3 + 1] = p.y; positions[i * 3 + 2] = p.z;
    expandBounds(bounds, p);
    if (i > 0) length += Math.hypot(p.x - pts[i - 1]!.x, p.y - pts[i - 1]!.y, p.z - pts[i - 1]!.z);
  });
  for (let i = 0; i < pts.length; i++) {
    const prev = pts[Math.max(0, i - 1)] as Pt3;
    const next = pts[Math.min(pts.length - 1, i + 1)] as Pt3;
    const tangent = vnormalize(vsub(next, prev));
    tangents[i * 3] = tangent.x; tangents[i * 3 + 1] = tangent.y; tangents[i * 3 + 2] = tangent.z;
  }
  return mathOk({ positions, tangents, length, bounds });
}

/**
 * 2D curve sampling: adaptive explicit/inverse plots with asymptote splits,
 * parametric and polar curves over closed domains, and a marching-squares
 * implicit contour with saddle disambiguation. Outputs are world-space
 * polylines with explicit breaks — never a single line through a discontinuity.
 */

import { err, mathFail, mathOk, warn, type MathDiagnostic, type MathResult, MATH_LIMITS } from "./diagnostics.ts";
import { evaluateExpression, type CompiledFunction, type SymbolTable } from "./expression.ts";
import type { Domain2D, PlotDefinition2D, Pt2 } from "./model.ts";
import type { EvaluationContext } from "./document.ts";

export interface WorldRect { minX: number; maxX: number; minY: number; maxY: number }

export interface PlotPaths2D {
  /** Connected polylines in world coordinates; breaks split discontinuities. */
  polylines: Pt2[][];
  diagnostics: MathDiagnostic[];
}

const MAX_DEPTH = 8;

function evalY(compiled: CompiledFunction, symbols: SymbolTable, x: number): number | null {
  const v = evaluateExpression(compiled, { x }, symbols);
  return v.ok && Number.isFinite(v.value) ? v.value : null;
}

function evalInverse(compiled: CompiledFunction, symbols: SymbolTable, y: number): number | null {
  const v = evaluateExpression(compiled, { y }, symbols);
  return v.ok && Number.isFinite(v.value) ? v.value : null;
}

/** Screen-space curvature-aware recursive sampler for y=f(x). */
function sampleExplicitRec(
  compiled: CompiledFunction, symbols: SymbolTable,
  lo: number, hi: number, flo: number | null, fhi: number | null,
  worldPerPx: number, depth: number, out: Pt2[], budget: { samples: number },
): void {
  if (depth > MAX_DEPTH || budget.samples > MATH_LIMITS.maxSamples2d) { out.push({ x: hi, y: fhi === null ? NaN : fhi }); return; }
  const mid = (lo + hi) / 2;
  const fmid = evalY(compiled, symbols, mid);
  budget.samples += 1;
  if (fmid === null) {
    // Discontinuity inside the cell: recurse to isolate, splitting output.
    sampleExplicitRec(compiled, symbols, lo, mid, flo, null, worldPerPx, depth + 1, out, budget);
    out.push({ x: mid, y: NaN }); // hard break
    sampleExplicitRec(compiled, symbols, mid, hi, null, fhi, worldPerPx, depth + 1, out, budget);
    return;
  }
  // Flatness test in screen terms: the chord must be within a fraction of a
  // pixel of the midpoint value; huge slopes (near-vertical) force subdivision.
  const chord = flo !== null && fhi !== null ? ((flo + fhi) / 2) : null;
  const tolPx = 0.25 * worldPerPx;
  const flat = chord !== null && Math.abs(chord - fmid) <= tolPx;
  const slopeOk = flo !== null && fhi !== null && Math.abs(fhi - flo) <= 6 * (hi - lo);
  if (flat && slopeOk) { out.push({ x: hi, y: fhi === null ? NaN : fhi }); return; }
  sampleExplicitRec(compiled, symbols, lo, mid, flo, fmid, worldPerPx, depth + 1, out, budget);
  sampleExplicitRec(compiled, symbols, mid, hi, fmid, fhi, worldPerPx, depth + 1, out, budget);
}

function splitAtNaNs(points: readonly Pt2[]): Pt2[][] {
  const polylines: Pt2[][] = [];
  let current: Pt2[] = [];
  for (const p of points) {
    if (Number.isFinite(p.x) && Number.isFinite(p.y)) current.push(p);
    else if (current.length > 0) { polylines.push(current); current = []; }
  }
  if (current.length > 0) polylines.push(current);
  return polylines;
}

export function sampleExplicit(
  compiled: CompiledFunction, symbols: SymbolTable, viewport: WorldRect, domain: Domain2D | undefined,
): MathResult<PlotPaths2D> {
  const diagnostics: MathDiagnostic[] = [];
  const lo = Math.max(domain ? domain.min : -Infinity, viewport.minX - 2);
  const hi = Math.min(domain ? domain.max : Infinity, viewport.maxX + 2);
  if (!(lo < hi)) return mathOk({ polylines: [], diagnostics });
  const budget = { samples: 0 };
  const baseCount = 64;
  const points: Pt2[] = [];
  const worldPerPx = (viewport.maxX - viewport.minX) / 800;
  let prev: number | null = null;
  for (let i = 0; i <= baseCount; i++) {
    const x = lo + ((hi - lo) * i) / baseCount;
    const y = evalY(compiled, symbols, x);
    budget.samples += 1;
    points.push({ x, y: y === null ? NaN : y });
    if (i > 0 && prev !== null && y !== null && Math.abs(y - prev) > 4 * (hi - lo)) {
      diagnostics.push(warn("plot_large_jump", { args: { x } }));
    }
    prev = y;
  }
  // Refine each base segment adaptively (recursive midpoint insertion).
  const refined: Pt2[] = [{ x: lo, y: points[0]?.y ?? NaN }];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1] as Pt2;
    const b = points[i] as Pt2;
    if (Number.isFinite(a.y) && Number.isFinite(b.y)) {
      const segment: Pt2[] = [];
      sampleExplicitRec(compiled, symbols, a.x, b.x, a.y, b.y, worldPerPx, 1, segment, budget);
      refined.push(...segment);
    } else {
      refined.push({ x: b.x, y: NaN });
    }
  }
  if (budget.samples > MATH_LIMITS.maxSamples2d) diagnostics.push(warn("sample_budget_exceeded", { args: { samples: budget.samples } }));
  return mathOk({ polylines: splitAtAsymptotes(refined, viewport.maxY - viewport.minY), diagnostics });
}

export function sampleInverse(
  compiled: CompiledFunction, symbols: SymbolTable, viewport: WorldRect, domain: Domain2D | undefined,
): MathResult<PlotPaths2D> {
  // x=f(y): sample along y then swap coordinates; same machinery.
  const lo = Math.max(domain ? domain.min : -Infinity, viewport.minY - 2);
  const hi = Math.min(domain ? domain.max : Infinity, viewport.maxY + 2);
  if (!(lo < hi)) return mathOk({ polylines: [], diagnostics: [] });
  const raw = sampleExplicitAlong(compiled, symbols, "y", lo, hi, viewport.maxX - viewport.minX);
  return mathOk({ polylines: raw, diagnostics: [] });
}

function sampleExplicitAlong(compiled: CompiledFunction, symbols: SymbolTable, variable: string, lo: number, hi: number, valueRange: number): Pt2[][] {
  const count = 512;
  const points: Pt2[] = [];
  for (let i = 0; i <= count; i++) {
    const t = lo + ((hi - lo) * i) / count;
    const v = evaluateExpression(compiled, { [variable]: t }, symbols);
    const value = v.ok && Number.isFinite(v.value) ? v.value : NaN;
    if (variable === "y") points.push({ x: value, y: t });
    else points.push({ x: t, y: value });
  }
  // Same asymptote rule as the explicit sampler, applied to the value axis.
  return splitAtAsymptotes(points, valueRange);
}

export function sampleParametric2D(
  xCompiled: CompiledFunction, yCompiled: CompiledFunction, symbols: SymbolTable, tDomain: Domain2D,
): MathResult<PlotPaths2D> {
  const count = 1000;
  const points: Pt2[] = [];
  for (let i = 0; i <= count; i++) {
    const t = tDomain.min + ((tDomain.max - tDomain.min) * i) / count;
    const x = evaluateExpression(xCompiled, { t }, symbols);
    const y = evaluateExpression(yCompiled, { t }, symbols);
    points.push({ x: x.ok ? x.value : NaN, y: y.ok ? y.value : NaN });
  }
  return mathOk({ polylines: splitAtNaNs(points), diagnostics: [] });
}

/**
 * Split polylines at suspected vertical asymptotes: a sign flip whose
 * magnitude dwarfs the visible y-range is a pole crossing, not a real zero.
 * `yRange` is the world-space height of the current viewport.
 */
function splitAtAsymptotes(points: readonly Pt2[], yRange: number): Pt2[][] {
  const threshold = 4 * Math.max(yRange, 1e-9);
  const polylines: Pt2[][] = [];
  let current: Pt2[] = [];
  for (const p of points) {
    if (!(Number.isFinite(p.x) && Number.isFinite(p.y))) {
      if (current.length > 0) { polylines.push(current); current = []; }
      continue;
    }
    const prev = current[current.length - 1];
    if (prev && prev.y * p.y < 0 && Math.abs(prev.y) + Math.abs(p.y) > threshold) {
      polylines.push(current);
      current = [];
    }
    current.push(p);
  }
  if (current.length > 0) polylines.push(current);
  return polylines;
}

/** Polar r(θ): world point = (r cosθ, r sinθ). Negative r keeps the standard reflection. */
export function samplePolar(
  rCompiled: CompiledFunction, symbols: SymbolTable, thetaDomain: Domain2D,
): MathResult<PlotPaths2D> {
  const count = 1200;
  const points: Pt2[] = [];
  for (let i = 0; i <= count; i++) {
    const theta = thetaDomain.min + ((thetaDomain.max - thetaDomain.min) * i) / count;
    const r = evaluateExpression(rCompiled, { theta }, symbols);
    if (r.ok && Number.isFinite(r.value)) {
      points.push({ x: r.value * Math.cos(theta), y: r.value * Math.sin(theta) });
    } else {
      points.push({ x: NaN, y: NaN });
    }
  }
  return mathOk({ polylines: splitAtNaNs(points), diagnostics: [] });
}

/* ------------------------------ implicit contour ------------------------------ */

function evalImplicit(compiled: CompiledFunction, symbols: SymbolTable, x: number, y: number): number | null {
  const v = evaluateExpression(compiled, { x, y }, symbols);
  return v.ok && Number.isFinite(v.value) ? v.value : null;
}

/**
 * Marching squares on F(x,y)-level over a bounded box. Saddle cases use the
 * center sample for disambiguation. Domain edges stay open (no fake closure).
 */
export function implicitContour(
  compiled: CompiledFunction, symbols: SymbolTable, level: number, box: { x: Domain2D; y: Domain2D }, resolution: number,
): MathResult<PlotPaths2D> {
  const diagnostics: MathDiagnostic[] = [];
  const n = Math.min(Math.max(resolution, 16), MATH_LIMITS.maxImplicitGrid2d);
  const dx = (box.x.max - box.x.min) / n;
  const dy = (box.y.max - box.y.min) / n;
  const values: Array<number | null> = new Array((n + 1) * (n + 1)).fill(null);
  let anyValue = false;
  for (let j = 0; j <= n; j++) {
    for (let i = 0; i <= n; i++) {
      const v = evalImplicit(compiled, symbols, box.x.min + i * dx, box.y.min + j * dy);
      values[j * (n + 1) + i] = v;
      if (v !== null) anyValue = true;
    }
  }
  if (!anyValue) return mathFail([err("implicit_all_undefined")]);
  const segments: Array<[Pt2, Pt2]> = [];
  const lerp = (x0: number, x1: number, f0: number, f1: number): number => {
    const t = (0 - f0) / (f1 - f0);
    return x0 + Math.min(1, Math.max(0, t)) * (x1 - x0);
  };
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const idx = (jj: number, ii: number) => values[jj * (n + 1) + ii] ?? null;
      const f00 = idx(j, i);
      const f10 = idx(j, i + 1);
      const f11 = idx(j + 1, i + 1);
      const f01 = idx(j + 1, i);
      if (f00 === null || f10 === null || f11 === null || f01 === null) continue;
      const g00 = f00 - level, g10 = f10 - level, g11 = f11 - level, g01 = f01 - level;
      // Nudge exact-zero corners off the grid: contour-through-vertex cases
      // otherwise leave loose chain endpoints (classic marching-squares pinch).
      const nz = (g: number): number => (g === 0 ? 1e-12 : g);
      const c00 = nz(g00), c10 = nz(g10), c11 = nz(g11), c01 = nz(g01);
      const x0 = box.x.min + i * dx, x1 = x0 + dx;
      const y0 = box.y.min + j * dy, y1 = y0 + dy;
      const code = (c00 > 0 ? 1 : 0) | (c10 > 0 ? 2 : 0) | (c11 > 0 ? 4 : 0) | (c01 > 0 ? 8 : 0);
      if (code === 0 || code === 15) continue;
      const edgePoint = (edge: "bottom" | "right" | "top" | "left"): Pt2 => {
        switch (edge) {
          case "bottom": return { x: lerp(x0, x1, c00, c10), y: y0 };
          case "right": return { x: x1, y: lerp(y0, y1, c10, c11) };
          case "top": return { x: lerp(x0, x1, c01, c11), y: y1 };
          case "left": return { x: x0, y: lerp(y0, y1, c00, c01) };
        }
      };
      const push = (a: "bottom" | "right" | "top" | "left", b: "bottom" | "right" | "top" | "left") => {
        segments.push([edgePoint(a), edgePoint(b)]);
      };
      // Standard 16-case table; saddles (5/10) resolved by the center sample.
      const centerValue = evalImplicit(compiled, symbols, (x0 + x1) / 2, (y0 + y1) / 2);
      const centerPositive = centerValue === null ? null : centerValue - level > 0;
      switch (code) {
        case 1: case 14: push("left", "bottom"); break;
        case 2: case 13: push("bottom", "right"); break;
        case 3: case 12: push("left", "right"); break;
        case 4: case 11: push("right", "top"); break;
        case 6: case 9: push("bottom", "top"); break;
        case 7: case 8: push("left", "top"); break;
        case 5:
          if (centerPositive === true) { push("left", "top"); push("bottom", "right"); }
          else if (centerPositive === false) { push("left", "bottom"); push("right", "top"); }
          else push("left", "bottom");
          break;
        case 10:
          if (centerPositive === true) { push("left", "bottom"); push("right", "top"); }
          else if (centerPositive === false) { push("bottom", "right"); push("left", "top"); }
          else push("bottom", "right");
          break;
      }
    }
  }
  if (segments.length === 0) diagnostics.push(warn("implicit_no_contour", {}));
  return mathOk({ polylines: chainSegments(segments), diagnostics });
}

/** Greedy chaining of small segments into drawable polylines. */
function chainSegments(segments: readonly [Pt2, Pt2][]): Pt2[][] {
  const key = (p: Pt2) => `${p.x.toFixed(9)}:${p.y.toFixed(9)}`;
  const map = new Map<string, number[]>();
  segments.forEach((seg, i) => {
    const ka = key(seg[0] as Pt2), kb = key(seg[1] as Pt2);
    (map.get(ka) ?? map.set(ka, []).get(ka)!).push(i);
    (map.get(kb) ?? map.set(kb, []).get(kb)!).push(i);
  });
  const used = new Array<boolean>(segments.length).fill(false);
  const polylines: Pt2[][] = [];
  for (let i = 0; i < segments.length; i++) {
    if (used[i]) continue;
    used[i] = true;
    const start = segments[i] as [Pt2, Pt2];
    const line: Pt2[] = [start[0], start[1]];
    // Extend forward then backward.
    for (const direction of [1, -1] as const) {
      for (;;) {
        const tip = direction === 1 ? line[line.length - 1] as Pt2 : line[0] as Pt2;
        const candidates = map.get(key(tip)) ?? [];
        const nextIndex = candidates.find((ci) => !used[ci]);
        if (nextIndex === undefined) break;
        used[nextIndex] = true;
        const seg = segments[nextIndex] as [Pt2, Pt2];
        const otherEnd = key(seg[0] as Pt2) === key(tip) ? seg[1] : seg[0];
        if (direction === 1) line.push(otherEnd as Pt2);
        else line.unshift(seg[0] === tip ? seg[1] : seg[0]);
      }
    }
    polylines.push(line);
  }
  return polylines;
}

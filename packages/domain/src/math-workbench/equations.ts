/**
 * Equation solving: exact linear/quadratic branches, small dense linear
 * systems with partial pivoting, and bounded interval root finding with
 * bracket + safe secant refinement. Asymptote sign flips are rejected via
 * residual + local continuity probes; tangential roots get bounded local-min
 * probing. No "all roots guaranteed" claim is ever made.
 */

import { err, mathFail, mathOk, nearlyEqual, warn, type MathDiagnostic, type MathResult, MATH_LIMITS } from "./diagnostics.ts";
import { evaluateExpression, type CompiledFunction, type ExprNode, type SymbolTable } from "./expression.ts";
import { intersectLineCircle, intersectLineLine, intersectCircleCircle, type CirclePrimitive, type LinePrimitive } from "./geometry2d.ts";

/* ------------------------------ exact branches ------------------------------ */

export interface RootSet {
  roots: number[];
  /** Whether each root is simple (bracketed) or tangential/double. */
  kinds: ("simple" | "double")[];
  method: "linear" | "quadratic" | "numeric";
}

export interface SystemSolution {
  status: "unique" | "none" | "underdetermined";
  x?: number[];
  residual?: number;
  rank?: number;
}

export function solveLinear(a: number, b: number): MathResult<RootSet> {
  if (nearlyEqual(a, 0, 1e-12, 0)) {
    return nearlyEqual(b, 0, 1e-12, 0)
      ? mathFail([err("equation_infinite_solutions")])
      : mathFail([err("equation_no_solution")]);
  }
  return mathOk({ roots: [-b / a], kinds: ["simple"], method: "linear" });
}

/** Stable quadratic formula: avoids catastrophic cancellation on b±√D. */
export function solveQuadratic(a: number, b: number, c: number): MathResult<RootSet> {
  const scale = Math.max(Math.abs(a), Math.abs(b), Math.abs(c), 1);
  if (nearlyEqual(a, 0, 1e-12 * scale, 0)) return solveLinear(b, c);
  const disc = b * b - 4 * a * c;
  const eps = 1e-12 * scale * scale;
  if (disc < -eps) return mathOk({ roots: [], kinds: [], method: "quadratic" }, [warn("quadratic_no_real_roots")]);
  const clamped = Math.max(disc, 0);
  const root = Math.sqrt(clamped);
  if (clamped <= eps) {
    const r = -b / (2 * a);
    return mathOk({ roots: [r, r], kinds: ["double", "double"], method: "quadratic" });
  }
  const s = b >= 0 ? 1 : -1; // Math.sign(0) === 0 would zero out q
  const q = -0.5 * (b + s * root);
  const r1 = q / a;
  const r2 = c / q;
  const ordered = r1 <= r2 ? [r1, r2] : [r2, r1];
  return mathOk({ roots: ordered, kinds: ["simple", "simple"], method: "quadratic" });
}

/** 2×2 or 3×3 linear system via Gaussian elimination with partial pivoting. */
export function solveLinearSystem(A: number[][], b: number[]): MathResult<SystemSolution> {
  const n = b.length;
  if (n < 1 || n > 3 || A.length !== n || A.some((row) => row.length !== n)) {
    return mathFail([err("system_size_unsupported")]);
  }
  const M = A.map((row, i) => [...row, b[i] as number]);
  let rank = 0;
  for (let col = 0; col < n; col++) {
    let pivotRow = -1;
    let pivotVal = 0;
    for (let row = rank; row < n; row++) {
      const v = Math.abs(M[row]?.[col] ?? 0);
      if (v > pivotVal) { pivotVal = v; pivotRow = row; }
    }
    if (pivotRow < 0 || pivotVal < 1e-12) continue;
    const pr = M[pivotRow] as number[];
    if (pivotRow !== rank) { M[pivotRow] = M[rank] as number[]; M[rank] = pr; }
    const pivot = (M[rank] as number[])[col] as number;
    for (let j = col; j <= n; j++) ((M[rank] as number[])[j] as number) /= pivot;
    for (let row = 0; row < n; row++) {
      if (row === rank) continue;
      const factor = (M[row] as number[])[col] as number;
      if (Math.abs(factor) < 1e-15) continue;
      for (let j = col; j <= n; j++) ((M[row] as number[])[j] as number) -= factor * ((M[rank] as number[])[j] as number);
    }
    rank++;
  }
  const consistent = M.every((row, i) => {
    const coefficients = row.slice(0, n);
    const rhs = row[n] as number;
    return coefficients.every((v) => Math.abs(v) < 1e-9) ? Math.abs(rhs) < 1e-9 * Math.max(1, Math.abs(rhs)) : true;
  });
  if (rank === n) {
    const x = M.map((row) => row[n] as number);
    // Residual check against the original system.
    let residual = 0;
    for (let i = 0; i < n; i++) {
      let sum = 0;
      for (let j = 0; j < n; j++) sum += (A[i]?.[j] ?? 0) * (x[j] ?? 0);
      residual = Math.max(residual, Math.abs(sum - (b[i] ?? 0)));
    }
    return mathOk({ status: "unique", x, residual, rank });
  }
  if (!consistent) return mathOk({ status: "none", rank });
  return mathOk({ status: "underdetermined", rank });
}

/* ------------------------------ interval roots ------------------------------ */

export interface RootOptions {
  samples?: number;
  tolerance?: number;
}

/**
 * Numeric roots of f(x)=0 on a user-supplied interval: sign-change scan then
 * bracketed safe-secant/bisection refinement. Poles where the sign flips are
 * rejected by a residual + continuity probe; flat minima get bounded probing
 * for tangential roots. Undiscovered roots are simply not reported — the UI
 * says "not found with current interval/sampling".
 */
export function solveInterval(
  f: (x: number) => number,
  interval: readonly [number, number],
  options?: RootOptions,
): MathResult<RootSet> {
  const diagnostics: MathDiagnostic[] = [];
  const samples = Math.min(Math.max(options?.samples ?? 400, 32), 4000);
  const tol = options?.tolerance ?? 1e-10;
  const [lo, hi] = interval[0] <= interval[1] ? interval : [interval[1], interval[0]];
  const evalAt = (x: number): number | null => {
    const v = f(x);
    return Number.isFinite(v) ? v : null;
  };
  const xs: number[] = [];
  const fs: Array<number | null> = [];
  for (let i = 0; i <= samples; i++) {
    const x = lo + ((hi - lo) * i) / samples;
    xs.push(x);
    fs.push(evalAt(x));
  }
  const roots: number[] = [];
  const kinds: ("simple" | "double")[] = [];

  const refine = (a: number, fa: number, b: number, fb: number): number | null => {
    // Safeguarded secant (Illinois-style): falls back to bisection whenever
    // the secant step escapes the bracket.
    let left = a, fLeft = fa, right = b, fRight = fb;
    for (let iter = 0; iter < MATH_LIMITS.maxRootRefinements; iter++) {
      const width = right - left;
      if (width < tol * Math.max(1, Math.abs(left), Math.abs(right))) return (left + right) / 2;
      let next: number;
      const denom = fRight - fLeft;
      if (Math.abs(denom) > 1e-300) {
        next = left + ((width * fLeft) / (fLeft - fRight));
        if (!(next > left && next < right)) next = (left + right) / 2;
      } else {
        next = (left + right) / 2;
      }
      const fNext = evalAt(next);
      if (fNext === null) return null;
      if (nearlyEqual(fNext, 0, tol, tol)) return next;
      if (Math.sign(fNext) === Math.sign(fLeft)) { left = next; fLeft = fNext; }
      else { right = next; fRight = fNext; }
    }
    return (left + right) / 2;
  };

  const isPole = (x: number): boolean => {
    // A sign change across a pole: values blow up near x instead of crossing 0.
    const h = Math.max((hi - lo) * 1e-6, 1e-9) * 8;
    const left = evalAt(x - h);
    const right = evalAt(x + h);
    const at = evalAt(x);
    if (at !== null && Math.abs(at) < 1e-9) return false;
    if (left === null || right === null) return true;
    const magnitude = Math.max(Math.abs(left), Math.abs(right));
    return magnitude > 1e6 && Math.sign(left) !== Math.sign(right);
  };

  for (let i = 0; i < samples; i++) {
    const a = xs[i] as number;
    const b = xs[i + 1] as number;
    const fa = fs[i];
    const fb = fs[i + 1];
    if (fa == null || fb == null) continue;
    if (fa === 0) {
      roots.push(a);
      kinds.push("simple");
      continue;
    }
    if (fb === 0) continue; // the next segment's fa===0 reports this exact root
    if (Math.sign(fa) !== Math.sign(fb)) {
      if (isPole((a + b) / 2)) {
        diagnostics.push(warn("root_pole_rejected", { args: { x: (a + b) / 2 } }));
        continue;
      }
      const refined = refine(a, fa, b, fb);
      if (refined === null) continue;
      const residual = evalAt(refined);
      if (residual === null || Math.abs(residual) > 1e-6 * Math.max(1, Math.abs(refined) ** 0)) {
        diagnostics.push(warn("root_residual_failed", { args: { x: refined } }));
        continue;
      }
      roots.push(refined);
      kinds.push("simple");
    }
  }

  // Tangential (even-multiplicity) roots: bounded scan for local minima of |f|.
  let probes = 0;
  for (let i = 1; i < samples && probes < 64; i++) {
    const a = xs[i - 1] as number, m = xs[i] as number, b = xs[i + 1] as number;
    const fa = fs[i - 1], fm = fs[i], fb = fs[i + 1];
    if (fa == null || fm == null || fb == null) continue;
    if (Math.abs(fm) < Math.abs(fa) && Math.abs(fm) < Math.abs(fb) && Math.abs(fm) < 1e-4) {
      probes++;
      // Golden-section-ish shrink around m, bounded.
      let left = a, right = b;
      for (let iter = 0; iter < 60; iter++) {
        const mid = (left + right) / 2;
        const fMid = evalAt(mid);
        if (fMid === null) break;
        const fLeftOf = evalAt((left + mid) / 2);
        const fRightOf = evalAt((mid + right) / 2);
        if (fLeftOf === null || fRightOf === null) break;
        if (Math.abs(fLeftOf) < Math.abs(fRightOf)) right = mid;
        else left = mid;
        if (right - left < tol * Math.max(1, Math.abs(m))) break;
      }
      const candidate = (left + right) / 2;
      const value = evalAt(candidate);
      if (value !== null && Math.abs(value) < 1e-7) {
        // A touching minimum is double-multiplicity even when a sample landed
        // exactly on it and already reported it as a simple root.
        const existing = roots.findIndex((r) => nearlyEqual(r, candidate, 1e-6, 1e-9));
        if (existing >= 0) kinds[existing] = "double";
        else { roots.push(candidate); kinds.push("double"); }
        diagnostics.push(warn("root_tangential", { args: { x: candidate } }));
      }
    }
  }

  roots.sort((p, q) => p - q);
  const mergedRoots: number[] = [];
  const mergedKinds: ("simple" | "double")[] = [];
  roots.forEach((r, i) => {
    if (mergedRoots.length > 0 && nearlyEqual(r, mergedRoots[mergedRoots.length - 1] as number, 1e-9 * Math.max(1, Math.abs(r)))) return;
    mergedRoots.push(r);
    mergedKinds.push(kinds[i] as "simple" | "double");
  });
  return mathOk({ roots: mergedRoots, kinds: mergedKinds, method: "numeric" }, diagnostics);
}

/** Solve `lhs = rhs` for a variable over an interval using compiled ASTs. */
export function solveEquationInterval(
  compiledLhs: CompiledFunction,
  compiledRhs: CompiledFunction,
  variable: string,
  interval: readonly [number, number],
  symbols: SymbolTable = { variables: [], functions: new Map() },
  options?: RootOptions,
): MathResult<RootSet> {
  const f = (x: number): number => {
    const vars = { [variable]: x };
    const l = evaluateExpression(compiledLhs, vars, symbols);
    const r = evaluateExpression(compiledRhs, vars, symbols);
    if (!l.ok || !r.ok) return NaN;
    return l.value - r.value;
  };
  return solveInterval(f, interval, options);
}

/** 2D curve intersections reuse the root machinery through geometry helpers. */
export function intersectionPoints(
  kind: "line-line" | "line-circle" | "circle-circle",
  a: LinePrimitive | CirclePrimitive,
  b: LinePrimitive | CirclePrimitive,
): Pt2Export[] {
  switch (kind) {
    case "line-line": return intersectLineLine(a as LinePrimitive, b as LinePrimitive);
    case "line-circle": return intersectLineCircle(a as LinePrimitive, b as CirclePrimitive);
    case "circle-circle": return intersectCircleCircle(a as CirclePrimitive, b as CirclePrimitive);
  }
}

type Pt2Export = { x: number; y: number };

/** Polynomial coefficient extraction for x^n forms used by exact solvers. */
export function polyFromNodes(nodes: readonly { coefficient: number; power: number }[]): number[] {
  const degree = Math.max(0, ...nodes.map((n) => n.power));
  const coeffs = new Array<number>(degree + 1).fill(0);
  for (const n of nodes) coeffs[n.power] = (coeffs[n.power] as number) + n.coefficient;
  return coeffs;
}

/** Try exact solving when the equation is polynomial of degree ≤ 2 in `variable`. */
export function tryExactPolynomial(
  lhs: ExprNode,
  rhs: ExprNode,
  variable: string,
): { exact: true; result: MathResult<RootSet> } | { exact: false } {
  // Flatten lhs-rhs into coefficient/power pairs for degree ≤ 2 polynomials.
  const terms: Array<{ coefficient: number; power: number }> = [];
  const walk = (node: ExprNode, sign: number, depth: number): boolean => {
    if (depth > 32) return false;
    switch (node.kind) {
      case "num": terms.push({ coefficient: sign * node.value, power: 0 }); return true;
      case "neg": return walk(node.arg, -sign, depth + 1);
      case "bin":
        if (node.op === "+" || node.op === "-") {
          return walk(node.lhs, sign, depth + 1) && walk(node.rhs, node.op === "-" ? -sign : sign, depth + 1);
        }
        if (node.op === "*" && node.lhs.kind === "num") {
          const save = terms.length;
          if (!walk(node.rhs, sign, depth + 1)) return false;
          for (let i = save; i < terms.length; i++) (terms[i] as { coefficient: number }).coefficient *= node.lhs.value;
          return true;
        }
        if (node.op === "/" && node.rhs.kind === "num" && node.rhs.value !== 0) {
          const save = terms.length;
          if (!walk(node.lhs, sign, depth + 1)) return false;
          for (let i = save; i < terms.length; i++) (terms[i] as { coefficient: number }).coefficient /= node.rhs.value;
          return true;
        }
        if (node.op === "^" && node.rhs.kind === "num" && Number.isInteger(node.rhs.value) && node.rhs.value >= 0 && node.rhs.value <= 2) {
          const power = node.rhs.value;
          if (power === 0) { terms.push({ coefficient: sign, power: 0 }); return true; }
          if (node.lhs.kind === "var" && node.lhs.name === variable) { terms.push({ coefficient: sign, power }); return true; }
          return false;
        }
        return false;
      case "var":
        if (node.name === variable) terms.push({ coefficient: sign, power: 1 });
        else return false;
        return true;
      default: return false;
    }
  };
  const diff: ExprNode = { kind: "bin", op: "-", lhs, rhs, start: lhs.start, end: rhs.end };
  if (!walk(diff, 1, 0)) return { exact: false };
  const coeffs = polyFromNodes(terms);
  if (coeffs.length > 3) return { exact: false };
  const [c2, c1, c0] = [coeffs[2] ?? 0, coeffs[1] ?? 0, coeffs[0] ?? 0];
  if (c2 === 0) return { exact: true, result: solveLinear(c1, c0) };
  return { exact: true, result: solveQuadratic(c2, c1, c0) };
}

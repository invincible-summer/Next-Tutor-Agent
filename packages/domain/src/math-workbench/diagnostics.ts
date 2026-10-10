/**
 * Math workbench: centralized limits, typed diagnostics and numeric tolerance.
 * Every module reads budget/epsilon constants from here so no subsystem grows
 * its own private `1e-6`. Diagnostics codes are stable public contracts used
 * by the Web i18n layer; never inline user-facing text in the domain.
 */

export interface MathDiagnostic {
  code: string;
  severity: "info" | "warning" | "error";
  objectId?: string;
  range?: { start: number; end: number };
  args?: Record<string, number | string>;
}

export type MathResult<T> =
  | { ok: true; value: T; diagnostics: MathDiagnostic[] }
  | { ok: false; diagnostics: MathDiagnostic[] };

export function mathOk<T>(value: T, diagnostics: MathDiagnostic[] = []): MathResult<T> {
  return { ok: true, value, diagnostics };
}

export function mathFail<T = never>(diagnostics: MathDiagnostic[]): MathResult<T> {
  return { ok: false, diagnostics };
}

export function err(code: string, extra?: Omit<MathDiagnostic, "code" | "severity">): MathDiagnostic {
  return { code, severity: "error", ...extra };
}

export function warn(code: string, extra?: Omit<MathDiagnostic, "code" | "severity">): MathDiagnostic {
  return { code, severity: "warning", ...extra };
}

export function info(code: string, extra?: Omit<MathDiagnostic, "code" | "severity">): MathDiagnostic {
  return { code, severity: "info", ...extra };
}

/** Hard budgets for the whole workbench (plan G4; tune from real device runs). */
export interface MathLimits {
  maxExpressionChars: number;
  maxTokens: number;
  maxAstNodes: number;
  maxAstDepth: number;
  maxCallArgs: number;
  maxEvalNodeVisits: number;
  maxCallDepth: number;
  maxObjects2d: number;
  maxPlots2d: number;
  maxObjects3d: number;
  maxPlots3d: number;
  maxParameters: number;
  maxNamedFunctions: number;
  maxUndoEntries: number;
  maxSavedDocuments: number;
  maxDocumentBytes: number;
  maxSamples2d: number;
  maxImplicitGrid2d: number;
  surfaceGridNormal: number;
  surfaceGridHigh: number;
  maxImplicitGrid3d: number;
  maxTriangles3d: number;
  maxIntegralEvaluations: number;
  maxRootRefinements: number;
}

export const MATH_LIMITS: MathLimits = {
  maxExpressionChars: 2000,
  maxTokens: 1024,
  maxAstNodes: 1024,
  maxAstDepth: 64,
  maxCallArgs: 16,
  maxEvalNodeVisits: 200_000,
  maxCallDepth: 32,
  maxObjects2d: 500,
  maxPlots2d: 32,
  maxObjects3d: 200,
  maxPlots3d: 16,
  maxParameters: 64,
  maxNamedFunctions: 64,
  maxUndoEntries: 100,
  maxSavedDocuments: 40,
  maxDocumentBytes: 512 * 1024,
  maxSamples2d: 20_000,
  maxImplicitGrid2d: 220,
  surfaceGridNormal: 80,
  surfaceGridHigh: 140,
  maxImplicitGrid3d: 48,
  maxTriangles3d: 100_000,
  maxIntegralEvaluations: 20_000,
  maxRootRefinements: 200,
};

export interface EvalBudget {
  nodeVisits: number;
  callDepth: number;
}

export function freshBudget(limits: MathLimits = MATH_LIMITS): EvalBudget {
  return { nodeVisits: limits.maxEvalNodeVisits, callDepth: limits.maxCallDepth };
}

/** Combined relative+absolute comparison; never use bare `x === y` on computed floats. */
export function nearlyEqual(a: number, b: number, relTol = 1e-9, absTol = 1e-12): boolean {
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  const diff = Math.abs(a - b);
  if (diff <= absTol) return true;
  return diff <= relTol * Math.max(Math.abs(a), Math.abs(b));
}

/** Tolerance that adapts to scene magnitude, for geometry epsilon decisions. */
export function geometryEpsilon(scale: number): number {
  const magnitude = Math.max(1, Math.abs(scale));
  return 1e-9 * magnitude;
}

export function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/** Clamp helper shared by samplers so domains stay finite before evaluation. */
export function clampFinite(v: number, lo = -1e12, hi = 1e12): number {
  if (!Number.isFinite(v)) return lo;
  return Math.min(hi, Math.max(lo, v));
}

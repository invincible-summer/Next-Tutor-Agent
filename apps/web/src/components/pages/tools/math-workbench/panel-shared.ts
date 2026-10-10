/** Small shared helpers for the workbench side panels (no React here so
 * panel modules can import freely without cycles). */

/** Curve/object palette (light-paper oriented; dark theme lifts strokes). */
export const PALETTE = ["#2f7d6e", "#b0563c", "#3a5f9e", "#7a5aa0", "#8a7a2f", "#a03a5a"] as const;

export const TWO_PI = Math.PI * 2;

/** Compact human-readable number for readouts and labels. */
export function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (Number.isInteger(value) && Math.abs(value) < 1e15) return String(value);
  const abs = Math.abs(value);
  if (abs >= 1e6 || abs < 1e-4) return value.toExponential(4);
  return String(Math.round(value * 1e6) / 1e6);
}

/** Strip a friendly `y =` / `z =` / `f(x) =` left side before compiling. */
export function stripLhsPrefix(text: string, kind: string): string {
  let out = text.trim();
  const patterns: RegExp = kind === "inverse"
    ? /^(?:x|g\s*\(\s*y\s*\))\s*=\s*/i
    : kind === "explicitSurface"
      ? /^(?:z|f\s*\(\s*x\s*,\s*y\s*\))\s*=\s*/i
      : /^(?:y|f\s*\(\s*x\s*\))\s*=\s*/i;
  out = out.replace(patterns, "");
  return out;
}

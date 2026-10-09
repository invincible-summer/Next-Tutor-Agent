const PREFIXES: [number, string][] = [[1e9, "G"], [1e6, "M"], [1e3, "k"], [1, ""], [1e-3, "m"], [1e-6, "μ"], [1e-9, "n"], [1e-12, "p"]];
export function formatElectrical(value: number | null | undefined, unit = "", digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (Math.abs(value) < 1e-13) return `0${unit ? ` ${unit}` : ""}`;
  const [scale, prefix] = PREFIXES.find(([v]) => Math.abs(value) >= v) ?? [1e-12, "p"];
  const n = Number((value / scale).toPrecision(digits));
  return `${n} ${prefix}${unit}`.trim();
}
/** Parse a numeric SI value. Unit suffix must match the field, not arbitrary text. */
export function parseElectrical(text: string, unit = ""): number | null {
  const match = /^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s*([GMkmunpμµ]?)([A-Za-zΩβ]*)\s*$/i.exec(text);
  if (!match) return null;
  const suffix = match[3] ?? "";
  if (suffix && suffix.toLowerCase() !== unit.toLowerCase() && !(unit === "Ω" && suffix.toLowerCase() === "ohm")) return null;
  const scales: Record<string, number> = { G: 1e9, M: 1e6, k: 1e3, K: 1e3, "": 1, m: 1e-3, u: 1e-6, μ: 1e-6, µ: 1e-6, n: 1e-9, p: 1e-12 };
  const scale = scales[match[2] ?? ""];
  // Normalize the multiplication result so common classroom values such as
  // `10 μF` compare like their SI literal (`10e-6`) instead of retaining a
  // one-ulp floating point tail.
  const value = Number.parseFloat((Number(match[1]) * (scale ?? NaN)).toPrecision(15));
  return Number.isFinite(value) ? value : null;
}

/**
 * Integer-only value primitives for the chem-lab engine.
 * Line-by-line mirror of services/api/app/chem_lab/engine/model.py — any
 * semantic change must land in both files in the same commit, and the shared
 * replay vectors (content/vectors) pin the two implementations together.
 *
 * Everything is an integer: volumes in microlitres (`_uL`), amounts in
 * micromoles (`_umol`), temperatures in milli-°C, pH in milli-pH, ratios in
 * permille and time in milliseconds. Note umol/uL === mol/L.
 */

export const SCHEMA_VERSION = 1;
export const DT_MS = 500;
export const ACTION_MS = 2000;
export const MAX_RULE_FIRINGS_PER_COMMAND = 64;
export const MAX_EVENTS_PER_SESSION = 2000;
export const MAX_CHECKPOINTS_PER_SESSION = 40;

export const ROOM_TEMPERATURE_MILLI_C = 25000;
export const PH_NEUTRAL_MILLI = 7000;
export const PH_SCALE_MILLI = 14000;

/** 1000*log10(1 + i/10) for i in 0..90 — identical literal to the Python side. */
export const LOG10_MILLI_TABLE: readonly number[] = [
  0, 41, 79, 114, 146, 176, 204, 230, 255, 279, 301, 322, 342, 362, 380,
  398, 415, 431, 447, 462, 477, 491, 505, 519, 531, 544, 556, 568, 580,
  591, 602, 613, 623, 633, 643, 653, 663, 672, 681, 690, 699, 708, 716,
  724, 732, 740, 748, 756, 763, 771, 778, 785, 792, 799, 806, 813, 820,
  826, 833, 839, 845, 851, 857, 863, 869, 875, 881, 886, 892, 898, 903,
  908, 914, 919, 924, 929, 934, 940, 944, 949, 954, 959, 964, 968, 973,
  978, 982, 987, 991, 996, 1000,
];

/** Python floor division for the non-negative integer domain the engine uses. */
export function idiv(a: number, b: number): number {
  return Math.floor(a / b);
}

/** floor(1000 * log10(value)) for integer value >= 1, table-based. */
export function milliLog10(value: number): number {
  if (value < 1) throw new Error("milliLog10 requires value >= 1");
  let exponent = 0;
  let scaled = value;
  while (scaled >= 10) {
    scaled = idiv(scaled, 10);
    exponent += 1;
  }
  const sig = exponent >= 2 ? idiv(value, 10 ** (exponent - 2)) : value * 10 ** (2 - exponent);
  const idx = idiv(sig, 10) - 10;
  const rem = sig % 10;
  const base = LOG10_MILLI_TABLE[idx] as number;
  const next = LOG10_MILLI_TABLE[idx + 1] as number;
  return exponent * 1000 + base + idiv((next - base) * rem, 10);
}

/** FNV-1a 64-bit over UTF-8 bytes; 16-char lowercase hex. */
export function fnv1a64(text: string): string {
  // BigInt() 构造而非字面量：部分消费方 tsconfig target 低于 ES2020。
  let h = BigInt("14695981039346656037");
  const prime = BigInt("1099511628211");
  const mask = BigInt("0xffffffffffffffff");
  for (const byte of new TextEncoder().encode(text)) {
    h ^= BigInt(byte);
    h = (h * prime) & mask;
  }
  return h.toString(16).padStart(16, "0");
}

function canonicalString(value: string): string {
  let out = '"';
  for (const ch of value) {
    const code = ch.codePointAt(0) as number;
    if (ch === '"') out += '\\"';
    else if (ch === "\\") out += "\\\\";
    else if (code < 0x20) out += `\\u${code.toString(16).padStart(4, "0")}`;
    else out += ch;
  }
  return out + '"';
}

/**
 * Canonical JSON: sorted keys, no whitespace, minimal escaping.
 * Only None/bool/int/str/list/dict shapes — non-integer numbers are rejected
 * outright so floats can never leak into the rule layer.
 */
export function canonicalJson(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "number") {
    if (!Number.isInteger(value)) throw new TypeError("floats are not allowed in chem-lab state");
    return String(value);
  }
  if (typeof value === "string") return canonicalString(value);
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const parts: string[] = [];
    for (const key of Object.keys(obj).sort()) {
      parts.push(`${canonicalString(key)}:${canonicalJson(obj[key])}`);
    }
    return `{${parts.join(",")}}`;
  }
  throw new TypeError(`unsupported state value: ${typeof value}`);
}

export type AnyDict = Record<string, any>;

/** Hash the chemistry-relevant projection (guidance/observations excluded). */
export function stateHash(state: AnyDict): string {
  const projection = {
    schema_version: state.schema_version,
    experiment_id: state.experiment_id,
    pack_version: state.pack_version,
    pack_hash: state.pack_hash,
    session_seed: state.session_seed,
    mode: state.mode,
    phase: state.phase,
    revision: state.revision,
    seq: state.seq,
    sim_time_ms: state.sim_time_ms,
    vessels: state.vessels,
    equipment: state.equipment,
    environment: state.environment,
    goals: state.goals,
    visible: state.visible,
  };
  return fnv1a64(canonicalJson(projection));
}

// ---------------------------------------------------------------------------
// Amount helpers (all integer, floor semantics shared with the Python engine)
// ---------------------------------------------------------------------------

/** Re-key a species map in sorted order, dropping zero entries. */
export function sortedSpecies(amounts: AnyDict): AnyDict {
  const out: AnyDict = {};
  for (const key of Object.keys(amounts).sort()) {
    if (amounts[key] !== 0) out[key] = amounts[key];
  }
  return out;
}

export function addAmount(amounts: AnyDict, species: string, delta: number): void {
  if (delta === 0) return;
  const value = (amounts[species] ?? 0) + delta;
  if (value < 0) throw new Error(`species amount underflow: ${species}`);
  if (value === 0) delete amounts[species];
  else amounts[species] = value;
}

/** Solute amounts carried by `amount_uL` of a well-mixed liquid. */
export function transferAmounts(contents: AnyDict, amountUL: number, volumeUL: number): AnyDict {
  if (amountUL <= 0 || volumeUL <= 0) return {};
  const moved: AnyDict = {};
  for (const species of Object.keys(contents).sort()) {
    const qty = idiv(contents[species] * amountUL, volumeUL);
    if (qty > 0) moved[species] = qty;
  }
  return moved;
}

export function applyTransfer(source: AnyDict, target: AnyDict, moved: AnyDict): void {
  for (const species of Object.keys(moved).sort()) {
    addAmount(source, species, -moved[species]);
    addAmount(target, species, moved[species]);
  }
}

export function total(amounts: AnyDict): number {
  return Object.values(amounts).reduce((sum: number, value) => sum + (value as number), 0);
}

/** Linear integer interpolation over sorted [x, y] points. */
export function interpolateTable(points: number[][], x: number): number {
  const first = points[0];
  const last = points[points.length - 1];
  if (!first || !last) throw new Error("empty interpolation table");
  if (x <= (first[0] as number)) return first[1] as number;
  if (x >= (last[0] as number)) return last[1] as number;
  for (let index = 1; index < points.length; index += 1) {
    const point = points[index] as number[];
    const x1 = point[0] as number;
    const y1 = point[1] as number;
    if (x <= x1) {
      const prev = points[index - 1] as number[];
      const x0 = prev[0] as number;
      const y0 = prev[1] as number;
      return y0 + idiv((y1 - y0) * (x - x0), x1 - x0);
    }
  }
  return last[1] as number;
}

/**
 * Water-like heat capacity: 4.18 J/(g·K); 1 uL ≈ 1 mg water, so heating one
 * microlitre by one millidegree costs ~4.18 microjoules.
 */
export const HEAT_CAPACITY_MICRO_J_PER_UL_K = 4180;

export function temperatureDeltaMilliC(vessel: AnyDict, microJ: number): number {
  const volume = vessel.volume_uL;
  if (microJ <= 0 || volume <= 0) return 0;
  return idiv(microJ * 1000, volume * HEAT_CAPACITY_MICRO_J_PER_UL_K);
}

/** Lumped Newton cooling per DT step: 0.6% of the excess temperature. */
export function coolingDropMilliC(vessel: AnyDict, roomMilliC: number): number {
  const excess = vessel.temperature_milli_c - roomMilliC;
  if (excess <= 0) return 0;
  return idiv(excess * 6, 1000);
}

/** Build a semantic event and assign its sequence number. */
export function makeEvent(
  state: AnyDict,
  kind: string,
  commandId: string,
  options: { vesselId?: string; ruleId?: string; data?: AnyDict } = {},
): AnyDict {
  state.seq += 1;
  return {
    seq: state.seq,
    kind,
    command_id: commandId,
    sim_time_ms: state.sim_time_ms,
    vessel_id: options.vesselId ?? "",
    rule_id: options.ruleId ?? "",
    data: { ...(options.data ?? {}) },
  };
}

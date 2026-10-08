/**
 * RenderFrame → SVG display props. Pure projection: every chemical value
 * (fill, colour token, turbidity, bubbles, precipitate, steam, temperature
 * band, highlights) already lives in the server/worker-computed RenderFrame;
 * this module only normalises permille → 0..1 and resolves the pack's
 * visual_theme colour hexes and bilingual labels. No chemistry happens here,
 * and JSX must never special-case a species — the pack drives everything.
 */
import type {
  ChemLabRenderFrame,
  ChemLabVesselFrame,
} from "@/lib/api-chem-lab";

export interface VesselVisual {
  vesselId: string;
  fillRatio: number;
  liquidColor: string | null;
  liquidLabel: string;
  opacity: number;
  turbidity: number;
  precipitate: { amount: number; color: string | null; texture: string } | null;
  bubbles: { rate: number; size: number; gasLabel: string } | null;
  steam: number;
  temperatureBand: string;
  highlight: string | null;
  temperatureMilliC: number | null;
  volumeUL: number;
  capacityUL: number;
  mixPermille: number;
  graduations: number[];
}

export interface InstrumentVisual {
  equipmentId: string;
  reading: string;
  unit: string;
  status: string;
}

type AnyRecord = Record<string, unknown>;

function permille(value: unknown): number {
  const n = Number(value ?? 0);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return Math.min(1, n / 1000);
}

/** visual_theme lookup tables shipped inside the experiment pack. */
export function themeColors(pack: AnyRecord | null | undefined): {
  hex: Record<string, string>;
  labels: Record<string, AnyRecord>;
} {
  const theme = (pack?.visual_theme ?? {}) as AnyRecord;
  const hex = (theme.color_hex ?? {}) as Record<string, string>;
  const labels = (theme.color_tokens ?? {}) as Record<string, AnyRecord>;
  return { hex, labels };
}

export function colorHex(pack: AnyRecord | null | undefined, token: string | null | undefined): string | null {
  if (!token) return null;
  const { hex } = themeColors(pack);
  return typeof hex[token] === "string" ? hex[token] : null;
}

export function colorLabel(
  pack: AnyRecord | null | undefined,
  token: string | null | undefined,
  language: string,
): string {
  if (!token) return "";
  const { labels } = themeColors(pack);
  const entry = labels[token];
  if (!entry) return token;
  const localized = entry[language] ?? entry.zh ?? entry.en;
  return typeof localized === "string" ? localized : token;
}

/** Graduation marks as fill fractions (0..1) from the equipment def. */
export function graduationFractions(def: AnyRecord | null | undefined): number[] {
  const capacity = Number(def?.capacity_uL ?? 0);
  const marks = def?.graduations_uL;
  if (!capacity || !Array.isArray(marks)) return [];
  return marks
    .map((mark) => Number(mark) / capacity)
    .filter((mark) => mark > 0 && mark < 1);
}

function vesselFrameOf(frame: ChemLabRenderFrame | null, vesselId: string): ChemLabVesselFrame | null {
  const vessels = frame?.vessels ?? {};
  return vessels[vesselId] ?? null;
}

function highlightOf(frame: ChemLabRenderFrame | null, vesselId: string): string | null {
  for (const entry of frame?.highlights ?? []) {
    if (String((entry as AnyRecord).vessel_id ?? "") === vesselId) {
      return String((entry as AnyRecord).kind ?? "") || null;
    }
  }
  return null;
}

/**
 * Per-vessel visual props. `state` is the candidate engine state (server or
 * worker prediction) used for exact volume/temperature readouts; the frame
 * alone decides what is *visible*.
 */
export function vesselVisual(
  pack: AnyRecord | null | undefined,
  frame: ChemLabRenderFrame | null,
  state: AnyRecord | null | undefined,
  vesselId: string,
  language: string,
): VesselVisual {
  const view = vesselFrameOf(frame, vesselId);
  const vessels = (state?.vessels ?? {}) as AnyRecord;
  const vessel = (vessels[vesselId] ?? {}) as AnyRecord;
  const equipmentDefs = (pack?._equipment_defs ?? pack?.equipment_defs ?? {}) as AnyRecord;
  const def = (equipmentDefs[String(vessel.kind ?? "")] ?? null) as AnyRecord | null;
  const precipitate = view?.precipitate as AnyRecord | null | undefined;
  const bubbles = view?.bubbles as AnyRecord | null | undefined;
  const token = view?.liquid_color_token ?? null;
  return {
    vesselId,
    fillRatio: permille(view?.fill_ratio_permille),
    liquidColor: colorHex(pack, token),
    liquidLabel: colorLabel(pack, view?.liquid_label ?? token, language),
    opacity: permille(view?.opacity_permille),
    turbidity: permille(view?.turbidity_permille),
    precipitate: precipitate
      ? {
          amount: permille(precipitate.amount_permille),
          color: colorHex(pack, String(precipitate.color_token ?? "")),
          texture: String(precipitate.texture ?? ""),
        }
      : null,
    bubbles: bubbles
      ? {
          rate: permille(bubbles.rate_permille),
          size: permille(bubbles.size_permille),
          gasLabel: String(bubbles.gas_label ?? ""),
        }
      : null,
    steam: permille(view?.steam_permille),
    temperatureBand: String(view?.temperature_band ?? "room"),
    highlight: highlightOf(frame, vesselId),
    temperatureMilliC:
      typeof vessel.temperature_milli_c === "number" ? vessel.temperature_milli_c : null,
    volumeUL: Number(vessel.volume_uL ?? 0),
    capacityUL: Number(vessel.capacity_uL ?? 0),
    mixPermille: Number(vessel.mix_permille ?? 0),
    graduations: graduationFractions(def),
  };
}

export function instrumentVisuals(frame: ChemLabRenderFrame | null): InstrumentVisual[] {
  const instruments = (frame?.instruments ?? {}) as Record<string, AnyRecord>;
  return Object.keys(instruments)
    .sort()
    .map((equipmentId) => {
      const row = instruments[equipmentId]!;
      return {
        equipmentId,
        reading: String(row.reading ?? ""),
        unit: String(row.unit ?? ""),
        status: String(row.status ?? "idle"),
      };
    });
}

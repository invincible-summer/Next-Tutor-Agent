/**
 * Projection: LabState → RenderFrame, public observations, species ledger —
 * mirror of projection.py. Colors come from pack-declared concentration
 * bands and indicator pH bands (never free-form CSS).
 */
import {
  idiv,
  milliLog10,
  PH_NEUTRAL_MILLI,
  PH_SCALE_MILLI,
  total,
  type AnyDict,
} from "./units.ts";

export const VISIBLE_CONC_DEFAULT_UMOL_PER_UL_MILLI = 1;
export const BUBBLE_VISIBLE_RATE_PERMILLE = 120;
export const STEAM_VISIBLE_MILLI_C = 55000;

/**
 * Strong-acid/strong-base teaching approximation (declared in the pack).
 * Concentration umol/uL == mol/L: pH = -log10([H+]). Returns null when the
 * pack declares no pH model.
 */
export function computePhMilli(vessel: AnyDict, phModel: string): number | null {
  if (phModel !== "strong_binary_v1" || vessel.volume_uL <= 0) return null;
  const hPlus = vessel.contents.h_plus ?? 0;
  const ohMinus = vessel.contents.oh_minus ?? 0;
  const net = hPlus - ohMinus;
  const volume = vessel.volume_uL;
  // |net|/V <= 1e-7 mol/L  ⇔  |net| * 1e7 <= V  → neutral within the model.
  if (Math.abs(net) * 10000000 <= volume) return PH_NEUTRAL_MILLI;
  if (net > 0) return -(milliLog10(net) - milliLog10(volume));
  return PH_SCALE_MILLI + (milliLog10(-net) - milliLog10(volume));
}

/** (color_token, opacity_permille) from concentration bands. */
function dominantColor(vessel: AnyDict, speciesDefs: AnyDict): [string, number] {
  const volume = vessel.volume_uL;
  if (volume <= 0) return ["clear", 0];
  let bestToken = "clear";
  let bestOpacity = 0;
  let bestStrength = 0;
  for (const speciesId of Object.keys(vessel.contents).sort()) {
    const species = speciesDefs[speciesId];
    // Solid-phase species tint their precipitate layer, never the liquid.
    if (!species || species.state === "solid") continue;
    if (!species.color_token || species.color_token === "clear") continue;
    const concMilli = idiv(vessel.contents[speciesId] * 1000, volume);
    const threshold = Math.max(1, species.visible_conc_umol_per_ul_milli || VISIBLE_CONC_DEFAULT_UMOL_PER_UL_MILLI);
    if (concMilli < threshold) continue;
    const strength = species.color_strength_permille ?? 500;
    if (strength < bestStrength) continue;
    const opacity = Math.min(1000, 300 + idiv(concMilli * 700, threshold * 4));
    if (species.ph_bands && species.ph_bands.length > 0 && vessel.ph_milli !== null) {
      let token = species.color_token as string;
      for (const band of species.ph_bands as [number, string][]) {
        if (vessel.ph_milli <= Number(band[0])) {
          token = String(band[1]);
          break;
        }
      }
      bestToken = token;
      bestOpacity = Math.min(1000, opacity + 200);
      bestStrength = strength;
    } else {
      bestToken = species.color_token;
      bestOpacity = opacity;
      bestStrength = strength;
    }
  }
  return [bestToken, bestOpacity];
}

/** Suspended (not yet settled) solid fraction → turbidity permille. */
function turbidity(vessel: AnyDict, speciesDefs: AnyDict): number {
  if (vessel.volume_uL <= 0) return 0;
  let suspendedUmol = 0;
  for (const [speciesId, amount] of Object.entries(vessel.solids)) {
    const species = speciesDefs[speciesId];
    if (species && species.solubility_table && species.solubility_table.length > 0) continue;
    suspendedUmol += amount as number;
  }
  if (suspendedUmol <= 0) return 0;
  const concentrationMilli = idiv(suspendedUmol * 1000000, vessel.volume_uL);
  const base = Math.min(900, idiv(concentrationMilli, 20));
  return idiv(base * (1000 - vessel.settled_permille), 1000);
}

function precipitateLayer(vessel: AnyDict, speciesDefs: AnyDict): AnyDict | null {
  let totalSolid = 0;
  let token = "precipitate.white";
  let texture = "powder";
  for (const speciesId of Object.keys(vessel.solids).sort()) {
    const species = speciesDefs[speciesId];
    const amount = vessel.solids[speciesId] as number;
    if (amount <= 0) continue;
    if (species && species.solubility_table && species.solubility_table.length > 0) texture = "flakes";
    totalSolid += amount;
    if (species && species.color_token && species.color_token !== "clear") {
      token = species.color_token;
    }
  }
  if (totalSolid <= 0) return null;
  let threshold = 1;
  for (const speciesId of Object.keys(vessel.solids)) {
    const species = speciesDefs[speciesId];
    if (species && species.visible_amount_umol) {
      threshold = species.visible_amount_umol;
      break;
    }
  }
  const amountPermille = Math.min(1000, idiv(totalSolid * 1000, threshold * 4));
  return {
    amount_permille: amountPermille,
    color_token: token,
    texture: vessel.settled_permille >= 700 ? "layer" : texture,
  };
}

function temperatureBand(milliC: number): string {
  if (milliC < 10000) return "cold";
  if (milliC < 35000) return "room";
  if (milliC < 65000) return "warm";
  return "hot";
}

export function vesselFrame(state: AnyDict, vessel: AnyDict, speciesDefs: AnyDict): AnyDict {
  const [token, opacity] = dominantColor(vessel, speciesDefs);
  let bubbles: AnyDict | null = null;
  const gasTotal = total(vessel.gases);
  if (vessel.gas_rate_permille >= BUBBLE_VISIBLE_RATE_PERMILLE && gasTotal > 0) {
    const gasSpecies = Object.keys(vessel.gases).sort()[0];
    bubbles = {
      rate_permille: Math.min(1000, vessel.gas_rate_permille),
      size_permille: Math.min(1000, 200 + idiv(gasTotal * 100, 1000)),
      gas_label: gasSpecies,
    };
  }
  let steam = 0;
  if (vessel.temperature_milli_c >= STEAM_VISIBLE_MILLI_C && vessel.volume_uL > 0) {
    steam = Math.min(1000, idiv(vessel.temperature_milli_c - STEAM_VISIBLE_MILLI_C, 40));
  }
  return {
    fill_ratio_permille: Math.min(1000, idiv(vessel.volume_uL * 1000, Math.max(1, vessel.capacity_uL))),
    liquid_color_token: token,
    liquid_label: token,
    opacity_permille: opacity,
    turbidity_permille: turbidity(vessel, speciesDefs),
    precipitate: precipitateLayer(vessel, speciesDefs),
    bubbles,
    steam_permille: steam,
    temperature_band: temperatureBand(vessel.temperature_milli_c),
  };
}

export function renderFrame(state: AnyDict, speciesDefs: AnyDict): AnyDict {
  const vessels: AnyDict = {};
  for (const vesselId of Object.keys(state.vessels).sort()) {
    vessels[vesselId] = vesselFrame(state, state.vessels[vesselId], speciesDefs);
  }
  const instruments: AnyDict = {};
  for (const equipmentId of Object.keys(state.equipment).sort()) {
    const reading = state.equipment[equipmentId].reading;
    instruments[equipmentId] = reading
      ? { reading: reading.display ?? "", unit: reading.unit ?? "", status: reading.status ?? "idle" }
      : { reading: "", unit: "", status: "idle" };
  }
  const highlights: AnyDict[] = [];
  for (const event of [...(state._recent_events ?? [])].reverse() as AnyDict[]) {
    if (
      ["precipitate_formed", "gas_released", "safety_warning", "safety_locked", "crystal_formed"].includes(event.kind)
      && event.vessel_id
    ) {
      highlights.push({ vessel_id: event.vessel_id, kind: event.kind });
    }
  }
  return { vessels, instruments, highlights: highlights.slice(0, 6) };
}

// ---------------------------------------------------------------------------
// Species ledger (物质层) and "no visible change" (可解释的无现象)
// ---------------------------------------------------------------------------

export function speciesLedger(state: AnyDict, vesselId: string, speciesDefs: AnyDict): AnyDict {
  const vessel = state.vessels[vesselId];
  const rows: AnyDict[] = [];
  const speciesIds = new Set<string>([
    ...Object.keys(vessel.contents),
    ...Object.keys(vessel.solids),
    ...Object.keys(vessel.gases),
  ]);
  for (const speciesId of [...speciesIds].sort()) {
    const species = speciesDefs[speciesId] ?? {};
    rows.push({
      species_id: speciesId,
      dissolved_umol: vessel.contents[speciesId] ?? 0,
      solid_umol: vessel.solids[speciesId] ?? 0,
      gas_umol: vessel.gases[speciesId] ?? 0,
      spectator: Boolean(species.spectator),
    });
  }
  return {
    vessel_id: vesselId,
    volume_uL: vessel.volume_uL,
    ph_milli: vessel.ph_milli,
    temperature_milli_c: vessel.temperature_milli_c,
    mix_permille: vessel.mix_permille,
    rows,
  };
}

/** Ordered, checkable reasons a visible change may be absent. */
export function explainNoChange(
  state: AnyDict,
  vesselId: string,
  speciesDefs: AnyDict,
  rules: AnyDict[],
): string[] {
  const vessel = state.vessels[vesselId];
  const reasons: string[] = [];
  const hasIndicator = Object.keys(vessel.contents).some(
    (s) => (speciesDefs[s] ?? {}).role === "indicator",
  );
  if (!hasIndicator && Object.keys(vessel.contents).some(
    (s) => ["acid", "base"].includes((speciesDefs[s] ?? {}).role),
  )) {
    reasons.push("no_indicator");
  }
  const volume = vessel.volume_uL;
  if (volume > 0) {
    let below = true;
    for (const speciesId of Object.keys(vessel.contents)) {
      const species = speciesDefs[speciesId] ?? {};
      const threshold = species.visible_conc_umol_per_ul_milli || VISIBLE_CONC_DEFAULT_UMOL_PER_UL_MILLI;
      if ((species.color_token ?? "clear") !== "clear"
        && idiv(vessel.contents[speciesId] * 1000, volume) >= Math.max(1, threshold)) {
        below = false;
        break;
      }
    }
    if (below && Object.keys(vessel.contents).some(
      (s) => ((speciesDefs[s] ?? {}).color_token ?? "clear") !== "clear",
    )) {
      reasons.push("below_visible_threshold");
    }
  }
  if (vessel.mix_permille < 300) reasons.push("not_mixed");
  let reacted = false;
  for (const rule of rules) {
    for (const condition of rule.when as AnyDict[]) {
      if (condition.op === "species_at_least") {
        const species = condition.species;
        if ((vessel.contents[species] ?? 0) + (vessel.solids[species] ?? 0) < condition.amount_umol) {
          reacted = true;
          break;
        }
      }
    }
    if (reacted) break;
  }
  if (reacted) reasons.push("reactant_exhausted");
  if (total(state.environment.escaped_gas_umol) > 0 && Object.keys(vessel.gases).length === 0) {
    reasons.push("gas_escaped");
  }
  const knownSpecies = new Set<string>();
  for (const rule of rules) {
    for (const effect of rule.then as AnyDict[]) knownSpecies.add(effect.species ?? "");
  }
  if (!Object.keys(vessel.contents).some((s) => knownSpecies.has(s))) {
    reasons.push("not_modeled");
  }
  return reasons;
}

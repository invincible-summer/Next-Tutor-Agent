/**
 * Discrete-time phase dynamics — mirror of phase.py: heat, mixing, settling,
 * gas, solubility. All processes advance in fixed DT steps with integer math
 * only. Safety is evaluated per step so a warming vessel crosses the warning
 * band before the lock band.
 */
import {
  addAmount,
  coolingDropMilliC,
  DT_MS,
  idiv,
  interpolateTable,
  makeEvent,
  temperatureDeltaMilliC,
  type AnyDict,
} from "./units.ts";
import { orderedRules, runRules } from "./reaction.ts";
import { evaluateSafety } from "./safety.ts";

function heatStep(state: AnyDict, vessel: AnyDict): void {
  const heat = vessel.heat;
  if (heat && heat.power_permille > 0 && state.sim_time_ms < heat.until_ms) {
    const microJ = idiv(heat.rated_micro_j_per_step * heat.power_permille, 1000);
    const delta = temperatureDeltaMilliC(vessel, microJ);
    if (delta > 0) vessel.temperature_milli_c += delta;
  }
  const drop = coolingDropMilliC(vessel, state.environment.room_temperature_milli_c);
  if (drop > 0) vessel.temperature_milli_c -= drop;
}

function mixStep(state: AnyDict, vessel: AnyDict): void {
  const stir = vessel.stir;
  if (stir && stir.speed_permille > 0 && state.sim_time_ms < stir.until_ms) {
    const gain = idiv((1000 - vessel.mix_permille) * (200 + idiv(stir.speed_permille * 400, 1000)), 1000);
    vessel.mix_permille = Math.min(1000, vessel.mix_permille + gain);
  } else {
    vessel.mix_permille -= idiv(vessel.mix_permille * 20, 1000);
  }
}

function settleStep(vessel: AnyDict): void {
  if (Object.keys(vessel.solids).length === 0) {
    vessel.settled_permille = 0;
    return;
  }
  if (vessel.mix_permille >= 500) {
    vessel.settled_permille = Math.max(0, vessel.settled_permille - 60);
  } else {
    vessel.settled_permille = Math.min(1000, vessel.settled_permille + 30);
  }
}

function gasOutlet(state: AnyDict, vesselId: string): string | null {
  for (const equipment of Object.values(state.equipment) as AnyDict[]) {
    const connected = equipment.connected;
    if (connected && connected.source === vesselId) return connected.target ?? null;
  }
  return null;
}

function gasStep(
  state: AnyDict,
  vessel: AnyDict,
  events: AnyDict[],
  commandId: string,
): void {
  const gases = vessel.gases;
  if (Object.keys(gases).length === 0) {
    vessel.gas_rate_permille = Math.max(0, vessel.gas_rate_permille - 80);
    return;
  }
  vessel.gas_rate_permille = Math.max(200, vessel.gas_rate_permille - 40);
  if (vessel.collects_gas) {
    // Inverted collection cylinder: gas accumulates instead of venting.
    return;
  }
  let outlet = gasOutlet(state, vessel.id);
  for (const species of Object.keys(gases).sort()) {
    const amount = gases[species] ?? 0;
    if (amount <= 0) continue;
    const moved = amount > 1 ? Math.max(1, idiv(amount * 250, 1000)) : amount;
    if (outlet !== null && !state.vessels[outlet]) outlet = null;
    if (outlet !== null) {
      addAmount(gases, species, -moved);
      addAmount(state.vessels[outlet].gases, species, moved);
      events.push(makeEvent(state, "gas_released", commandId, {
        vesselId: vessel.id, ruleId: "",
        data: { species, amount_umol: moved, destination: outlet },
      }));
    } else {
      addAmount(gases, species, -moved);
      addAmount(state.environment.escaped_gas_umol, species, moved);
      events.push(makeEvent(state, "gas_released", commandId, {
        vesselId: vessel.id, ruleId: "",
        data: { species, amount_umol: moved, destination: "atmosphere" },
      }));
    }
  }
}

/** Solubility-table equilibrium with finite rate, both directions. */
function dissolveStep(
  state: AnyDict,
  vessel: AnyDict,
  events: AnyDict[],
  commandId: string,
  speciesDefs: AnyDict,
): void {
  const volume = vessel.volume_uL;
  if (volume <= 0) return;
  const speciesIds = new Set<string>([...Object.keys(vessel.solids), ...Object.keys(vessel.contents)]);
  for (const speciesId of [...speciesIds].sort()) {
    const species = speciesDefs[speciesId];
    if (!species || !species.solubility_table || species.solubility_table.length === 0) continue;
    const limitUmolPerUlMilli = interpolateTable(
      (species.solubility_table as AnyDict[]).map(
        (point) => [point.temperature_milli_c, point.solubility_umol_per_ul_milli],
      ),
      vessel.temperature_milli_c,
    );
    const capacityUmol = idiv(limitUmolPerUlMilli * volume, 1000);
    const dissolved = vessel.contents[speciesId] ?? 0;
    const rate = 60 + idiv(vessel.mix_permille * 240, 1000); // permille of deficit per step
    if (dissolved < capacityUmol && (vessel.solids[speciesId] ?? 0) > 0) {
      const deficit = capacityUmol - dissolved;
      const moved = Math.min(vessel.solids[speciesId], Math.max(1, idiv(deficit * rate, 1000)));
      addAmount(vessel.solids, speciesId, -moved);
      addAmount(vessel.contents, speciesId, moved);
      events.push(makeEvent(state, "phase_changed", commandId, {
        vesselId: vessel.id, ruleId: "",
        data: { species: speciesId, amount_umol: moved, direction: "dissolve" },
      }));
    } else if (dissolved > capacityUmol) {
      const excess = dissolved - capacityUmol;
      const moved = Math.max(1, idiv(excess * rate, 1000));
      addAmount(vessel.contents, speciesId, -moved);
      addAmount(vessel.solids, speciesId, moved);
      events.push(makeEvent(state, "crystal_formed", commandId, {
        vesselId: vessel.id, ruleId: "",
        data: { species: speciesId, amount_umol: moved },
      }));
    }
  }
}

/**
 * Advance every vessel by `durationMs` in DT steps, re-firing rules after
 * each step so rate-capped reactions keep pace with time.
 */
export function advance(
  state: AnyDict,
  durationMs: number,
  events: AnyDict[],
  commandId: string,
  speciesDefs: AnyDict,
  rules: AnyDict[],
  trigger: string,
  pack: AnyDict,
): void {
  const steps = idiv(durationMs, DT_MS);
  if (steps <= 0) return;
  const ordered = orderedRules(rules);
  for (let step = 0; step < steps; step += 1) {
    state.sim_time_ms += DT_MS;
    for (const vesselId of Object.keys(state.vessels).sort()) {
      heatStep(state, state.vessels[vesselId]);
      mixStep(state, state.vessels[vesselId]);
    }
    for (const vesselId of Object.keys(state.vessels).sort()) {
      const vessel = state.vessels[vesselId];
      settleStep(vessel);
      gasStep(state, vessel, events, commandId);
      dissolveStep(state, vessel, events, commandId, speciesDefs);
    }
    for (const vesselId of Object.keys(state.vessels).sort()) {
      runRules(state, ordered, vesselId, trigger, events, commandId);
    }
    evaluateSafety(state, pack, events, commandId);
  }
}

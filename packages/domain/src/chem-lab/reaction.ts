/**
 * Rule matching and stoichiometry — mirror of reaction.py.
 * All arithmetic is integer with floor semantics. Rules fire in a fixed
 * order (priority desc, rule id asc); each firing of `consume_min_ratio`
 * consumes the limiting reagent times the declared ratio, optionally capped
 * per firing.
 */
import {
  addAmount,
  idiv,
  makeEvent,
  temperatureDeltaMilliC,
  MAX_RULE_FIRINGS_PER_COMMAND,
  type AnyDict,
} from "./units.ts";

export function orderedRules(rules: AnyDict[]): AnyDict[] {
  return [...rules].sort((a, b) => (b.priority - a.priority) || (a.id < b.id ? -1 : 1));
}

function vesselOf(state: AnyDict, _scope: string, targetId: string): AnyDict {
  // v1 rules only address the command's target vessel.
  return state.vessels[targetId];
}

function conditionHolds(state: AnyDict, condition: AnyDict, targetId: string): boolean {
  const vessel = vesselOf(state, condition.vessel ?? "$target", targetId);
  const op = condition.op;
  if (op === "species_at_least") {
    const species = condition.species;
    const pool = (vessel.contents[species] ?? 0) + (vessel.solids[species] ?? 0);
    return pool >= condition.amount_umol;
  }
  if (op === "volume_between") {
    return condition.min_value < vessel.volume_uL && vessel.volume_uL < condition.max_value;
  }
  if (op === "temperature_between") {
    return condition.min_value < vessel.temperature_milli_c
      && vessel.temperature_milli_c < condition.max_value;
  }
  if (op === "ph_between") {
    const ph = vessel.ph_milli;
    return ph !== null && condition.min_value < ph && ph < condition.max_value;
  }
  if (op === "mix_at_least") {
    return vessel.mix_permille >= condition.min_value;
  }
  if (op === "vessel_connected") {
    for (const equipment of Object.values(state.equipment) as AnyDict[]) {
      const connected = equipment.connected;
      if (connected && (connected.source === vessel.id || connected.target === vessel.id)) {
        return true;
      }
    }
    return false;
  }
  if (op === "equipment_state") {
    const equipment = state.equipment[condition.target];
    if (!equipment) return false;
    const expected = condition.expected;
    if (expected === "loaded") {
      return equipment.load.volume_uL > 0 || Object.keys(equipment.load.contents).length > 0;
    }
    if (expected === "clean") return Boolean(equipment.load.clean);
    if (expected === "connected") return equipment.connected != null;
    const reading = equipment.reading ?? {};
    return (reading.status ?? "idle") === expected;
  }
  return false;
}

export function conditionsHold(state: AnyDict, rule: AnyDict, targetId: string): boolean {
  return (rule.when as AnyDict[]).every((condition) => conditionHolds(state, condition, targetId));
}

/**
 * Consume species in stoichiometric ratio; returns the base extent (umol).
 * Pools look at dissolved contents first, then solids.
 */
function consumeMinRatio(vessel: AnyDict, pairs: [string, number][], capUmol: number): number {
  let extent: number | null = null;
  for (const [species, ratio] of pairs) {
    const pool = (vessel.contents[species] ?? 0) + (vessel.solids[species] ?? 0);
    const available = idiv(pool, ratio);
    extent = extent === null ? available : Math.min(extent, available);
  }
  if (extent === null) return 0;
  if (capUmol > 0) extent = Math.min(extent, capUmol);
  if (extent <= 0) return 0;
  for (const [species, ratio] of pairs) {
    let remaining = extent * ratio;
    const fromContents = Math.min(vessel.contents[species] ?? 0, remaining);
    if (fromContents) {
      addAmount(vessel.contents, species, -fromContents);
      remaining -= fromContents;
    }
    if (remaining) addAmount(vessel.solids, species, -remaining);
  }
  return extent;
}

/**
 * Apply one firing of `rule` on the target vessel. Returns false when the
 * rule could not fire (conditions unmet or nothing to consume).
 */
export function fireRule(
  state: AnyDict,
  rule: AnyDict,
  targetId: string,
  events: AnyDict[],
  commandId: string,
): boolean {
  if (!conditionsHold(state, rule, targetId)) return false;
  const vessel = state.vessels[targetId];
  let extent = 0;
  const consumedMap: Record<string, number> = {};
  for (const effect of rule.then as AnyDict[]) {
    if (effect.op === "consume_min_ratio") {
      const pairs = (effect.pairs as [string, number][]).map((row) => [row[0], Number(row[1])] as [string, number]);
      extent = consumeMinRatio(vessel, pairs, Number(effect.cap_umol ?? 0));
      if (extent <= 0) return false;
      for (const [species, ratio] of pairs) consumedMap[species] = extent * ratio;
    }
  }
  let firedChemistry = false;
  for (const effect of rule.then as AnyDict[]) {
    const op = effect.op;
    if (op === "consume_min_ratio") {
      firedChemistry = true;
      for (const species of Object.keys(consumedMap).sort()) {
        events.push(makeEvent(state, "species_consumed", commandId, {
          vesselId: targetId, ruleId: rule.id,
          data: { species, amount_umol: consumedMap[species] },
        }));
      }
    } else if (op === "produce") {
      const species = effect.species;
      const amount = effect.from_consumed
        ? idiv(extent * Number(effect.ratio_to_consumed ?? 1000), 1000)
        : Number(effect.amount_umol ?? 0);
      if (amount > 0) {
        addAmount(vessel.contents, species, amount);
        firedChemistry = true;
        events.push(makeEvent(state, "species_produced", commandId, {
          vesselId: targetId, ruleId: rule.id,
          data: { species, amount_umol: amount },
        }));
      }
    } else if (op === "transfer_to_solid") {
      const species = effect.species;
      const amount = idiv(extent * Number(effect.ratio_to_consumed ?? 1000), 1000);
      if (amount > 0) {
        addAmount(vessel.contents, species, -Math.min(amount, vessel.contents[species] ?? 0));
        addAmount(vessel.solids, species, amount);
        vessel.settled_permille = Math.min(vessel.settled_permille, 200);
        firedChemistry = true;
        events.push(makeEvent(state, "precipitate_formed", commandId, {
          vesselId: targetId, ruleId: rule.id,
          data: { species, amount_umol: amount },
        }));
      }
    } else if (op === "transfer_to_gas") {
      const species = effect.species;
      let amount = idiv(extent * Number(effect.ratio_to_consumed ?? 1000), 1000);
      if (amount <= 0 && effect.amount_umol) {
        amount = Math.min(Number(effect.amount_umol), vessel.contents[species] ?? 0);
      }
      if (amount > 0) {
        addAmount(vessel.contents, species, -Math.min(amount, vessel.contents[species] ?? 0));
        addAmount(vessel.gases, species, amount);
        vessel.gas_rate_permille = Math.min(1000, vessel.gas_rate_permille + 400);
        firedChemistry = true;
        events.push(makeEvent(state, "gas_released", commandId, {
          vesselId: targetId, ruleId: rule.id,
          data: { species, amount_umol: amount },
        }));
      }
    } else if (op === "emit_temperature") {
      const microJ = extent * Number(effect.micro_j_per_umol);
      const delta = temperatureDeltaMilliC(vessel, microJ);
      if (delta > 0) {
        vessel.temperature_milli_c += delta;
        events.push(makeEvent(state, "temperature_changed", commandId, {
          vesselId: targetId, ruleId: rule.id,
          data: { delta_milli_c: delta, cause: "reaction" },
        }));
      }
    } else if (op === "mark_observation") {
      events.push(makeEvent(state, "observation_emitted", commandId, {
        vesselId: targetId, ruleId: rule.id,
        data: { key: effect.key },
      }));
    } else if (op === "mark_step") {
      events.push(makeEvent(state, "procedure_step_completed", commandId, {
        vesselId: targetId, ruleId: rule.id,
        data: { step_id: effect.key },
      }));
    }
  }
  return firedChemistry;
}

/** Fixed-order rule evaluation with per-rule and global firing caps. */
export function runRules(
  state: AnyDict,
  rules: AnyDict[],
  targetId: string,
  trigger: string,
  events: AnyDict[],
  commandId: string,
): void {
  let budget = MAX_RULE_FIRINGS_PER_COMMAND;
  for (const rule of rules) {
    if (budget <= 0) {
      events.push(makeEvent(state, "rule_limit_reached", commandId, {
        vesselId: targetId, ruleId: "", data: { scope: "command" },
      }));
      return;
    }
    if (!(rule.triggers as string[]).includes(trigger)) continue;
    const limit = Number(rule.limits.max_firings_per_evaluation);
    let firings = 0;
    while (firings < limit && budget > 0) {
      if (!fireRule(state, rule, targetId, events, commandId)) break;
      firings += 1;
      budget -= 1;
    }
    const limitNoted = events.some(
      (event) => event.kind === "rule_limit_reached" && event.rule_id === rule.id,
    );
    if (firings >= limit && !limitNoted) {
      // At most one rule-scope limit notice per rule per command.
      events.push(makeEvent(state, "rule_limit_reached", commandId, {
        vesselId: targetId, ruleId: rule.id, data: { scope: "rule" },
      }));
    }
    if (firings) {
      state.rule_firings[rule.id] = (state.rule_firings[rule.id] ?? 0) + firings;
    }
  }
}

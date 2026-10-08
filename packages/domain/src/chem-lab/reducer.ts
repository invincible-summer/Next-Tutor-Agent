/**
 * Command reducer: LabCommand → (new state, events), fully deterministic.
 * Line-by-line mirror of services/api/app/chem_lab/engine/reducer.py.
 *
 * Evaluation order: normalize → validate → transfer → rules by
 * priority → phase stepping → projection → procedure/goals → state hash.
 * Rejections append a `command_rejected` event (seq still advances) and
 * never raise through the session.
 */
import {
  ACTION_MS,
  MAX_CHECKPOINTS_PER_SESSION,
  addAmount,
  makeEvent,
  stateHash,
  transferAmounts,
  applyTransfer,
  type AnyDict,
} from "./units.ts";
import { advance } from "./phase.ts";
import { computePhMilli, vesselFrame } from "./projection.ts";
import { orderedRules, runRules } from "./reaction.ts";
import { evaluateSafety, heatCompatible } from "./safety.ts";

export const COMMAND_KINDS = new Set([
  "pick_up", "place", "aspirate", "dispense", "pour", "heat", "stir",
  "wait", "measure", "connect", "filter", "wash", "dispose", "checkpoint",
  "move", "release",
]);
const TRANSFER_TRIGGERS: Record<string, string> = {
  aspirate: "on_aspirate",
  dispense: "on_dispense",
  pour: "on_pour",
};
const MEASURE_QUANTITIES = new Set(["temperature", "ph", "volume", "mass"]);
const POUR_RATES = new Set(["slow", "normal"]);

class _Reject extends Error {
  reason: string;
  constructor(reason: string) {
    super(reason);
    this.reason = reason;
  }
}

// ---------------------------------------------------------------------------
// Validation helpers
// ---------------------------------------------------------------------------

function _vessel(state: AnyDict, vesselId: string): AnyDict {
  const vessel = state.vessels[vesselId];
  if (vessel === undefined || vessel === null) throw new _Reject("unknown_object");
  return vessel;
}

function _equipment(state: AnyDict, equipmentId: string): AnyDict {
  const equipment = state.equipment[equipmentId];
  if (equipment === undefined || equipment === null) throw new _Reject("unknown_object");
  return equipment;
}

function _positive(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0) {
    throw new _Reject("invalid_params");
  }
  return value;
}

function _permille(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || !(value > 0 && value <= 1000)) {
    throw new _Reject("invalid_params");
  }
  return value;
}

function _freeSlot(state: AnyDict, slotId: string): void {
  if (!(state.slots as AnyDict[]).some((slot) => slot.id === slotId)) {
    throw new _Reject("unknown_object");
  }
  for (const vessel of Object.values(state.vessels) as AnyDict[]) {
    if (vessel.slot === slotId) throw new _Reject("invalid_params");
  }
  for (const equipment of Object.values(state.equipment) as AnyDict[]) {
    if (equipment.slot === slotId) throw new _Reject("invalid_params");
  }
}

// ---------------------------------------------------------------------------
// Command handlers (each returns the sim duration to advance afterwards)
// ---------------------------------------------------------------------------

function _doPickUp(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  const objectId = String(command.object_id ?? "");
  if (state.held !== null) throw new _Reject("invalid_params");
  if (objectId in state.vessels || objectId in state.equipment) {
    state.held = objectId;
  } else {
    throw new _Reject("unknown_object");
  }
  events.push(makeEvent(state, "object_picked_up", commandId, { data: { object_id: objectId } }));
  return 0;
}

function _doPlace(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  const objectId = String(command.object_id ?? "");
  const slotId = String(command.slot_id ?? "");
  if (state.held !== objectId) throw new _Reject("invalid_params");
  _freeSlot(state, slotId);
  if (objectId in state.vessels) {
    state.vessels[objectId].slot = slotId;
  } else {
    state.equipment[objectId].slot = slotId;
  }
  state.held = null;
  events.push(makeEvent(state, "object_placed", commandId, {
    data: { object_id: objectId, slot_id: slotId },
  }));
  return 0;
}

function _doMove(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  // Atomic position command: one object to one slot in a single step.
  // Unlike the legacy pick_up/place pair, `move` needs no prior hold; it
  // validates target occupancy (only the moved object itself may sit on the
  // target slot) and clears `held` when it matches the moved object.
  const objectId = String(command.object_id ?? "");
  const slotId = String(command.slot_id ?? "");
  if (!(objectId in state.vessels) && !(objectId in state.equipment)) {
    throw new _Reject("unknown_object");
  }
  if (!(state.slots as AnyDict[]).some((slot) => slot.id === slotId)) {
    throw new _Reject("unknown_object");
  }
  if (state.held !== null && state.held !== objectId) throw new _Reject("invalid_params");
  for (const vessel of Object.values(state.vessels) as AnyDict[]) {
    if (vessel.id !== objectId && vessel.slot === slotId) throw new _Reject("invalid_params");
  }
  for (const equipment of Object.values(state.equipment) as AnyDict[]) {
    if (equipment.id !== objectId && equipment.slot === slotId) throw new _Reject("invalid_params");
  }
  let fromSlot: string;
  if (objectId in state.vessels) {
    fromSlot = state.vessels[objectId].slot;
    state.vessels[objectId].slot = slotId;
  } else {
    fromSlot = state.equipment[objectId].slot;
    state.equipment[objectId].slot = slotId;
  }
  state.held = null;
  events.push(makeEvent(state, "object_moved", commandId, {
    data: { object_id: objectId, from_slot_id: fromSlot, to_slot_id: slotId },
  }));
  return 0;
}

function _doRelease(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  // Drop the current hold: clears `held` without touching slot or contents —
  // the escape hatch for legacy pick_up sessions.
  const objectId = String(command.object_id ?? "");
  if (state.held !== objectId) throw new _Reject("invalid_params");
  const slotId = objectId in state.vessels
    ? state.vessels[objectId].slot
    : state.equipment[objectId].slot;
  state.held = null;
  events.push(makeEvent(state, "object_released", commandId, {
    data: { object_id: objectId, slot_id: slotId },
  }));
  return 0;
}

function _doAspirate(
  state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string, pack: AnyDict,
): number {
  const source = _vessel(state, String(command.source_id ?? ""));
  const instrument = _equipment(state, String(command.instrument_id ?? ""));
  const amount = _positive(command.amount_uL);
  const load = instrument.load;
  if (source.volume_uL < amount) throw new _Reject("invalid_params");
  const capacity = Number(pack._equipment_defs[instrument.kind]?.capacity_uL ?? 0);
  if (capacity > 0 && load.volume_uL + amount > capacity) throw new _Reject("capacity_exceeded");
  if (load.volume_uL > 0 && !load.clean && load.source !== source.id) {
    throw new _Reject("not_clean");
  }
  const moved = transferAmounts(source.contents, amount, source.volume_uL);
  source.volume_uL -= amount;
  applyTransfer(source.contents, load.contents, moved);
  load.volume_uL += amount;
  load.source = source.id;
  load.clean = false;
  events.push(makeEvent(state, "reagent_aspirated", commandId, {
    vesselId: source.id,
    data: { instrument_id: instrument.id, amount_uL: amount },
  }));
  return ACTION_MS;
}

function _doDispense(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  const instrument = _equipment(state, String(command.instrument_id ?? ""));
  const target = _vessel(state, String(command.target_id ?? ""));
  let amount = _positive(command.amount_uL);
  const load = instrument.load;
  if (load.volume_uL <= 0) throw new _Reject("instrument_empty");
  amount = Math.min(amount, load.volume_uL);
  if (target.volume_uL + amount > target.capacity_uL) throw new _Reject("capacity_exceeded");
  const moved = transferAmounts(load.contents, amount, load.volume_uL);
  load.volume_uL -= amount;
  applyTransfer(load.contents, target.contents, moved);
  target.volume_uL += amount;
  if (load.volume_uL === 0) {
    load.source = null;
    load.clean = true;
  }
  _markContamination(state, target, instrument, events, commandId);
  events.push(makeEvent(state, "reagent_dispensed", commandId, {
    vesselId: target.id,
    data: { instrument_id: instrument.id, amount_uL: amount },
  }));
  events.push(makeEvent(state, "volume_changed", commandId, {
    vesselId: target.id,
    data: { volume_uL: target.volume_uL },
  }));
  return ACTION_MS;
}

function _doPour(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  const source = _vessel(state, String(command.source_id ?? ""));
  const target = _vessel(state, String(command.target_id ?? ""));
  const amount = _positive(command.amount_uL);
  if (!POUR_RATES.has(command.rate ?? "normal")) throw new _Reject("invalid_params");
  if (source.id === target.id) throw new _Reject("invalid_params");
  if (source.volume_uL < amount) throw new _Reject("invalid_params");
  if (target.volume_uL + amount > target.capacity_uL) throw new _Reject("capacity_exceeded");
  const moved = transferAmounts(source.contents, amount, source.volume_uL);
  source.volume_uL -= amount;
  applyTransfer(source.contents, target.contents, moved);
  target.volume_uL += amount;
  target.mix_permille = Math.min(1000, target.mix_permille + 120);
  events.push(makeEvent(state, "material_poured", commandId, {
    vesselId: target.id,
    data: { source_id: source.id, amount_uL: amount, rate: command.rate ?? "normal" },
  }));
  events.push(makeEvent(state, "volume_changed", commandId, {
    vesselId: target.id,
    data: { volume_uL: target.volume_uL },
  }));
  return ACTION_MS;
}

function _doHeat(
  state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string, pack: AnyDict,
): number {
  const device = _equipment(state, String(command.device_id ?? ""));
  const vessel = _vessel(state, String(command.vessel_id ?? ""));
  const power = _permille(command.power_permille);
  const duration = _positive(command.duration_ms);
  if (!heatCompatible(state, device.id, vessel.id, pack._equipment_defs)) {
    throw new _Reject("incompatible_device");
  }
  const rated = Number(pack._equipment_defs[device.kind]?.rated_micro_j_per_step ?? 0);
  vessel.heat = {
    device_id: device.id,
    power_permille: power,
    rated_micro_j_per_step: rated,
    until_ms: state.sim_time_ms + duration,
  };
  events.push(makeEvent(state, "heating_started", commandId, {
    vesselId: vessel.id,
    data: { device_id: device.id, power_permille: power, duration_ms: duration },
  }));
  return duration;
}

function _doStir(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  const vessel = _vessel(state, String(command.vessel_id ?? ""));
  const speed = _permille(command.speed_permille);
  const duration = _positive(command.duration_ms);
  vessel.stir = { speed_permille: speed, until_ms: state.sim_time_ms + duration };
  events.push(makeEvent(state, "mixing_changed", commandId, {
    vesselId: vessel.id,
    data: { speed_permille: speed, duration_ms: duration },
  }));
  return duration;
}

function _doWait(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  return _positive(command.duration_ms);
}

function _doMeasure(
  state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string, pack: AnyDict,
): number {
  const instrumentId = String(command.instrument_id ?? "");
  const vessel = _vessel(state, String(command.vessel_id ?? ""));
  const quantity = String(command.quantity ?? "");
  if (!MEASURE_QUANTITIES.has(quantity)) throw new _Reject("invalid_params");
  // Instruments live in equipment; graduated vessels (e.g. the cylinder)
  // may measure themselves.
  let instrument: AnyDict;
  if (instrumentId in state.equipment) {
    instrument = state.equipment[instrumentId];
  } else if (instrumentId in state.vessels) {
    instrument = state.vessels[instrumentId];
  } else {
    throw new _Reject("unknown_object");
  }
  const equipmentDef = pack._equipment_defs[instrument.kind] ?? {};
  if (!((equipmentDef.measures ?? []) as string[]).includes(quantity)) {
    throw new _Reject("incompatible_device");
  }
  const reading: AnyDict = { status: "done", quantity, unit: "", display: "" };
  if (quantity === "temperature") {
    const milliC = vessel.temperature_milli_c;
    reading.unit = "°C";
    reading.value_milli = milliC;
    reading.display = _formatMilli(milliC, 1);
  } else if (quantity === "ph") {
    if (vessel.ph_milli === null || vessel.ph_milli === undefined) throw new _Reject("not_modeled");
    reading.unit = "pH";
    reading.value_milli = vessel.ph_milli;
    reading.display = _formatMilli(vessel.ph_milli, 2);
  } else if (quantity === "volume") {
    reading.unit = "mL";
    reading.value_milli = vessel.volume_uL;
    reading.display = _formatMilli(vessel.volume_uL, 1);
  } else {
    // mass — no mass model in v1 packs
    throw new _Reject("not_modeled");
  }
  instrument.reading = reading;
  events.push(makeEvent(state, "measurement_taken", commandId, {
    vesselId: vessel.id,
    data: {
      instrument_id: instrument.id,
      quantity,
      display: reading.display,
      unit: reading.unit,
    },
  }));
  // A deliberate reading is a macro-layer observation fact, so procedure
  // steps like "量取并读数" can complete.
  _recordObservation(state, pack, events, commandId, vessel.id, `measured_${quantity}`);
  return ACTION_MS;
}

function _doConnect(
  state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string, pack: AnyDict,
): number {
  // Attach tubing `instrument_id` from a gas-producing `vessel_id` to a
  // collection `target_id` vessel. Gas phase dynamics read this link.
  const vessel = _vessel(state, String(command.vessel_id ?? ""));
  const instrument = _equipment(state, String(command.instrument_id ?? ""));
  const target = _vessel(state, String(command.target_id ?? ""));
  if (vessel.id === target.id) throw new _Reject("invalid_params");
  instrument.connected = { source: vessel.id, target: target.id };
  events.push(makeEvent(state, "instrument_connected", commandId, {
    vesselId: vessel.id,
    data: { instrument_id: instrument.id, target_id: target.id },
  }));
  _recordObservation(state, pack, events, commandId, vessel.id, "tubing_connected");
  return 0;
}

function _doFilter(
  state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string, pack: AnyDict,
): number {
  const vessel = _vessel(state, String(command.vessel_id ?? ""));
  const apparatus = _vessel(state, String(command.apparatus_id ?? ""));
  if (vessel.id === apparatus.id) throw new _Reject("invalid_params");
  // Liquid (and dissolved species) passes; solids stay behind on the filter.
  const movedVolume = vessel.volume_uL;
  if (apparatus.volume_uL + movedVolume > apparatus.capacity_uL) {
    throw new _Reject("capacity_exceeded");
  }
  if (movedVolume > 0) {
    const moved = transferAmounts(vessel.contents, movedVolume, vessel.volume_uL);
    vessel.volume_uL = 0;
    applyTransfer(vessel.contents, apparatus.contents, moved);
    apparatus.volume_uL += movedVolume;
  }
  events.push(makeEvent(state, "phase_changed", commandId, {
    vesselId: apparatus.id,
    data: {
      operation: "filter",
      source_id: vessel.id,
      filtrate_uL: movedVolume,
      residue_umol: { ...vessel.solids },
    },
  }));
  _recordObservation(state, pack, events, commandId, apparatus.id, "filtered");
  return ACTION_MS;
}

function _doWash(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  const instrument = _equipment(state, String(command.instrument_id ?? ""));
  const load = instrument.load;
  const disposed = load.volume_uL;
  state.environment.disposed_volume_uL += disposed;
  instrument.load = { volume_uL: 0, contents: {}, solids: {}, source: null, clean: true };
  events.push(makeEvent(state, "instrument_washed", commandId, {
    data: { instrument_id: instrument.id, disposed_uL: disposed },
  }));
  return ACTION_MS;
}

function _doDispose(state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string): number {
  const vessel = _vessel(state, String(command.vessel_id ?? ""));
  const amount = _positive(command.amount_uL);
  if (vessel.volume_uL < amount) throw new _Reject("invalid_params");
  const moved = transferAmounts(vessel.contents, amount, vessel.volume_uL);
  vessel.volume_uL -= amount;
  for (const species of Object.keys(moved).sort()) {
    addAmount(vessel.contents, species, -moved[species]);
    addAmount(state.environment.escaped_gas_umol, `waste:${species}`, moved[species]);
  }
  state.environment.disposed_volume_uL += amount;
  events.push(makeEvent(state, "volume_changed", commandId, {
    vesselId: vessel.id,
    data: { volume_uL: vessel.volume_uL, disposed_uL: amount },
  }));
  return ACTION_MS;
}

function _doCheckpoint(
  state: AnyDict, command: AnyDict, events: AnyDict[], commandId: string,
): number {
  const label = String(command.label ?? "").trim().slice(0, 80);
  if (!label) throw new _Reject("invalid_params");
  if (state.checkpoints.length >= MAX_CHECKPOINTS_PER_SESSION) throw new _Reject("invalid_params");
  const checkpoint = {
    id: `cp_${state.checkpoints.length + 1}`,
    label,
    revision: state.revision,
    seq: state.seq,
    sim_time_ms: state.sim_time_ms,
  };
  state.checkpoints.push(checkpoint);
  events.push(makeEvent(state, "checkpoint_created", commandId, {
    data: { checkpoint_id: checkpoint.id, label },
  }));
  return 0;
}

function _markContamination(
  state: AnyDict, vessel: AnyDict, instrument: AnyDict, events: AnyDict[], commandId: string,
): void {
  const source = instrument.load.source;
  if (!source || source === vessel.id) return;
  if (!vessel.contamination.includes(source)) {
    vessel.contamination = [...vessel.contamination, source].sort();
    events.push(makeEvent(state, "contamination_changed", commandId, {
      vesselId: vessel.id,
      data: { via: instrument.id, source },
    }));
  }
}

function _formatMilli(value: number, decimals: number): string {
  const sign = value < 0 ? "-" : "";
  const scaled = Math.abs(value);
  if (decimals === 1) {
    return `${sign}${Math.floor(scaled / 1000)}.${Math.floor((scaled % 1000) / 100)}`;
  }
  return `${sign}${Math.floor(scaled / 1000)}.${String(Math.floor((scaled % 1000) / 10)).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Observations, procedure and goals
// ---------------------------------------------------------------------------

function _visibleScan(
  vessel: AnyDict, frame: AnyDict, speciesDefs: AnyDict, before: AnyDict,
): AnyDict {
  // Macro-layer visibility facts for one vessel (shared by priming and by
  // per-command auto-observations).
  const seen: AnyDict = {};
  if (frame.opacity_permille >= 250 && frame.liquid_color_token !== "clear") {
    seen.color_visible = frame.liquid_color_token;
    // Any newly visible token — first appearance or a switch — is a
    // color change (e.g. colorless → pink at the titration endpoint).
    if ((before.color_visible ?? null) !== frame.liquid_color_token) {
      seen.color_changed = frame.liquid_color_token;
    }
  }
  const precipitate = frame.precipitate;
  if (precipitate && precipitate.amount_permille >= 120) {
    seen.precipitate_visible = true;
    if (precipitate.texture === "layer") seen.sediment_layer = true;
  }
  if (frame.bubbles) seen.bubbles_visible = true;
  if (frame.steam_permille >= 250) seen.steam_visible = true;
  if (frame.turbidity_permille >= 300) seen.turbid_visible = true;
  // Indicator at working concentration is a macro fact even when its
  // current pH band renders it colorless (e.g. phenolphthalein in acid).
  if (vessel.volume_uL > 0) {
    for (const speciesId of Object.keys(vessel.contents).sort()) {
      const species = speciesDefs[speciesId];
      if (!species || species.role !== "indicator") continue;
      const concMilli = Math.floor((vessel.contents[speciesId] * 1000) / vessel.volume_uL);
      const threshold = Math.max(1, species.visible_conc_umol_per_ul_milli || 1);
      if (concMilli >= threshold) {
        seen.indicator_present = true;
        break;
      }
    }
  }
  if (Object.keys(vessel.gases).length > 0) seen.gas_collected = true;
  return seen;
}

export function primeVisibility(state: AnyDict, pack: AnyDict): void {
  // Fill `state.visible` with the starting layout's appearances so they are
  // not reported as "new" observations on the first command.
  const speciesDefs = pack._species_defs;
  const phModel = pack.ph_model ?? "none";
  const visible: AnyDict = {};
  for (const vesselId of Object.keys(state.vessels).sort()) {
    const vessel = state.vessels[vesselId];
    vessel.ph_milli = computePhMilli(vessel, phModel);
    const frame = vesselFrame(state, vessel, speciesDefs);
    visible[vesselId] = _visibleScan(vessel, frame, speciesDefs, {});
  }
  state.visible = visible;
}

function _autoObservations(
  state: AnyDict, pack: AnyDict, events: AnyDict[], commandId: string, previousVisible: AnyDict,
): void {
  // Emit observation facts for newly-visible phenomena (macro layer).
  const speciesDefs = pack._species_defs;
  for (const vesselId of Object.keys(state.vessels).sort()) {
    const vessel = state.vessels[vesselId];
    const frame = vesselFrame(state, vessel, speciesDefs);
    const before = previousVisible[vesselId] ?? {};
    const seen = _visibleScan(vessel, frame, speciesDefs, before);
    // Disappearance facts: a previously visible color/bed that is gone.
    for (const [goneKey, wasKey] of [
      ["color_faded", "color_visible"],
      ["precipitate_dissolved", "precipitate_visible"],
    ] as [string, string][]) {
      if (before[wasKey] && !(wasKey in seen)) {
        _recordObservation(state, pack, events, commandId, vesselId, goneKey);
      }
    }
    previousVisible[vesselId] = seen;
    for (const key of Object.keys(seen).sort()) {
      const beforeValue = before[key];
      if (beforeValue === undefined || beforeValue === null || beforeValue === false
        || key === "color_changed") {
        _recordObservation(state, pack, events, commandId, vesselId, key);
      }
    }
  }
}

function _recordObservation(
  state: AnyDict,
  pack: AnyDict,
  events: AnyDict[],
  commandId: string,
  vesselId: string,
  key: string,
  ruleId = "",
): void {
  const event = makeEvent(state, "observation_emitted", commandId, {
    vesselId, ruleId, data: { key },
  });
  events.push(event);
  let conceptIds: string[] = [];
  for (const step of (pack.procedure ?? []) as AnyDict[]) {
    if (((step.expected_observations ?? []) as string[]).includes(key)) {
      conceptIds = [...((step.concept_ids ?? []) as string[])];
      break;
    }
  }
  state.observations.push({
    seq: event.seq,
    key,
    vessel_id: vesselId,
    rule_id: ruleId,
    sim_time_ms: state.sim_time_ms,
    concept_ids: conceptIds,
  });
}

function _ruleObservations(
  state: AnyDict, pack: AnyDict, events: AnyDict[], commandId: string,
): void {
  // Convert rule-emitted observation events into observation facts.
  for (const event of events) {
    if (event.kind === "observation_emitted" && event.data.key) {
      const key = event.data.key;
      const already = (state.observations as AnyDict[]).some(
        (o) => o.key === key && o.seq === event.seq,
      );
      if (!already) {
        state.observations.push({
          seq: event.seq,
          key,
          vessel_id: event.vessel_id,
          rule_id: event.rule_id,
          sim_time_ms: event.sim_time_ms,
          concept_ids: [],
        });
      }
    }
  }
}

function _updateProcedure(
  state: AnyDict, pack: AnyDict, events: AnyDict[], commandId: string,
): void {
  // Sequential step completion: a step's expected observations must be
  // emitted *after* the previous step completed, so measuring the same
  // quantity twice can gate two different steps.
  const completion = state.step_completion_seq;
  let cursor = 0;
  for (const step of (pack.procedure ?? []) as AnyDict[]) {
    if ((state.completed_steps as string[]).includes(step.id)) {
      cursor = completion[step.id] ?? cursor;
      continue;
    }
    const expected = (step.expected_observations ?? []) as string[];
    const marked = events.some(
      (e) => e.kind === "procedure_step_completed" && e.data.step_id === step.id,
    );
    const freshKeys = new Set(
      (state.observations as AnyDict[]).filter((o) => o.seq > cursor).map((o) => o.key),
    );
    if (marked || (expected.length > 0 && expected.every((key) => freshKeys.has(key)))) {
      state.completed_steps.push(step.id);
      completion[step.id] = state.seq;
      cursor = state.seq;
      if (!marked) {
        events.push(makeEvent(state, "procedure_step_completed", commandId, {
          data: { step_id: step.id },
        }));
      }
      continue;
    }
    // Strictly sequential: later steps wait for this one.
    break;
  }
  let step: AnyDict | null = null;
  for (const candidate of (pack.procedure ?? []) as AnyDict[]) {
    if (!(state.completed_steps as string[]).includes(candidate.id)) {
      step = candidate;
      break;
    }
  }
  if (step !== null) {
    state.step_visits[step.id] = (state.step_visits[step.id] ?? 0) + 1;
  } else {
    state.step_visits = {};
  }
}

function _updateGoals(state: AnyDict, pack: AnyDict): void {
  const status = projectionGoals(pack, state);
  state.goals = status.map((g) => ({ id: g.id, status: g.status }));
  if (status.length > 0 && status.every((g) => g.status === "met") && state.phase === "running") {
    state.phase = "completed";
  }
}

export function projectionGoals(pack: AnyDict, state: AnyDict): AnyDict[] {
  const emitted = new Set((state.observations as AnyDict[]).map((o) => o.key));
  const status: AnyDict[] = [];
  for (const goal of (pack.goals ?? []) as AnyDict[]) {
    const met = (goal.requires_observations as string[]).every((key) => emitted.has(key));
    status.push({ id: goal.id, status: met ? "met" : "pending" });
  }
  return status;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export function applyCommand(
  state: AnyDict, pack: AnyDict, command: AnyDict, commandId: string,
): [AnyDict, AnyDict[], boolean, string] {
  const events: AnyDict[] = [];
  const kind = String(command.kind ?? "");
  if (!COMMAND_KINDS.has(kind)) {
    return _rejectCmd(state, events, commandId, "invalid_params", kind);
  }
  if (state.phase === "safety_locked" && kind !== "checkpoint") {
    return _rejectCmd(state, events, commandId, "safety_locked", kind);
  }
  if (["setup", "completed"].includes(state.phase)
    && !["checkpoint", "measure", "wait"].includes(kind)) {
    if (state.phase === "completed"
      && ["pick_up", "place", "measure", "wait", "checkpoint", "move", "release"].includes(kind)) {
      // read-only inspection stays possible after completion
    } else if (state.phase === "setup") {
      return _rejectCmd(state, events, commandId, "phase", kind);
    }
  }

  if (state.phase === "ready") {
    state.phase = "running";
  }

  const previousVisible = state.visible;
  let duration = 0;
  const trigger = TRANSFER_TRIGGERS[kind] ?? `on_${kind}`;
  try {
    if (kind === "pick_up") duration = _doPickUp(state, command, events, commandId);
    else if (kind === "place") duration = _doPlace(state, command, events, commandId);
    else if (kind === "move") duration = _doMove(state, command, events, commandId);
    else if (kind === "release") duration = _doRelease(state, command, events, commandId);
    else if (kind === "aspirate") duration = _doAspirate(state, command, events, commandId, pack);
    else if (kind === "dispense") duration = _doDispense(state, command, events, commandId);
    else if (kind === "pour") duration = _doPour(state, command, events, commandId);
    else if (kind === "heat") duration = _doHeat(state, command, events, commandId, pack);
    else if (kind === "stir") duration = _doStir(state, command, events, commandId);
    else if (kind === "wait") duration = _doWait(state, command, events, commandId);
    else if (kind === "measure") duration = _doMeasure(state, command, events, commandId, pack);
    else if (kind === "connect") duration = _doConnect(state, command, events, commandId, pack);
    else if (kind === "filter") duration = _doFilter(state, command, events, commandId, pack);
    else if (kind === "wash") duration = _doWash(state, command, events, commandId);
    else if (kind === "dispose") duration = _doDispose(state, command, events, commandId);
    else if (kind === "checkpoint") duration = _doCheckpoint(state, command, events, commandId);
  } catch (error) {
    if (error instanceof _Reject) {
      return _rejectCmd(state, events, commandId, error.reason, kind);
    }
    throw error;
  }

  const ordered = orderedRules(pack._rules ?? []);
  const targetId = String(command.target_id || command.vessel_id || command.source_id || "");
  if (targetId && targetId in state.vessels && kind in TRANSFER_TRIGGERS) {
    runRules(state, ordered, targetId, trigger, events, commandId);
  }

  if (duration > 0) {
    advance(
      state, duration, events, commandId,
      pack._species_defs,
      pack._rules ?? [],
      kind === "wait" ? "on_wait" : trigger,
      pack,
    );
  }

  _ruleObservations(state, pack, events, commandId);
  for (const vesselId of Object.keys(state.vessels).sort()) {
    const vessel = state.vessels[vesselId];
    vessel.ph_milli = computePhMilli(vessel, pack.ph_model ?? "none");
  }
  _autoObservations(state, pack, events, commandId, previousVisible);
  evaluateSafety(state, pack, events, commandId);
  _updateProcedure(state, pack, events, commandId);
  _updateGoals(state, pack);

  state.revision += 1;
  state._recent_events = events.slice(-8);
  state.state_hash = stateHash(state);
  return [state, events, true, ""];
}

function _rejectCmd(
  state: AnyDict, events: AnyDict[], commandId: string, reason: string, kind: string,
): [AnyDict, AnyDict[], boolean, string] {
  events.push(makeEvent(state, "command_rejected", commandId, {
    data: { reason, command_kind: kind },
  }));
  state.revision += 1;
  state._recent_events = events.slice(-8);
  state.state_hash = stateHash(state);
  return [state, events, false, reason.startsWith("chem_lab_") ? reason : `chem_lab_${reason}`];
}

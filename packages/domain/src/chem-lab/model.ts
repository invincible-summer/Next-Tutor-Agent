/**
 * State constructors — mirror of model.py's make_vessel/make_equipment/
 * initial_state.
 */
import {
  ROOM_TEMPERATURE_MILLI_C,
  SCHEMA_VERSION,
  sortedSpecies,
  type AnyDict,
} from "./units.ts";

export function makeVessel(entry: AnyDict, defaults: AnyDict): AnyDict {
  return {
    id: entry.id,
    kind: entry.kind,
    slot: entry.slot,
    capacity_uL: entry.capacity_uL,
    volume_uL: entry.volume_uL ?? 0,
    temperature_milli_c: entry.temperature_milli_c ?? defaults.room_temperature_milli_c,
    ph_milli: null,
    mix_permille: 0,
    contents: sortedSpecies({ ...(entry.contents ?? {}) }),
    solids: sortedSpecies({ ...(entry.solids ?? {}) }),
    gases: sortedSpecies({ ...(entry.gases ?? {}) }),
    settled_permille: 0,
    gas_rate_permille: 0,
    collects_gas: Boolean(entry.collects_gas ?? false),
    contamination: [...(entry.contamination ?? [])].sort(),
    heat: null,
    stir: null,
    reading: null,
  };
}

export function makeEquipment(entry: AnyDict): AnyDict {
  return {
    id: entry.id,
    kind: entry.kind,
    slot: entry.slot,
    load: { volume_uL: 0, contents: {}, solids: {}, source: null, clean: true },
    connected: null,
    reading: null,
  };
}

/** Build the session LabState from a validated experiment pack. */
export function initialState(
  pack: AnyDict,
  options: { sessionSeed: number; mode: string; language: string },
): AnyDict {
  const start = pack.starting_state;
  const environment = {
    room_temperature_milli_c: start.room_temperature_milli_c ?? ROOM_TEMPERATURE_MILLI_C,
    escaped_gas_umol: {},
    disposed_volume_uL: 0,
  };
  const vessels: AnyDict = {};
  for (const entry of start.vessels) {
    vessels[entry.id] = makeVessel(entry, environment);
  }
  const equipment: AnyDict = {};
  for (const entry of start.equipment ?? []) {
    equipment[entry.id] = makeEquipment(entry);
  }
  const goals = (pack.goals ?? []).map((goal: AnyDict) => ({ id: goal.id, status: "pending" }));
  return {
    schema_version: SCHEMA_VERSION,
    experiment_id: pack.id,
    pack_version: pack.pack_version,
    pack_hash: pack.pack_hash,
    session_seed: options.sessionSeed,
    mode: options.mode,
    language: options.language,
    phase: "ready",
    revision: 0,
    seq: 0,
    sim_time_ms: 0,
    held: null,
    vessels,
    equipment,
    environment,
    slots: start.slots.map((slot: AnyDict) => ({ ...slot })),
    goals,
    completed_steps: [],
    step_completion_seq: {},
    step_visits: {},
    checkpoints: [],
    observations: [],
    visible: {},
    guidance: null,
    safety: null,
    rule_firings: {},
    state_hash: "",
  };
}

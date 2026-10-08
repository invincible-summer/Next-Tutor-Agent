/**
 * Virtual safety envelope — mirror of safety.py.
 * Safety outcomes are deterministic state results (warning events or a
 * session lock), never uncaught exceptions.
 */
import { makeEvent, type AnyDict } from "./units.ts";

export function checkCapacity(vessel: AnyDict): string | null {
  if (vessel.volume_uL > vessel.capacity_uL) return "overflow";
  return null;
}

export function checkTemperature(vessel: AnyDict, profile: AnyDict): string | null {
  if (vessel.volume_uL <= 0) return null;
  if (vessel.temperature_milli_c >= profile.lock_temperature_milli_c) return "lock_temperature";
  if (vessel.temperature_milli_c >= profile.warn_temperature_milli_c) return "warn_temperature";
  return null;
}

export function heatCompatible(
  state: AnyDict,
  deviceId: string,
  vesselId: string,
  equipmentDefs: AnyDict,
): boolean {
  const device = state.equipment[deviceId];
  const vessel = state.vessels[vesselId];
  if (!device || !vessel) return false;
  const deviceDef = equipmentDefs[device.kind] ?? {};
  const vesselDef = equipmentDefs[vessel.kind] ?? {};
  return Boolean(deviceDef.rated_micro_j_per_step) && Boolean(vesselDef.heat_compatible);
}

/** Scan all vessels after a command; emit warnings or lock the session. */
export function evaluateSafety(
  state: AnyDict,
  pack: AnyDict,
  events: AnyDict[],
  commandId: string,
): void {
  if (state.phase === "safety_locked") return;
  const profile = pack.safety_profile;
  let lockedReason: string | null = null;
  for (const vesselId of Object.keys(state.vessels).sort()) {
    const vessel = state.vessels[vesselId];
    const issue = checkTemperature(vessel, profile);
    if (issue === "lock_temperature") {
      lockedReason = "lock_temperature";
      events.push(makeEvent(state, "safety_locked", commandId, {
        vesselId, ruleId: "",
        data: { reason: issue, temperature_milli_c: vessel.temperature_milli_c },
      }));
    } else if (issue === "warn_temperature") {
      const already = events.some(
        (event) => event.kind === "safety_warning" && event.vessel_id === vesselId
          && event.data.reason === issue,
      );
      if (!already) {
        events.push(makeEvent(state, "safety_warning", commandId, {
          vesselId, ruleId: "",
          data: { reason: issue, temperature_milli_c: vessel.temperature_milli_c },
        }));
      }
    }
  }
  if (lockedReason) {
    state.phase = "safety_locked";
    state.safety = { reason: lockedReason, profile: profile.id };
  }
}

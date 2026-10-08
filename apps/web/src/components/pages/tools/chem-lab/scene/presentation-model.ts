/**
 * Pure presentation-motion model. Turns the session's read-only transition
 * metadata into one cancelable motion descriptor; owns no chemistry ledger
 * and issues no commands. All movement itself lives in CSS keyframes — the
 * scene only flips a class, so there are no per-frame React updates.
 *
 * Stage semantics (plan §7):
 * - prediction + accepted → "pending": soft neutral pulse only — never a
 *   success effect, the server has not confirmed yet;
 * - ack + accepted        → "accepted": the per-command one-shot motion;
 * - ack + rejected        → "rejected": one brief neutral flash, no effect.
 * Conflict/offline, session or pack change, reduced motion and hidden tabs
 * all collapse to no motion (the frame itself already carries the facts).
 */
import type { LabCommand } from "@/lib/api-chem-lab";

export type LabMotionStage = "pending" | "accepted" | "rejected";

export interface LabMotion {
  /** `${sessionId}:${packHash}:${revision}:${commandId}` — identity of the transition. */
  token: string;
  kind: LabCommand["kind"];
  /** Object ids touched by the command, actor first, then target/instrument. */
  subjects: readonly string[];
  stage: LabMotionStage;
  /** 0 → final state immediately (checkpoint / no visual). */
  durationMs: number;
}

/** Structurally compatible with useChemLabSession's LabDisplayTransition. */
export interface MotionTransition {
  commandId: string;
  commandKind: LabCommand["kind"];
  revision: number;
  accepted: boolean;
  authority: "prediction" | "ack";
  subjectIds: readonly string[];
}

export interface MotionEnvironment {
  sessionId: string | null;
  packHash: string | null;
  conflict: boolean;
  reducedMotion: boolean;
}

/** One-shot durations per command kind (display time, not engine time). */
export const MOTION_MS: Record<LabCommand["kind"], number> = {
  move: 420,
  release: 280,
  pick_up: 280,
  place: 380,
  pour: 650,
  aspirate: 460,
  dispense: 520,
  heat: 900,
  stir: 800,
  wait: 700,
  measure: 520,
  connect: 460,
  filter: 640,
  wash: 480,
  dispose: 380,
  checkpoint: 0,
};

/** Rejected commands get one brief neutral flash so the tap is acknowledged. */
export const REJECTED_FLASH_MS = 320;

/** Which command fields name the visual actor and its targets, per kind. */
const KIND_SUBJECT_KEYS: Record<LabCommand["kind"], readonly string[]> = {
  pick_up: ["object_id"],
  place: ["object_id"],
  move: ["object_id"],
  release: ["object_id"],
  aspirate: ["instrument_id", "source_id"],
  dispense: ["instrument_id", "target_id"],
  pour: ["source_id", "target_id"],
  heat: ["vessel_id", "device_id"],
  stir: ["vessel_id"],
  wait: [],
  measure: ["instrument_id", "vessel_id"],
  connect: ["instrument_id", "vessel_id", "target_id"],
  filter: ["vessel_id", "apparatus_id"],
  wash: ["instrument_id"],
  dispose: ["vessel_id"],
  checkpoint: [],
};

/** Object ids a command visually touches, actor first (presentation only). */
export function subjectIdsOf(command: LabCommand): string[] {
  const record = command as unknown as Record<string, unknown>;
  const keys = KIND_SUBJECT_KEYS[command.kind] ?? ["object_id", "vessel_id"];
  const ids: string[] = [];
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value && !ids.includes(value)) ids.push(value);
  }
  return ids;
}

export function presentationToken(
  sessionId: string,
  packHash: string,
  revision: number,
  commandId: string,
): string {
  return `${sessionId}:${packHash}:${revision}:${commandId}`;
}

export function deriveMotion(
  env: MotionEnvironment,
  transition: MotionTransition | null,
): LabMotion | null {
  if (!transition || !env.sessionId || !env.packHash) return null;
  if (env.conflict) return null;
  if (env.reducedMotion) return null;
  const token = presentationToken(
    env.sessionId,
    env.packHash,
    transition.revision,
    transition.commandId,
  );
  const base = {
    token,
    kind: transition.commandKind,
    subjects: transition.subjectIds,
  };
  if (transition.authority === "prediction") {
    return transition.accepted
      ? { ...base, stage: "pending", durationMs: MOTION_MS[transition.commandKind] }
      : null;
  }
  if (!transition.accepted) {
    return { ...base, stage: "rejected", durationMs: REJECTED_FLASH_MS };
  }
  const durationMs = MOTION_MS[transition.commandKind];
  return durationMs > 0 ? { ...base, stage: "accepted", durationMs } : null;
}

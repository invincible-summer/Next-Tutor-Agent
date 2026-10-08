/**
 * Deterministic replay: fold a command script through the reducer and
 * collect per-step state hashes. Mirror of replay.py — shared by session
 * playback (`at` revision) and by the replay-vector fixtures that keep the
 * TS/Python engines aligned.
 */
import { stateHash, type AnyDict } from "./units.ts";
import { initialState } from "./model.ts";
import { applyCommand, primeVisibility } from "./reducer.ts";

export class ReplayError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

/**
 * Fold `script` from the pack's starting state.
 * Returns [state, events, hashes, acceptedMask] where hashes[0] is the
 * initial-state hash and hashes[i+1] follows command i.
 */
export function runScript(
  pack: AnyDict,
  script: AnyDict[],
  options: { mode?: string; sessionSeed?: number } = {},
): [AnyDict, AnyDict[], string[], boolean[]] {
  const state = initialState(pack, {
    mode: options.mode ?? "guided",
    sessionSeed: options.sessionSeed ?? 0,
    language: pack.language ?? "zh-CN",
  });
  primeVisibility(state, pack);
  const events: AnyDict[] = [];
  const hashes = [stateHash(state)];
  const acceptedMask: boolean[] = [];
  for (let index = 0; index < script.length; index += 1) {
    const command = script[index];
    if (command === null || typeof command !== "object" || Array.isArray(command)) {
      throw new ReplayError("bad_request", `script[${index}] 必须是对象`);
    }
    const [, cmdEvents, accepted] = applyCommand(state, pack, command, `replay-${index}`);
    events.push(...cmdEvents);
    hashes.push(stateHash(state));
    acceptedMask.push(accepted);
  }
  return [state, events, hashes, acceptedMask];
}

/**
 * Replay `script` and return the state at `revision` plus the events
 * emitted up to that point. Revision 0 is the initial state.
 */
export function replayToRevision(
  pack: AnyDict,
  script: AnyDict[],
  options: { mode: string; sessionSeed: number; revision: number },
): [AnyDict, AnyDict[]] {
  const { mode, sessionSeed, revision } = options;
  if (revision < 0 || revision > script.length) {
    throw new ReplayError("revision_out_of_range", `revision 需在 0..${script.length} 之间`);
  }
  const state = initialState(pack, {
    mode,
    sessionSeed,
    language: pack.language ?? "zh-CN",
  });
  primeVisibility(state, pack);
  const events: AnyDict[] = [];
  for (let index = 0; index < revision; index += 1) {
    const [, cmdEvents] = applyCommand(state, pack, script[index] as AnyDict, `replay-${index}`);
    events.push(...cmdEvents);
  }
  state._recent_events = events.slice(-8);
  return [state, events];
}

/** Check one replay vector against the engine; returns a list of problems. */
export function verifyVector(pack: AnyDict, vector: AnyDict): string[] {
  const problems: string[] = [];
  const script = vector.script ?? [];
  if (!Array.isArray(script)) return ["script 必须是数组"];
  let state: AnyDict;
  let events: AnyDict[];
  let hashes: string[];
  let acceptedMask: boolean[];
  try {
    [state, events, hashes, acceptedMask] = runScript(pack, script, {
      mode: vector.mode ?? "guided",
      sessionSeed: Number(vector.session_seed ?? 0),
    });
  } catch (error) {
    if (error instanceof ReplayError) {
      return [`回放失败: ${error.code}: ${error.message}`];
    }
    throw error;
  }
  const expectHashes = vector.state_hashes;
  if (Array.isArray(expectHashes)) {
    for (let i = 0; i < Math.min(hashes.length, expectHashes.length); i += 1) {
      if (hashes[i] !== expectHashes[i]) {
        problems.push(`state_hashes[${i}] 不一致: got ${hashes[i]} want ${expectHashes[i]}`);
        break;
      }
    }
    if (expectHashes.length !== hashes.length) {
      problems.push(`state_hashes 长度不一致: got ${hashes.length} want ${expectHashes.length}`);
    }
  }
  const finalHash = vector.final_hash;
  const lastHash = hashes[hashes.length - 1] as string;
  if (typeof finalHash === "string" && lastHash !== finalHash) {
    problems.push(`final_hash 不一致: got ${lastHash} want ${finalHash}`);
  }
  const expectAccepted = vector.accepted;
  if (Array.isArray(expectAccepted)
    && JSON.stringify(expectAccepted) !== JSON.stringify(acceptedMask)) {
    problems.push(`accepted 不一致: got ${JSON.stringify(acceptedMask)} want ${JSON.stringify(expectAccepted)}`);
  }
  const expectEvents = vector.expect_event_kinds;
  if (Array.isArray(expectEvents)) {
    const kinds = new Set(events.map((event) => event.kind));
    for (const kind of expectEvents) {
      if (!kinds.has(kind)) problems.push(`缺少事件 ${kind}`);
    }
  }
  const expectPhase = vector.expect_final_phase;
  if (typeof expectPhase === "string" && state.phase !== expectPhase) {
    problems.push(`终态 phase 不一致: got ${state.phase} want ${expectPhase}`);
  }
  const expectKeys = vector.expect_observation_keys;
  if (Array.isArray(expectKeys)) {
    const emitted = new Set((state.observations as AnyDict[]).map((o) => o.key));
    for (const key of expectKeys) {
      if (!emitted.has(key)) problems.push(`缺少观察 ${key}`);
    }
  }
  return problems;
}

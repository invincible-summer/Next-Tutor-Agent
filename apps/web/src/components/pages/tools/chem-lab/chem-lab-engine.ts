/**
 * Shared prediction runner for the chem-lab client: one engine instance over
 * a pinned pack + mutable candidate state. Used by the Web Worker and by the
 * main-thread fallback when Worker is unavailable — both must produce
 * byte-identical predictions, so the logic lives here exactly once.
 *
 * The runner never talks to the network and is never an authority; the
 * server's ACK replaces any divergent prediction.
 */
import {
  applyCommand,
  buildGuidance,
  renderFrame,
  stateHash,
  type AnyDict,
} from "@next-tutor/domain";

export interface EngineRunResult {
  ok: boolean;
  accepted: boolean;
  errorCode: string;
  revision: number;
  seq: number;
  stateHash: string;
  state: AnyDict | null;
  events: AnyDict[];
  observations: AnyDict[];
  guidance: AnyDict | null;
  renderFrame: AnyDict | null;
}

export interface ChemLabEngineRunner {
  init(pack: AnyDict, state: AnyDict): void;
  reset(state: AnyDict): void;
  runOne(command: AnyDict, commandId: string): EngineRunResult;
  runAll(commands: Array<{ command: AnyDict; commandId: string }>): EngineRunResult;
}

export function createChemLabEngineRunner(): ChemLabEngineRunner {
  let pack: AnyDict | null = null;
  let state: AnyDict | null = null;

  const visibleKeys = (): Set<string> | null => {
    const table = pack?.observation_visibility ?? {};
    const keys = table[state?.mode ?? ""];
    return Array.isArray(keys) ? new Set(keys as string[]) : null;
  };

  const publicEvents = (events: AnyDict[]): AnyDict[] => {
    const visible = visibleKeys();
    if (visible === null) return events.map((event) => ({ ...event }));
    return events.filter(
      (event) =>
        !(event.kind === "observation_emitted" && !visible.has(String(event.data?.key ?? ""))),
    );
  };

  const publicObservations = (observations: AnyDict[]): AnyDict[] => {
    const visible = visibleKeys();
    if (visible === null) return observations.map((obs) => ({ ...obs }));
    return observations.filter((obs) => visible.has(String(obs.key ?? "")));
  };

  const snapshot = (events: AnyDict[], observations: AnyDict[]): EngineRunResult => {
    if (!pack || !state) throw new Error("engine not initialised");
    return {
      ok: true,
      accepted: true,
      errorCode: "",
      revision: Number(state.revision ?? 0),
      seq: Number(state.seq ?? 0),
      stateHash: stateHash(state),
      state,
      events,
      observations,
      guidance: buildGuidance(pack, state, {
        lastEvents: events,
        language: String(state.language ?? "zh"),
      }),
      renderFrame: renderFrame(state, pack._species_defs ?? {}),
    };
  };

  const runOne = (command: AnyDict, commandId: string): EngineRunResult => {
    if (!pack || !state) throw new Error("engine not initialised");
    const observationsBefore = (state.observations ?? []).length;
    const [next, events, accepted, errorCode] = applyCommand(state, pack, command, commandId);
    state = next;
    const result = snapshot(
      publicEvents(events),
      publicObservations((state.observations ?? []).slice(observationsBefore)),
    );
    result.accepted = accepted;
    result.errorCode = errorCode ?? "";
    return result;
  };

  const runAll = (commands: Array<{ command: AnyDict; commandId: string }>): EngineRunResult => {
    const mergedEvents: AnyDict[] = [];
    const mergedObservations: AnyDict[] = [];
    let last: EngineRunResult | null = null;
    for (const item of commands) {
      last = runOne(item.command, item.commandId);
      mergedEvents.push(...last.events);
      mergedObservations.push(...last.observations);
    }
    if (!last) last = snapshot([], []);
    return { ...last, events: mergedEvents, observations: mergedObservations };
  };

  return {
    init(nextPack, nextState) {
      pack = nextPack;
      state = nextState;
    },
    reset(nextState) {
      state = nextState;
    },
    runOne,
    runAll,
  };
}

"use client";

/**
 * Chem-lab session controller — owns the server snapshot, the worker
 * prediction chain and the ordered pending-command queue.
 *
 * Invariants:
 * - every command carries a stable command_id; resending it is always safe;
 * - queue position i gets base_revision = server_tip + i (every command,
 *   accepted or rejected, bumps the revision by exactly one);
 * - an ACK whose state_hash equals the worker's prediction for that command
 *   proves the two engines agree, so the prediction becomes server truth
 *   without a refetch; any divergence pauses the queue, pulls the server
 *   snapshot and keeps the unconfirmed commands for diagnosis;
 * - HTTP 409 (revision/pack conflict) pauses the queue until the user
 *   re-syncs; network failures keep the queue for an explicit retry;
 * - switching session/account/experiment bumps `generation`; late worker or
 *   network callbacks from an older generation are discarded.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import type { AnyDict } from "@next-tutor/domain";
import type {
  ChemLabCommandAck,
  ChemLabEnginePack,
  ChemLabEvent,
  ChemLabGuidance,
  ChemLabObservation,
  ChemLabRenderFrame,
  ChemLabSessionSnapshot,
  LabCommand,
} from "@/lib/api-chem-lab";
import {
  ChemLabToolError,
  getChemLabSession,
  postChemLabCommand,
} from "@/lib/api-chem-lab";
import { useChemLabWorker } from "./useChemLabWorker";
import type { ChemLabPrediction } from "./chem-lab-worker";

export type ChemLabSyncStatus =
  | "idle"
  | "synced"
  | "predicting"
  | "pending_sync"
  | "conflict"
  | "offline_preview"
  | "safety_locked";

export interface QueuedCommand {
  commandId: string;
  clientSeq: number;
  baseRevision: number;
  command: LabCommand;
  predictedHash?: string;
}

export interface ChemLabConflict {
  reason: "diverged" | "revision" | "pack" | "session";
  commands: QueuedCommand[];
}

export interface ChemLabDisplay {
  renderFrame: ChemLabRenderFrame | null;
  guidance: ChemLabGuidance | null;
  phase: string;
  revision: number;
  simTimeMs: number;
  engineState: AnyDict | null;
  events: ChemLabEvent[];
  observations: ChemLabObservation[];
  serverRevision: number;
}

const EVENT_CAP = 240;
const OBSERVATION_CAP = 240;

function asEvents(value: unknown): ChemLabEvent[] {
  return Array.isArray(value) ? (value as ChemLabEvent[]) : [];
}

export function useChemLabSession() {
  const getWorker = useChemLabWorker();
  const [snapshot, setSnapshot] = useState<ChemLabSessionSnapshot | null>(null);
  const [prediction, setPrediction] = useState<ChemLabPrediction | null>(null);
  const [syncStatus, setSyncStatus] = useState<ChemLabSyncStatus>("idle");
  const [pendingCount, setPendingCount] = useState(0);
  const [conflict, setConflict] = useState<ChemLabConflict | null>(null);
  const [lastRejection, setLastRejection] = useState<{ commandId: string; errorCode: string } | null>(null);
  const [notice, setNotice] = useState("");
  const [events, setEvents] = useState<ChemLabEvent[]>([]);
  const [observations, setObservations] = useState<ChemLabObservation[]>([]);
  const [degraded, setDegraded] = useState(false);

  const queueRef = useRef<QueuedCommand[]>([]);
  const drainingRef = useRef(false);
  const generationRef = useRef(0);
  const clientSeqRef = useRef(0);
  const packRef = useRef<ChemLabEnginePack | null>(null);

  const worker = useCallback(() => {
    const handle = getWorker();
    setDegraded(handle.degraded);
    return handle;
  }, [getWorker]);

  /** Seed the controller with a fresh server snapshot + pinned engine pack. */
  const attach = useCallback(
    async (next: ChemLabSessionSnapshot, pack: ChemLabEnginePack) => {
      const token = ++generationRef.current;
      queueRef.current = [];
      drainingRef.current = false;
      clientSeqRef.current = 0;
      packRef.current = pack;
      setPendingCount(0);
      setPrediction(null);
      setConflict(null);
      setLastRejection(null);
      setSnapshot(next);
      setEvents(asEvents(next.recent_events).slice(-EVENT_CAP));
      setObservations([...(next.observations ?? [])].slice(-OBSERVATION_CAP));
      setSyncStatus(next.phase === "safety_locked" ? "safety_locked" : "synced");
      if (next.engine_state) {
        await worker().init(pack.pack as AnyDict, next.engine_state as AnyDict);
        if (generationRef.current !== token) return;
        setDegraded(worker().degraded);
      }
    },
    [worker],
  );

  const detach = useCallback(() => {
    generationRef.current += 1;
    queueRef.current = [];
    drainingRef.current = false;
    packRef.current = null;
    setSnapshot(null);
    setPrediction(null);
    setPendingCount(0);
    setConflict(null);
    setLastRejection(null);
    setEvents([]);
    setObservations([]);
    setSyncStatus("idle");
    setNotice("");
  }, []);

  const tipRevision = useCallback((): number => {
    const queue = queueRef.current;
    if (queue.length) {
      const last = queue[queue.length - 1]!;
      return last.baseRevision + 1;
    }
    return prediction?.revision ?? snapshot?.revision ?? 0;
  }, [prediction, snapshot]);

  const applyPrediction = useCallback(
    (result: ChemLabPrediction, token: number) => {
      if (generationRef.current !== token) return;
      const queued = queueRef.current.find((item) => item.commandId === result.commandId);
      if (queued) queued.predictedHash = result.stateHash;
      setPrediction(result);
      setEvents((prev) => [...prev, ...(result.events as ChemLabEvent[])].slice(-EVENT_CAP));
      setObservations(
        (prev) =>
          [...prev, ...(result.observations as ChemLabObservation[])].slice(-OBSERVATION_CAP),
      );
      const phase = String((result.state as AnyDict | null)?.phase ?? "");
      setSyncStatus(
        phase === "safety_locked"
          ? "safety_locked"
          : queueRef.current.length > 1
            ? "pending_sync"
            : "predicting",
      );
    },
    [],
  );

  const enterConflict = useCallback((reason: ChemLabConflict["reason"]) => {
    setConflict({ reason, commands: [...queueRef.current] });
    setSyncStatus("conflict");
  }, []);

  /** Serial drain: one POST in flight; the queue order is the server order. */
  const drain = useCallback(
    async (sessionId: string, token: number) => {
      if (drainingRef.current) return;
      drainingRef.current = true;
      try {
        while (queueRef.current.length) {
          if (generationRef.current !== token) return;
          const head = queueRef.current[0]!;
          const pack = packRef.current;
          if (!pack) return;
          let ack: ChemLabCommandAck;
          try {
            ack = await postChemLabCommand(sessionId, {
              command_id: head.commandId,
              client_seq: head.clientSeq,
              base_revision: head.baseRevision,
              pack_hash: pack.pack_hash,
              command: head.command,
            });
          } catch (error) {
            if (generationRef.current !== token) return;
            if (error instanceof ChemLabToolError && error.status === 409) {
              enterConflict(error.code === "chem_lab_pack_conflict" ? "pack" : "revision");
              return;
            }
            if (error instanceof ChemLabToolError && error.code === "chem_lab_session_missing") {
              enterConflict("session");
              return;
            }
            // Network / abort: keep the queue, offer an explicit retry.
            setSyncStatus("offline_preview");
            return;
          }
          if (generationRef.current !== token) return;
          if (head.predictedHash && ack.state_hash !== head.predictedHash) {
            // Engines diverged: adopt the server, keep commands for diagnosis.
            enterConflict("diverged");
            setNotice("syncedFromServer");
            try {
              const fresh = await getChemLabSession(sessionId);
              if (generationRef.current !== token) return;
              queueRef.current = [];
              setPendingCount(0);
              setPrediction(null);
              setSnapshot(fresh);
              setEvents(asEvents(fresh.recent_events).slice(-EVENT_CAP));
              setObservations([...(fresh.observations ?? [])].slice(-OBSERVATION_CAP));
              if (fresh.engine_state && packRef.current) {
                await worker().reset(fresh.engine_state as AnyDict);
              }
            } catch { /* conflict panel offers a manual re-sync */ }
            return;
          }
          queueRef.current.shift();
          setPendingCount(queueRef.current.length);
          if (!ack.accepted) {
            setLastRejection({ commandId: ack.command_id, errorCode: ack.error_code ?? "" });
          }
          setSnapshot((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              revision: ack.revision,
              seq: ack.seq_to,
              state_hash: ack.state_hash,
              guidance: ack.guidance ?? prev.guidance,
              render_frame: ack.render_frame ?? prev.render_frame,
              recent_events: ack.events ?? prev.recent_events,
            };
          });
        }
        if (generationRef.current !== token) return;
        setSyncStatus((prev) => (prev === "safety_locked" ? prev : "synced"));
      } finally {
        drainingRef.current = false;
      }
    },
    [enterConflict, worker],
  );

  /** Enqueue one closed command: worker predicts first, then the queue drains. */
  const send = useCallback(
    (command: LabCommand) => {
      const snap = snapshot;
      const pack = packRef.current;
      if (!snap || !pack) return;
      if (syncStatus === "conflict" || syncStatus === "offline_preview") return;
      if (snap.phase === "safety_locked") return;
      const token = generationRef.current;
      const queued: QueuedCommand = {
        commandId: crypto.randomUUID(),
        clientSeq: ++clientSeqRef.current,
        baseRevision: tipRevision(),
        command,
      };
      queueRef.current.push(queued);
      setPendingCount(queueRef.current.length);
      setLastRejection(null);
      setSyncStatus(queueRef.current.length > 1 ? "pending_sync" : "predicting");
      void worker()
        .predict(command as AnyDict, queued.commandId)
        .then((result) => applyPrediction(result, token))
        .catch(() => {
          if (generationRef.current === token) setSyncStatus("offline_preview");
        });
      void drain(snap.session_id, token);
    },
    [applyPrediction, drain, snapshot, syncStatus, tipRevision, worker],
  );

  /** Pull the authoritative snapshot and reseed the worker (conflict recovery). */
  const resync = useCallback(async () => {
    const id = snapshot?.session_id;
    const pack = packRef.current;
    if (!id || !pack) return;
    const fresh = await getChemLabSession(id);
    await attach(fresh, pack);
    setNotice("syncedFromServer");
  }, [attach, snapshot]);

  /** Retry the preserved queue after a network interruption. */
  const retryPending = useCallback(() => {
    const snap = snapshot;
    if (!snap || !queueRef.current.length) return;
    setSyncStatus("pending_sync");
    void drain(snap.session_id, generationRef.current);
  }, [drain, snapshot]);

  const dismissConflict = useCallback(() => {
    setConflict(null);
    setSyncStatus((prev) => (prev === "conflict" ? "synced" : prev));
  }, []);

  const display = useMemo<ChemLabDisplay>(() => {
    const engineState = (prediction?.state as AnyDict | null) ??
      (snapshot?.engine_state as AnyDict | null) ?? null;
    return {
      renderFrame: (prediction?.renderFrame as ChemLabRenderFrame | null) ?? snapshot?.render_frame ?? null,
      guidance: (prediction?.guidance as ChemLabGuidance | null) ?? snapshot?.guidance ?? null,
      phase: String(engineState?.phase ?? snapshot?.phase ?? "ready"),
      revision: prediction?.revision ?? snapshot?.revision ?? 0,
      simTimeMs: Number(engineState?.sim_time_ms ?? snapshot?.sim_time_ms ?? 0),
      engineState,
      events,
      observations,
      serverRevision: snapshot?.revision ?? 0,
    };
  }, [prediction, snapshot, events, observations]);

  return {
    snapshot,
    display,
    syncStatus,
    pendingCount,
    conflict,
    lastRejection,
    notice,
    degraded,
    attach,
    detach,
    send,
    resync,
    retryPending,
    dismissConflict,
    clearNotice: useCallback(() => setNotice(""), []),
  };
}

export type ChemLabSessionController = ReturnType<typeof useChemLabSession>;

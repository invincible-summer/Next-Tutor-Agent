/**
 * Scenario illustration (tool assistant) shared client — see docs/architecture/client-platform.md.
 *
 * Observer semantics baked in here so Web and Mobile share them while each
 * keeps its own UI state, timers and navigation:
 *
 * - `submitTurn` always carries a stable `request_id` plus the current
 *   `base_revision` (and optional `source_revision` for optimistic reads);
 * - if the POST response is lost, `submitTurnWithRecovery` recovers the same
 *   logical turn from the session projection via `request_id` — never a
 *   duplicate generation;
 * - polling treats the server terminal state (`ready`/`failed`) as the only
 *   truth: a client timeout merely stops observation (`observation_stopped`),
 *   it never fabricates a local `failed`;
 * - domain errors (session busy / revision conflict / material missing /
 *   version conflict / source revision missing …) surface as
 *   `IllustrationApiError` with the server's code;
 * - backgrounding suspends polling via `shouldPause`; after foreground the
 *   next fetch re-syncs from the server (`getJob` + `getSession` are the
 *   re-sync primitives).
 *
 * V1 turns submit no materials; V2/V3 submit `{asset_id, version}` refs only
 * — SVG source text never travels back up.
 */
import type {
  DeletedSessionAck,
  IllustrationSession,
  IllustrationSessionList,
  ScenarioTurn,
  SelectedMaterialRef,
  ToolIllustrationJob,
} from "@next-tutor/contracts";
import type { AbortSignalLike, Transport } from "../types.ts";
import {
  ApiError,
  IllustrationApiError,
  NetworkError,
  illustrationErrorFrom,
} from "../errors.ts";

/** Derived from the generated job DTO — the generator inlines this union. */
export type IllustrationMode = ToolIllustrationJob["mode"];

export interface SubmitTurnPayload {
  message: string;
  mode: IllustrationMode;
  /** V1: `[]`; V2/V3: `{asset_id, version}` refs only. */
  selected_materials: SelectedMaterialRef[];
  base_revision: number;
  source_revision?: number;
  /** Stable id, reused verbatim across retries/recovery of this turn. */
  request_id: string;
}

export interface ToolIllustrationClient {
  listSessions(signal?: AbortSignalLike | null): Promise<IllustrationSessionList>;
  createSession(title?: string, signal?: AbortSignalLike | null): Promise<IllustrationSession>;
  getSession(id: string, signal?: AbortSignalLike | null): Promise<IllustrationSession>;
  deleteSession(id: string, signal?: AbortSignalLike | null): Promise<DeletedSessionAck>;
  submitTurn(
    sessionId: string,
    payload: SubmitTurnPayload,
    signal?: AbortSignalLike | null,
  ): Promise<ToolIllustrationJob>;
  getJob(jobId: string, signal?: AbortSignalLike | null): Promise<ToolIllustrationJob>;
  retryJob(jobId: string, signal?: AbortSignalLike | null): Promise<ToolIllustrationJob>;

  // --- §5.2.1 recovery / observation primitives -----------------------------
  findTurnByRequestId(session: IllustrationSession, requestId: string): ScenarioTurn | null;
  /** Locate the turn a lost POST created; `job` null when it was reaped. */
  recoverTurn(
    sessionId: string,
    requestId: string,
    signal?: AbortSignalLike | null,
  ): Promise<{ turn: ScenarioTurn; job: ToolIllustrationJob | null } | null>;
  /** submitTurn + lost-response recovery via `payload.request_id`. */
  submitTurnWithRecovery(
    sessionId: string,
    payload: SubmitTurnPayload,
    signal?: AbortSignalLike | null,
  ): Promise<ToolIllustrationJob>;
  /** Server-truth job poller; see `JobPollEvent` for stop semantics. */
  pollJob(
    jobId: string,
    options?: PollJobOptions,
  ): AsyncGenerator<JobPollEvent>;
}

export type JobPollEvent =
  | { kind: "job"; job: ToolIllustrationJob }
  | { kind: "observation_stopped"; reason: "timeout" | "aborted" };

export interface PollJobOptions {
  /** Poll interval; default 1500ms. */
  intervalMs?: number;
  /** Observation budget. Exceeding it STOPS OBSERVING only — no local `failed`. */
  timeoutMs?: number;
  signal?: AbortSignalLike | null;
  /** While true the poller suspends network calls (app backgrounded). */
  shouldPause?: () => boolean;
  /** Test hook replacing interval sleeps. */
  sleepImpl?: (ms: number) => Promise<void>;
}

const TERMINAL_JOB_STATUSES = new Set(["ready", "failed"]);

function domainError(error: unknown): Error {
  if (error instanceof IllustrationApiError) return error;
  // NetworkError extends ApiError — check it first so a lost response stays
  // recoverable instead of being wrapped into a typed domain error.
  if (error instanceof NetworkError) {
    if (!error.retryable) {
      return new IllustrationApiError("run_interrupted", error.message, 0, true);
    }
    return error;
  }
  if (error instanceof ApiError) return illustrationErrorFrom(error);
  return error instanceof Error ? error : new Error(String(error));
}

export function createToolIllustrationClient(transport: Transport): ToolIllustrationClient {
  const call = async <T>(path: string, init?: {
    method?: string | undefined;
    json?: unknown;
    signal?: AbortSignalLike | null | undefined;
  }): Promise<T> => {
    try {
      const result = await transport.request<T>(path, init);
      return result.body as T;
    } catch (error) {
      throw domainError(error);
    }
  };

  const submitTurn = async (
    sessionId: string,
    payload: SubmitTurnPayload,
    signal?: AbortSignalLike | null,
  ): Promise<ToolIllustrationJob> =>
    call<ToolIllustrationJob>(`/tools/illustration/sessions/${encodeURIComponent(sessionId)}/turns`, {
      method: "POST",
      json: payload,
      signal,
    });

  const getJob = async (jobId: string, signal?: AbortSignalLike | null) =>
    call<ToolIllustrationJob>(`/tools/illustration/jobs/${encodeURIComponent(jobId)}`, { signal });

  const recoverTurn = async (
    sessionId: string,
    requestId: string,
    signal?: AbortSignalLike | null,
  ): Promise<{ turn: ScenarioTurn; job: ToolIllustrationJob | null } | null> => {
    const session = await call<IllustrationSession>(
      `/tools/illustration/sessions/${encodeURIComponent(sessionId)}`,
      { signal },
    );
    const turn = session.turns.find((candidate) => candidate.request_id === requestId) ?? null;
    if (!turn) return null;
    try {
      const job = await getJob(turn.job_id, signal);
      return { turn, job };
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return { turn, job: null };
      throw error;
    }
  };

  return {
    listSessions: (signal) =>
      call<IllustrationSessionList>("/tools/illustration/sessions", { signal }),
    createSession: (title = "", signal) =>
      call<IllustrationSession>("/tools/illustration/sessions", {
        method: "POST",
        json: { title },
        signal,
      }),
    getSession: (id, signal) =>
      call<IllustrationSession>(`/tools/illustration/sessions/${encodeURIComponent(id)}`, {
        signal,
      }),
    deleteSession: (id, signal) =>
      call<DeletedSessionAck>(`/tools/illustration/sessions/${encodeURIComponent(id)}`, {
        method: "DELETE",
        signal,
      }),
    submitTurn,
    getJob,
    retryJob: (jobId, signal) =>
      call<ToolIllustrationJob>(`/tools/illustration/jobs/${encodeURIComponent(jobId)}/retry`, {
        method: "POST",
        signal,
      }),

    findTurnByRequestId: (session, requestId) =>
      session.turns.find((turn) => turn.request_id === requestId) ?? null,

    recoverTurn,

    async submitTurnWithRecovery(sessionId, payload, signal) {
      try {
        return await submitTurn(sessionId, payload, signal);
      } catch (error) {
        // Only a lost POST response qualifies for recovery; HTTP answers
        // (busy / revision conflict / material errors) are definitive.
        if (!(error instanceof NetworkError) || !error.retryable) throw error;
        const recovered = await recoverTurn(sessionId, payload.request_id, signal);
        if (!recovered || !recovered.job) throw error;
        return recovered.job;
      }
    },

    async *pollJob(jobId, options = {}) {
      const interval = options.intervalMs ?? 1500;
      const sleep = options.sleepImpl ?? pollSleep;
      const deadline = options.timeoutMs !== undefined ? Date.now() + options.timeoutMs : null;
      const stopped = (reason: "timeout" | "aborted"): JobPollEvent => ({
        kind: "observation_stopped",
        reason,
      });
      while (true) {
        if (options.signal?.aborted) {
          yield stopped("aborted");
          return;
        }
        if (deadline !== null && Date.now() >= deadline) {
          yield stopped("timeout");
          return;
        }
        while (options.shouldPause?.() ?? false) {
          await sleep(interval);
          if (options.signal?.aborted) {
            yield stopped("aborted");
            return;
          }
          if (deadline !== null && Date.now() >= deadline) {
            yield stopped("timeout");
            return;
          }
        }
        const job = await getJob(jobId, options.signal ?? null);
        yield { kind: "job", job };
        if (TERMINAL_JOB_STATUSES.has(job.status)) return;
        await sleep(interval);
      }
    },
  };
}

function pollSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

/**
 * Virtual chemistry bench shared client — see docs/architecture/client-platform.md.
 *
 * Semantics baked in here so Web and Mobile share them:
 *
 * - every command POST carries a stable `command_id`, the caller's
 *   `base_revision` and the session's pinned `pack_hash`; the server treats a
 *   repeated `command_id` as a retry and returns the original ACK, so a lost
 *   response is recovered by simply resending the identical payload;
 * - HTTP 409 conflicts (`chem_lab_revision_conflict` / `chem_lab_pack_conflict`)
 *   surface as typed `ChemLabApiError`; the client must re-sync via
 *   `getSession` before issuing further commands — never retry blindly;
 * - domain rejections (`accepted=false` in a 200 ACK) are displayable
 *   results, not exceptions: they carry `error_code`, guidance and the
 *   rejection event for the UI to render;
 * - `getEvents(after_seq)` is the reconnect primitive; snapshots via
 *   `getSession` are the recovery primitive after a page reload.
 */
import type {
  ChemLabCatalog,
  ChemLabCommandAck,
  ChemLabCommandRequest,
  ChemLabCreateSession,
  ChemLabDeletedAck,
  ChemLabEnginePack,
  ChemLabEventPage,
  ChemLabExperimentDetail,
  ChemLabForkRequest,
  ChemLabForkResult,
  ChemLabResetRequest,
  ChemLabResultCard,
  ChemLabSessionList,
  ChemLabSessionSnapshot,
} from "@next-tutor/contracts";
import type { AbortSignalLike, Transport } from "../types.ts";
import {
  ApiError,
  ChemLabApiError,
  NetworkError,
  chemLabErrorFrom,
} from "../errors.ts";

export interface ToolChemLabClient {
  listCatalog(signal?: AbortSignalLike | null): Promise<ChemLabCatalog>;
  getExperiment(
    experimentId: string,
    version?: string | null,
    signal?: AbortSignalLike | null,
  ): Promise<ChemLabExperimentDetail>;
  /** Runtime pack for the client-side engine mirror (prediction answers stripped). */
  getEnginePack(
    experimentId: string,
    version?: string | null,
    signal?: AbortSignalLike | null,
  ): Promise<ChemLabEnginePack>;
  listSessions(cursor?: string | null, signal?: AbortSignalLike | null): Promise<ChemLabSessionList>;
  createSession(
    payload: ChemLabCreateSession,
    signal?: AbortSignalLike | null,
  ): Promise<ChemLabSessionSnapshot>;
  getSession(sessionId: string, signal?: AbortSignalLike | null): Promise<ChemLabSessionSnapshot>;
  deleteSession(sessionId: string, signal?: AbortSignalLike | null): Promise<ChemLabDeletedAck>;
  postCommand(
    sessionId: string,
    payload: ChemLabCommandRequest,
    signal?: AbortSignalLike | null,
  ): Promise<ChemLabCommandAck>;
  getEvents(
    sessionId: string,
    afterSeq: number,
    limit?: number,
    signal?: AbortSignalLike | null,
  ): Promise<ChemLabEventPage>;
  createCheckpoint(
    sessionId: string,
    label: string,
    signal?: AbortSignalLike | null,
  ): Promise<ChemLabCommandAck>;
  fork(
    sessionId: string,
    payload: ChemLabForkRequest,
    signal?: AbortSignalLike | null,
  ): Promise<ChemLabForkResult>;
  reset(
    sessionId: string,
    payload: ChemLabResetRequest,
    signal?: AbortSignalLike | null,
  ): Promise<ChemLabForkResult>;
  finish(sessionId: string, signal?: AbortSignalLike | null): Promise<ChemLabResultCard>;
}

function domainError(error: unknown): Error {
  if (error instanceof ChemLabApiError) return error;
  // NetworkError extends ApiError — a lost POST response stays resend-safe
  // (stable command_id), so keep it distinct from typed domain errors.
  if (error instanceof NetworkError) return error;
  if (error instanceof ApiError) return chemLabErrorFrom(error);
  return error instanceof Error ? error : new Error(String(error));
}

/**
 * Every chem-lab call carries its own timeout: the server ops are deterministic
 * local computations (millisecond scale), so a request that never settles can
 * only be a lost/stuck transport — it must surface as a typed error the UI can
 * render with retry, never an eternal spinner. (The transport's default
 * read timeout only applies to signal-less GET/HEAD; POSTs and signal-carrying
 * reads would otherwise wait forever.)
 */
const REQUEST_TIMEOUT_MS = 30_000;

export function createToolChemLabClient(transport: Transport): ToolChemLabClient {
  const call = async <T>(path: string, init?: {
    method?: string | undefined;
    json?: unknown;
    signal?: AbortSignalLike | null | undefined;
  }): Promise<T> => {
    try {
      const result = await transport.request<T>(path, {
        timeoutMs: REQUEST_TIMEOUT_MS,
        ...init,
      });
      return result.body as T;
    } catch (error) {
      throw domainError(error);
    }
  };

  const sessionPath = (sessionId: string) =>
    `/tools/lab/chemistry/sessions/${encodeURIComponent(sessionId)}`;

  return {
    listCatalog: (signal) =>
      call<ChemLabCatalog>("/tools/lab/chemistry/catalog", { signal }),
    getExperiment: (experimentId, version, signal) =>
      call<ChemLabExperimentDetail>(
        `/tools/lab/chemistry/experiments/${encodeURIComponent(experimentId)}`
          + (version ? `?version=${encodeURIComponent(version)}` : ""),
        { signal },
      ),
    getEnginePack: (experimentId, version, signal) =>
      call<ChemLabEnginePack>(
        `/tools/lab/chemistry/experiments/${encodeURIComponent(experimentId)}/engine-pack`
          + (version ? `?version=${encodeURIComponent(version)}` : ""),
        { signal },
      ),
    listSessions: (cursor, signal) =>
      call<ChemLabSessionList>(
        "/tools/lab/chemistry/sessions"
          + (cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""),
        { signal },
      ),
    createSession: (payload, signal) =>
      call<ChemLabSessionSnapshot>("/tools/lab/chemistry/sessions", {
        method: "POST",
        json: payload,
        signal,
      }),
    getSession: (sessionId, signal) =>
      call<ChemLabSessionSnapshot>(sessionPath(sessionId), { signal }),
    deleteSession: (sessionId, signal) =>
      call<ChemLabDeletedAck>(sessionPath(sessionId), { method: "DELETE", signal }),
    postCommand: (sessionId, payload, signal) =>
      call<ChemLabCommandAck>(`${sessionPath(sessionId)}/commands`, {
        method: "POST",
        json: payload,
        signal,
      }),
    getEvents: (sessionId, afterSeq, limit = 200, signal) =>
      call<ChemLabEventPage>(
        `${sessionPath(sessionId)}/events?after_seq=${afterSeq}&limit=${limit}`,
        { signal },
      ),
    createCheckpoint: (sessionId, label, signal) =>
      call<ChemLabCommandAck>(`${sessionPath(sessionId)}/checkpoints`, {
        method: "POST",
        json: { label },
        signal,
      }),
    fork: (sessionId, payload, signal) =>
      call<ChemLabForkResult>(`${sessionPath(sessionId)}/fork`, {
        method: "POST",
        json: payload,
        signal,
      }),
    reset: (sessionId, payload, signal) =>
      call<ChemLabForkResult>(`${sessionPath(sessionId)}/reset`, {
        method: "POST",
        json: payload,
        signal,
      }),
    finish: (sessionId, signal) =>
      call<ChemLabResultCard>(`${sessionPath(sessionId)}/finish`, {
        method: "POST",
        signal,
      }),
  };
}

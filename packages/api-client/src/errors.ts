/**
 * Error envelope for the shared client (docs/architecture/client-platform.md): transport failures,
 * HTTP error statuses and typed illustration domain errors.
 *
 * Server error bodies follow the repo convention
 * `{detail: {error: {code, message, retryable?}}}` or the flat
 * `{error: {code, message}}` variant; both are extracted. Unknown shapes
 * degrade to `status_<code>` codes so nothing is silently swallowed.
 */
import type { ResponseLike } from "./types.ts";

export type ApiErrorCode = string;

export class ApiError extends Error {
  /** HTTP status; 0 for network-level failures. */
  readonly status: number;
  readonly code: ApiErrorCode;
  /** Transport-level hint: network errors and 408/429/5xx default true. */
  readonly retryable: boolean;
  /** Raw parsed error body (if JSON) for domain-specific inspection. */
  readonly details: unknown;

  constructor(code: ApiErrorCode, message: string, status: number, retryable = false, details?: unknown) {
    super(message || code);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    this.details = details;
  }
}

/** `fetchImpl` rejected / timed out before a response arrived. */
export class NetworkError extends ApiError {
  constructor(message: string, options?: { retryable?: boolean; cause?: unknown }) {
    super("network_error", message, 0, options?.retryable ?? true);
    this.name = "NetworkError";
    if (options?.cause !== undefined) this.cause = options.cause;
  }
}

/** 401 observed after the (optional) single-flight refresh attempt. */
export class UnauthorizedError extends ApiError {
  constructor(message: string, details?: unknown) {
    super("unauthorized", message, 401, false, details);
    this.name = "UnauthorizedError";
  }
}

/** 409 domain conflict — passed through, never consumed by generic retry. */
export class ConflictError extends ApiError {
  constructor(code: ApiErrorCode, message: string, status: number, details?: unknown) {
    super(code, message, status, false, details);
    this.name = "ConflictError";
  }
}

export interface ServerErrorEnvelope {
  code?: string;
  message?: string;
  retryable?: boolean;
  [key: string]: unknown;
}

/**
 * Extract `{code, message, retryable}` from the repo's error envelope shapes:
 * `{detail: {error: {…}}}`, flat `{error: {…}}`, FastAPI `detail` strings and
 * the bare `{detail: {code}}` variant (speech endpoints).
 */
export function parseErrorBody(payload: unknown): ServerErrorEnvelope {
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    const detail = record.detail;
    if (detail && typeof detail === "object") {
      const detailRecord = detail as Record<string, unknown>;
      const nested = detailRecord.error;
      if (nested && typeof nested === "object") return nested as ServerErrorEnvelope;
      // `HTTPException(detail={"code": …})` without the error wrapper.
      if (typeof detailRecord.code === "string") return detailRecord as ServerErrorEnvelope;
    }
    if (record.error && typeof record.error === "object") {
      return record.error as ServerErrorEnvelope;
    }
    if (typeof detail === "string") return { code: detail, message: detail };
  }
  return {};
}

/** Build the ApiError subclass for a non-2xx response. */
export async function errorFromResponse(response: ResponseLike): Promise<ApiError> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON error body */
  }
  const envelope = parseErrorBody(payload);
  const code = typeof envelope.code === "string" && envelope.code
    ? envelope.code
    : `status_${response.status}`;
  const message = typeof envelope.message === "string" && envelope.message
    ? envelope.message
    : `HTTP ${response.status}`;
  if (response.status === 401) return new UnauthorizedError(message, payload);
  if (response.status === 409) return new ConflictError(code, message, response.status, payload);
  const retryable = typeof envelope.retryable === "boolean"
    ? envelope.retryable
    : response.status === 408 || response.status === 429 || response.status >= 500;
  return new ApiError(code, message, response.status, retryable, payload);
}

// --- illustration typed domain errors (see client-platform.md) ------------------------
//
// Codes are the server's SceneError / ReferenceError payloads (see
// app/agents/teaching_engine illustration domain). The union covers every code
// the clients are expected to branch on; unknown server codes still surface as
// IllustrationApiError with the raw string preserved.

export const ILLUSTRATION_ERROR_CODES = [
  // Scenario / tool assistant (SceneError)
  "illustration_context_limit",
  "illustration_disabled",
  "illustration_idempotency_conflict",
  "illustration_job_missing",
  "illustration_job_not_retryable",
  "illustration_revision_conflict",
  "illustration_session_busy",
  "illustration_session_missing",
  "illustration_source_revision_missing",
  // Material references (ReferenceError)
  "illustration_material_missing",
  "illustration_material_version_conflict",
  "illustration_material_budget_exceeded",
  // Quiz illustration request failures surfaced as codes
  "run_interrupted",
] as const;

export type IllustrationErrorCode =
  | (typeof ILLUSTRATION_ERROR_CODES)[number]
  | (string & {});

/** Error thrown by illustration domain methods; `code` keeps server casing. */
export class IllustrationApiError extends ApiError {
  constructor(code: IllustrationErrorCode, message: string, status: number, retryable = false, details?: unknown) {
    super(code, message, status, retryable, details);
    this.name = "IllustrationApiError";
  }
}

export function isIllustrationError(error: unknown): error is IllustrationApiError {
  return error instanceof IllustrationApiError;
}

/** Retryable variants per the scenario contract: transient job interrupts may resume. */
const RETRYABLE_ILLUSTRATION_CODES = new Set<string>([
  "run_interrupted",
  "illustration_job_missing", // job may resurface after a store race
]);

export function illustrationErrorFrom(error: ApiError): IllustrationApiError {
  if (error instanceof IllustrationApiError) return error;
  const retryable = RETRYABLE_ILLUSTRATION_CODES.has(error.code)
    ? true
    : error.retryable;
  return new IllustrationApiError(error.code, error.message, error.status, retryable, error.details);
}

// --- chem-lab typed domain errors ------------------------------------------------
//
// Codes are the server's ChemLabError payloads (`chem_lab_*`, see
// app/api/v1/tool_chem_lab.py). Command-level rejections
// (chem_lab_invalid_command / chem_lab_not_modeled / chem_lab_safety_locked)
// normally arrive inside a 200 ACK with `accepted=false`; they are listed so
// clients can branch on the raw string when they do surface as errors.

export const CHEM_LAB_ERROR_CODES = [
  "chem_lab_disabled",
  "chem_lab_session_missing",
  "chem_lab_experiment_missing",
  "chem_lab_checkpoint_missing",
  "chem_lab_pack_conflict",
  "chem_lab_invalid_command",
  "chem_lab_command_duplicate",
  "chem_lab_revision_conflict",
  "chem_lab_revision_out_of_range",
  "chem_lab_safety_locked",
  "chem_lab_session_busy",
  "chem_lab_sync_required",
  "chem_lab_not_modeled",
  "chem_lab_limit_exceeded",
  "chem_lab_bad_request",
] as const;

export type ChemLabErrorCode =
  | (typeof CHEM_LAB_ERROR_CODES)[number]
  | (string & {});

/** Error thrown by chem-lab domain methods; `code` keeps server casing. */
export class ChemLabApiError extends ApiError {
  constructor(code: ChemLabErrorCode, message: string, status: number, retryable = false, details?: unknown) {
    super(code, message, status, retryable, details);
    this.name = "ChemLabApiError";
  }
}

export function isChemLabError(error: unknown): error is ChemLabApiError {
  return error instanceof ChemLabApiError;
}

export function chemLabErrorFrom(error: ApiError): ChemLabApiError {
  if (error instanceof ChemLabApiError) return error;
  return new ChemLabApiError(error.code, error.message, error.status, error.retryable, error.details);
}

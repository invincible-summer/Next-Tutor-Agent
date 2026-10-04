/**
 * Request pipeline for the shared client (see docs/architecture/client-platform.md).
 *
 * - every request carries `X-Client-Platform/Version/Build` (from the
 *   injected metadata provider) and `X-Request-ID` (stable across retries);
 * - credentials come from `tokenProvider` (Bearer) or `guestTokenProvider`
 *   (`X-Guest-Token`); this pipeline only issues requests against the
 *   configured `baseUrl` + known paths, so tokens never leak to
 *   response-supplied absolute URLs — platform adapters keep their own guard
 *   for arbitrary-URL fetches;
 * - GET/HEAD retry network errors and 429/502/503/504 at most twice with
 *   jittered backoff (Retry-After honored, capped); writes retry only when
 *   the endpoint opts in via `retryWrites` AND carries an Idempotency-Key;
 * - 401 triggers the `onUnauthorized` hook once (single-flight across
 *   concurrent requests); the call is retried only when the provider then
 *   yields a *different* token, otherwise the 401 surfaces immediately;
 * - 409 is surfaced as `ConflictError` unless the caller explicitly asked
 *   for a bounded `waitForConflict` re-poll (e.g. `evaluation_pending`).
 */
import { NetworkError, errorFromResponse, parseErrorBody } from "./errors.ts";
import type {
  AbortSignalLike,
  ClientMetadata,
  FetchLike,
  QueryValue,
  RequestOptions,
  RequestResult,
  ResponseLike,
  Transport,
} from "./types.ts";

const RETRYABLE_STATUSES = new Set([429, 502, 503, 504]);
const MAX_RETRIES = 2;
const DEFAULT_BACKOFF_MS = 300;
const MAX_BACKOFF_MS = 8_000;
const RETRY_AFTER_CAP_MS = 30_000;
const DEFAULT_CONFLICT_DEADLINE_MS = 20_000;
const MAX_CONFLICT_RETRIES = 10;

export interface ApiClientConfig {
  /** API origin including the version prefix, e.g. `https://host/api/v1`. */
  baseUrl: string;
  fetchImpl: FetchLike;
  /** Bearer token source; resolved fresh before every attempt. */
  tokenProvider?: () => string | null | undefined | Promise<string | null | undefined>;
  /** Guest token source, used only when `tokenProvider` yields nothing. */
  guestTokenProvider?: () => string | null | undefined;
  clientMetadataProvider?: () => ClientMetadata | null;
  requestIdFactory?: () => string;
  /**
   * 401 hook (refresh / session teardown). Concurrent 401s coalesce into a
   * single invocation; a resolved hook only earns a retry when the token
   * provider then returns a different token.
   */
  onUnauthorized?: () => void | Promise<void>;
  /** Applied to GET/HEAD when the caller passes no signal (Web: 30s). */
  defaultReadTimeoutMs?: number;
  /** Test hook replacing timed sleeps; production code never sets it. */
  sleepImpl?: (ms: number, signal?: AbortSignalLike | null) => Promise<void>;
}

export function createTransport(config: ApiClientConfig): Transport {
  const sleep = config.sleepImpl ?? defaultSleep;
  let refreshInFlight: Promise<void> | null = null;

  const singleFlightRefresh = (): Promise<void> => {
    if (!refreshInFlight) {
      // Assigned synchronously so concurrent 401s observe the same promise
      // before any await point.
      refreshInFlight = Promise.resolve(
        config.onUnauthorized ? config.onUnauthorized() : undefined,
      )
        .then(() => undefined)
        .finally(() => {
          refreshInFlight = null;
        });
    }
    return refreshInFlight;
  };

  /** Shared attempt loop: headers, retry, 401 refresh, conflict re-poll. */
  const execute = async (path: string, options?: RequestOptions): Promise<ResponseLike> => {
    const method = (options?.method ?? "GET").toUpperCase();
    const isRead = method === "GET" || method === "HEAD";
    const writeRetryAllowed =
      !isRead && options?.retryWrites === true && !!options?.idempotencyKey;
    const canRetry = isRead || writeRetryAllowed;
    const requestId = (config.requestIdFactory ?? defaultRequestIdFactory)();

    const url = joinUrl(config.baseUrl, path) + buildQuery(options?.query);
    const conflict = options?.waitForConflict;
    const conflictDeadline = conflict
      ? Date.now() + (conflict.deadlineMs ?? DEFAULT_CONFLICT_DEADLINE_MS)
      : 0;
    let conflictRetries = 0;
    let refreshAttempted = false;

    for (let attempt = 0; ; attempt += 1) {
      const token = await resolveToken(config.tokenProvider);
      const headers: Record<string, string> = { ...(options?.headers ?? {}) };
      let body = options?.body ?? null;
      if (options?.json !== undefined) {
        body = JSON.stringify(options.json);
        if (!hasHeader(headers, "content-type")) {
          headers["Content-Type"] = "application/json";
        }
      }
      headers["X-Request-ID"] = requestId;
      const metadata = config.clientMetadataProvider?.();
      if (metadata) {
        headers["X-Client-Platform"] = metadata.platform;
        if (metadata.version !== undefined) headers["X-Client-Version"] = metadata.version;
        if (metadata.build !== undefined) headers["X-Client-Build"] = metadata.build;
      }
      if (token) headers.Authorization = `Bearer ${token}`;
      else {
        const guestToken = config.guestTokenProvider?.();
        if (guestToken && !hasHeader(headers, "x-guest-token")) {
          headers["X-Guest-Token"] = guestToken;
        }
      }
      if (options?.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

      const timeoutMs = options?.timeoutMs
        ?? (isRead && !options?.signal ? config.defaultReadTimeoutMs : undefined);
      const timed = withTimeout(options?.signal ?? null, timeoutMs);

      let response: ResponseLike;
      try {
        response = await config.fetchImpl(url, {
          method,
          headers,
          body,
          signal: timed.signal,
        });
      } catch (error) {
        timed.cancel();
        if (isAbortLike(error, timed.signal)) {
          throw new NetworkError("request_aborted", { retryable: false, cause: error });
        }
        if (canRetry && attempt < MAX_RETRIES) {
          await sleep(backoffDelayMs(attempt), options?.signal ?? null);
          continue;
        }
        throw new NetworkError(errorMessage(error), { cause: error });
      }
      timed.cancel();

      if (response.status === 401) {
        if (config.onUnauthorized && !refreshAttempted) {
          refreshAttempted = true;
          await singleFlightRefresh();
          const refreshed = await resolveToken(config.tokenProvider);
          if (refreshed && refreshed !== token) continue;
        }
        throw await errorFromResponse(response);
      }

      if (
        response.status === 409 &&
        conflict &&
        !RETRYABLE_STATUSES.has(response.status)
      ) {
        const envelope = await peekErrorEnvelope(response);
        const remaining = conflictDeadline - Date.now();
        if (
          envelope.code === conflict.code &&
          remaining > 0 &&
          conflictRetries < MAX_CONFLICT_RETRIES
        ) {
          conflictRetries += 1;
          await sleep(
            Math.min(remaining, 1000 + conflictRetries * 200),
            options?.signal ?? null,
          );
          continue;
        }
      }

      if (canRetry && RETRYABLE_STATUSES.has(response.status) && attempt < MAX_RETRIES) {
        await sleep(backoffDelayMs(attempt, response), options?.signal ?? null);
        continue;
      }

      if (!response.ok) throw await errorFromResponse(response);
      return response;
    }
  };

  return {
    baseUrl: config.baseUrl,
    async request<T = unknown>(path: string, options?: RequestOptions) {
      const response = await execute(path, options);
      const body = await parseBody<T>(response, options?.responseType ?? "json");
      return { status: response.status, headers: response.headers, body };
    },
    async raw(path: string, options?: RequestOptions) {
      return execute(path, options);
    },
  };
}

// --- helpers -----------------------------------------------------------------

async function resolveToken(
  provider: ApiClientConfig["tokenProvider"],
): Promise<string | null | undefined> {
  return provider ? await provider() : undefined;
}

function hasHeader(headers: Record<string, string>, name: string): boolean {
  const key = name.toLowerCase();
  return Object.keys(headers).some((candidate) => candidate.toLowerCase() === key);
}

function joinUrl(baseUrl: string, path: string): string {
  if (!path) return baseUrl.replace(/\/+$/, "");
  return `${baseUrl.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

function buildQuery(query?: Record<string, QueryValue>): string {
  if (!query) return "";
  const parts: string[] = [];
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined) continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

function defaultRequestIdFactory(): string {
  const crypto = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (crypto?.randomUUID) return crypto.randomUUID();
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function backoffDelayMs(attempt: number, response?: ResponseLike): number {
  const base = Math.min(DEFAULT_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS);
  const retryAfter = response ? retryAfterMs(response) : undefined;
  if (retryAfter !== undefined) {
    return Math.min(Math.max(retryAfter, base), RETRY_AFTER_CAP_MS);
  }
  return Math.round(base * (0.5 + Math.random()));
}

function retryAfterMs(response: ResponseLike): number | undefined {
  const raw = response.headers.get("retry-after");
  if (!raw) return undefined;
  const seconds = Number.parseInt(raw, 10);
  if (!Number.isFinite(seconds) || seconds < 0) return undefined;
  return seconds * 1000;
}

function isAbortLike(error: unknown, signal: AbortSignalLike | null): boolean {
  if (signal?.aborted) return true;
  return (
    error instanceof Error && (error.name === "AbortError" || error.name === "TimeoutError")
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "network request failed";
}

/** Read the error envelope of a 409 without losing the response stream. */
async function peekErrorEnvelope(response: ResponseLike) {
  try {
    const clone = response.clone?.();
    if (!clone) return parseErrorBody(await response.json());
    return parseErrorBody(await clone.json());
  } catch {
    return {};
  }
}

async function parseBody<T>(
  response: ResponseLike,
  kind: "json" | "text" | "none",
): Promise<T> {
  if (kind === "none" || response.status === 204 || response.status === 205) {
    return undefined as T;
  }
  if (kind === "text") return (await response.text()) as T;
  return (await response.json()) as T;
}

interface TimedSignal {
  signal: AbortSignalLike | null;
  cancel(): void;
}

/**
 * Per-attempt timeout wrapper: relays the caller's signal into a fresh
 * controller and aborts it after `timeoutMs`. Returns the caller's signal
 * untouched when no timeout applies.
 */
function withTimeout(signal: AbortSignalLike | null, timeoutMs?: number): TimedSignal {
  if (!timeoutMs) return { signal, cancel: () => {} };
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort(new Error("request_timeout"));
  }, timeoutMs);
  let relay: (() => void) | null = null;
  if (signal) {
    if (signal.aborted) {
      clearTimeout(timer);
      controller.abort(signal.reason);
    } else {
      relay = () => controller.abort(signal.reason);
      signal.addEventListener("abort", relay, { once: true });
    }
  }
  return {
    signal: controller.signal,
    cancel: () => {
      clearTimeout(timer);
      if (relay && signal) signal.removeEventListener("abort", relay);
    },
  };
}

function defaultSleep(ms: number, signal?: AbortSignalLike | null): Promise<void> {
  if (ms <= 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason ?? new Error("aborted"));
    };
    const timer = setTimeout(() => {
      if (signal) signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

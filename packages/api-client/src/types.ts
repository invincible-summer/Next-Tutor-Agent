/**
 * Structural HTTP types for the platform-neutral client (docs/architecture/client-platform.md).
 *
 * The package compiles without the DOM lib: instead of referencing global
 * `fetch`/`Response`/`FormData` types it declares the minimal shapes it
 * consumes. Real browser `fetch`, Node's undici fetch and Expo's `expo/fetch`
 * are all assignable to `FetchLike`; tests inject fakes.
 */

// --- fetch ------------------------------------------------------------------

export interface HeadersLike {
  get(name: string): string | null;
  has(name: string): boolean;
  set(name: string, value: string): void;
  entries(): IterableIterator<[string, string]>;
}

export interface StreamReadResult {
  done: boolean;
  value?: Uint8Array;
}

export interface StreamReaderLike {
  read(): Promise<StreamReadResult>;
  releaseLock?(): void;
  cancel?(): Promise<void>;
}

export interface ReadableStreamLike {
  getReader(): StreamReaderLike;
}

export interface ResponseLike {
  readonly ok: boolean;
  readonly status: number;
  readonly headers: HeadersLike;
  readonly body?: ReadableStreamLike | null;
  json(): Promise<unknown>;
  text(): Promise<string>;
  /** Binary bodies (`responseType: "bytes"`); present on browser/undici/expo fetch. */
  arrayBuffer?(): Promise<ArrayBuffer>;
  clone?(): ResponseLike;
}

/**
 * Multipart body. Values are opaque payloads (browser `File`/`Blob`, RN
 * `{uri, name, type}`); only `append` is required so both platform FormData
 * implementations satisfy the shape structurally.
 */
export interface FormDataLike {
  append(name: string, value: unknown, filename?: string): void;
}

export type FetchBodyLike = string | FormDataLike | Uint8Array | ArrayBuffer | null;

export interface FetchInitLike {
  method?: string;
  /** Plain string map only — keeps every fetch implementation assignable. */
  headers?: Record<string, string>;
  body?: FetchBodyLike;
  signal?: AbortSignalLike | null;
}

export type FetchLike = (input: string, init?: FetchInitLike) => Promise<ResponseLike>;

// --- abort ------------------------------------------------------------------

export interface AbortSignalLike {
  readonly aborted: boolean;
  readonly reason?: unknown;
  addEventListener(type: "abort", listener: () => void, options?: { once?: boolean }): void;
  removeEventListener(type: "abort", listener: () => void): void;
}

// --- client metadata headers --------------------------------------------

export interface ClientMetadata {
  /** `web` | `ios` | `android` — stable platform identifiers. */
  platform: string;
  /** App semantic version, e.g. `2.2.2`. */
  version?: string;
  /** Store/build number, e.g. expo `appVersion` build. */
  build?: string;
}

// --- request options ----------------------------------------------------------

export type QueryValue = string | number | boolean | undefined;

export interface RequestOptions {
  method?: string | undefined;
  headers?: Record<string, string> | undefined;
  query?: Record<string, QueryValue> | undefined;
  /** JSON body — serialized and `Content-Type: application/json` set. */
  json?: unknown;
  /** Raw body for non-JSON requests (multipart/text). */
  body?: FetchBodyLike | undefined;
  signal?: AbortSignalLike | null | undefined;
  /**
   * Sets `Idempotency-Key`. Together with `retryWrites: true` this opts the
   * call into bounded write retries (only idempotent endpoints
   * carrying the key may retry).
   */
  idempotencyKey?: string | undefined;
  retryWrites?: boolean | undefined;
  /**
   * Bounded re-poll when the server answers 409 with the given domain error
   * code (e.g. `evaluation_pending`). Deadline caps total wait; the final 409
   * response is surfaced to the caller when it expires.
   */
  waitForConflict?: { code: string; deadlineMs?: number | undefined } | undefined;
  /** Per-attempt timeout (an abort wrapper is created when set). */
  timeoutMs?: number | undefined;
  /**
   * Body interpretation; `none` skips parsing (204-style endpoints), `bytes`
   * resolves `arrayBuffer()` (audio/image/zip downloads).
   */
  responseType?: ResponseBodyKind | undefined;
}

export type ResponseBodyKind = "json" | "text" | "bytes" | "none";

// --- transport ---------------------------------------------------------------

export interface RequestResult<T> {
  status: number;
  headers: HeadersLike;
  body: T;
}

/** What domain modules consume: a configured request pipeline + origin. */
export interface Transport {
  readonly baseUrl: string;
  request<T = unknown>(path: string, options?: RequestOptions): Promise<RequestResult<T>>;
  /** Full pipeline returning the raw response (SSE streams, binary bodies). */
  raw(path: string, options?: RequestOptions): Promise<ResponseLike>;
}

// --- SSE ---------------------------------------------------------------------

export interface SseFrame {
  id?: string | undefined;
  event?: string | undefined;
  /** Multi-line `data:` payloads joined with `\n`. */
  data: string;
}

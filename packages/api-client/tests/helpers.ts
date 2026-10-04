/** Shared fakes for api-client node:test suites. */
import type {
  FetchInitLike,
  FetchLike,
  FormDataLike,
  HeadersLike,
  ResponseLike,
  StreamReaderLike,
} from "../src/index.ts";

export class FakeHeaders implements HeadersLike {
  private readonly map = new Map<string, string>();

  constructor(init?: Record<string, string>) {
    if (init) {
      for (const [key, value] of Object.entries(init)) this.map.set(key.toLowerCase(), value);
    }
  }

  get(name: string): string | null {
    return this.map.get(name.toLowerCase()) ?? null;
  }

  has(name: string): boolean {
    return this.map.has(name.toLowerCase());
  }

  set(name: string, value: string): void {
    this.map.set(name.toLowerCase(), value);
  }

  *entries(): IterableIterator<[string, string]> {
    yield* this.map.entries();
  }
}

export function jsonResponse(
  status: number,
  body: unknown,
  headers?: Record<string, string>,
): ResponseLike {
  const text = JSON.stringify(body);
  const build = (): ResponseLike => ({
    ok: status >= 200 && status < 300,
    status,
    headers: new FakeHeaders(headers),
    body: null,
    json: () => Promise.resolve(JSON.parse(text)),
    text: () => Promise.resolve(text),
    clone: () => build(),
  });
  return build();
}

export interface RecordedCall {
  url: string;
  init: FetchInitLike;
}

/**
 * FIFO scripted fetch: each call consumes the next step (a response, or an
 * Error to reject with). The last step repeats once the script is exhausted.
 */
export function scriptedFetch(steps: Array<ResponseLike | Error>): {
  fetch: FetchLike;
  calls: RecordedCall[];
} {
  if (steps.length === 0) throw new Error("scriptedFetch needs at least one step");
  const calls: RecordedCall[] = [];
  let index = 0;
  const fetch: FetchLike = async (url, init) => {
    calls.push({ url, init: init ?? {} });
    const step = steps[Math.min(index, steps.length - 1)]!;
    index += 1;
    if (step instanceof Error) throw step;
    return step;
  };
  return { fetch, calls };
}

export const encoder = new TextEncoder();

export function bytes(text: string): Uint8Array {
  return encoder.encode(text);
}

/** Binary response fake: `arrayBuffer()` returns an independent copy. */
export function binaryResponse(
  status: number,
  data: Uint8Array | string,
  headers?: Record<string, string>,
): ResponseLike {
  const view = typeof data === "string" ? bytes(data) : data;
  const build = (): ResponseLike => ({
    ok: status >= 200 && status < 300,
    status,
    headers: new FakeHeaders(headers),
    body: null,
    json: () => Promise.reject(new Error("binary response has no json body")),
    text: () => Promise.resolve(""),
    arrayBuffer: async () => view.slice().buffer as ArrayBuffer,
    clone: () => build(),
  });
  return build();
}

/** Plain text response fake (HTML frames, markdown bodies). */
export function textResponse(
  status: number,
  body: string,
  headers?: Record<string, string>,
): ResponseLike {
  const build = (): ResponseLike => ({
    ok: status >= 200 && status < 300,
    status,
    headers: new FakeHeaders(headers),
    body: null,
    json: () => Promise.reject(new Error("text response has no json body")),
    text: () => Promise.resolve(body),
    clone: () => build(),
  });
  return build();
}

export interface RecordedFormEntry {
  name: string;
  value: unknown;
  filename?: string | undefined;
}

/** FormData recorder: captures appends instead of serializing them. */
export class RecordingForm implements FormDataLike {
  readonly entries: RecordedFormEntry[] = [];
  append(name: string, value: unknown, filename?: string): void {
    this.entries.push({ name, value, filename });
  }
}

/** Split a UTF-8 byte payload into chunks of the given sizes (rest = tail). */
export function chunked(text: string, sizes: number[]): Uint8Array[] {
  const all = bytes(text);
  const chunks: Uint8Array[] = [];
  let offset = 0;
  for (const size of sizes) {
    if (offset >= all.length) break;
    chunks.push(all.slice(offset, offset + size));
    offset += size;
  }
  if (offset < all.length) chunks.push(all.slice(offset));
  return chunks;
}

export function sseResponse(chunks: Uint8Array[], status = 200): ResponseLike {
  const queue = [...chunks];
  const reader: StreamReaderLike = {
    read: async () => {
      const value = queue.shift();
      return value ? { done: false, value } : { done: true };
    },
    releaseLock: () => {},
  };
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new FakeHeaders({ "content-type": "text/event-stream" }),
    body: { getReader: () => reader },
    json: () => Promise.reject(new Error("sse response has no json body")),
    text: () => Promise.resolve(""),
  };
}

/** Fetch that never resolves; rejects with AbortError once the signal fires. */
export function hangingFetch(name = "AbortError"): FetchLike {
  return (_url, init) =>
    new Promise((_resolve, reject) => {
      init?.signal?.addEventListener(
        "abort",
        () => {
          const error = new Error("aborted");
          error.name = name;
          reject(error);
        },
        { once: true },
      );
    });
}

export const noSleep = async (): Promise<void> => {};

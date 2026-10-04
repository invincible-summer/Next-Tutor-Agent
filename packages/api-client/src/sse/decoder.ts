/**
 * SSE frame decoder (docs/architecture/client-platform.md): generalizes the per-page parsers that used
 * to live in `api-classroom.ts` (frame-based) and `api.ts` chatStream
 * (line-based). Handles chunk boundaries at any byte position (UTF-8
 * multibyte included, via streaming TextDecoder), multi-line `data:` payloads
 * joined with `\n`, `id:`/`event:` fields, heartbeat comment lines and a
 * final frame not terminated by a blank line.
 *
 * The reader lock is always released; aborting the supplied signal (or the
 * fetch signal owning the stream) ends iteration promptly.
 */
import type { AbortSignalLike, ReadableStreamLike, SseFrame } from "../types.ts";

interface TextDecoderLike {
  decode(source?: Uint8Array, options?: { stream?: boolean }): string;
}

function getTextDecoder(): TextDecoderLike {
  const ctor = (globalThis as { TextDecoder?: new () => TextDecoderLike }).TextDecoder;
  if (!ctor) throw new Error("TextDecoder is unavailable in this runtime");
  return new ctor();
}

export interface SseReadOptions {
  signal?: AbortSignalLike | null | undefined;
}

/** Iterate SSE frames from a byte stream. Frames without `data:` are skipped. */
export async function* readSseFrames(
  body: ReadableStreamLike,
  options?: SseReadOptions,
): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = getTextDecoder();
  let buffer = "";
  try {
    while (true) {
      if (options?.signal?.aborted) return;
      const { done, value } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      if (done) break;
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const frame = parseFrame(raw);
        if (frame) yield frame;
      }
    }
    // Flush the decoder and accept a trailing frame the server closed
    // without a final blank line.
    buffer += decoder.decode();
    const tail = parseFrame(buffer);
    if (tail) yield tail;
  } finally {
    reader.releaseLock?.();
  }
}

/** Parse one raw frame (lines already split by `\n`). Comments are ignored. */
export function parseFrame(raw: string): SseFrame | null {
  const dataLines: string[] = [];
  let id: string | undefined;
  let event: string | undefined;
  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) continue; // heartbeat comment
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return null;
  return { id, event, data: dataLines.join("\n") };
}

/** Convenience: frames with `data` parsed as JSON (malformed payloads skipped). */
export async function* readSseJson(
  body: ReadableStreamLike,
  options?: SseReadOptions,
): AsyncGenerator<{ id?: string | undefined; event?: string | undefined; data: unknown }> {
  for await (const frame of readSseFrames(body, options)) {
    try {
      yield { id: frame.id, event: frame.event, data: JSON.parse(frame.data) };
    } catch {
      /* skip malformed JSON payloads */
    }
  }
}

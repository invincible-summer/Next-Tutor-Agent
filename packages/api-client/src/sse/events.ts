/**
 * Chat SSE event mapping (docs/architecture/client-platform.md): the server stamps every event body
 * with its own `type` and repeats it on the `event:` line; the frame's event
 * name wins, defaulting to `message` for bare data frames. The discriminated
 * union itself lives in `@next-tutor/contracts` (`ChatStreamEvent`).
 */
import type { ChatStreamEvent } from "@next-tutor/contracts";
import type { SseFrame } from "../types.ts";
import { readSseFrames } from "./decoder.ts";

/** Convert one SSE frame into a chat event; null for malformed JSON payloads. */
export function chatEventFromFrame(frame: SseFrame): ChatStreamEvent | null {
  let payload: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(frame.data);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    payload = parsed as Record<string, unknown>;
  } catch {
    return null;
  }
  const type = frame.event
    ?? (typeof payload.type === "string" && payload.type ? payload.type : "message");
  return { ...payload, type } as ChatStreamEvent;
}

/** Classify a chat event as terminal (`done`/`error` ends the stream). */
export function isTerminalChatEvent(event: ChatStreamEvent): boolean {
  return event.type === "done" || event.type === "error";
}

/**
 * Chat domain: the authenticated SSE stream plus session projections.
 * Streaming goes through `transport.raw` (never `EventSource` — the
 * Authorization header must travel with the request) and the shared SSE
 * decoder; the event union (`ChatStreamEvent`) is imported type-only from
 * `@next-tutor/contracts`.
 *
 * Session payloads are large and still Web-typed; methods are generic so the
 * platform layer re-exports them with its concrete types without this
 * package duplicating those interfaces.
 */
import type { ChatStreamEvent } from "@next-tutor/contracts";
import type { AbortSignalLike, Transport } from "./types.ts";
import { errorFromResponse } from "./errors.ts";
import { readSseFrames } from "./sse/decoder.ts";
import { chatEventFromFrame } from "./sse/events.ts";

export interface ChatStreamRequest {
  message: string;
  session_id?: string | null;
  workspace_id?: string | null;
  grade?: string;
  lang?: string;
  output_language?: string | null;
  attachments?: unknown[];
  classroom_ref?: unknown;
  public_textbook_ids?: string[];
}

export interface ChatClient {
  /** POST /chat/stream — yields server events until the stream ends/aborts. */
  stream(
    body: ChatStreamRequest,
    signal?: AbortSignalLike | null,
  ): AsyncGenerator<ChatStreamEvent>;
  listSessions<T = { sessions: unknown[] }>(): Promise<T>;
  loadSession<T = unknown>(id: string, tail?: number): Promise<T>;
  deleteSession<T = unknown>(id: string, forgetPromptMemory?: boolean): Promise<T>;
  renameSession<T = unknown>(id: string, title: string): Promise<T>;
  patchSession<T = unknown>(id: string, patch: { title?: string; grade?: string }): Promise<T>;
}

export function createChatClient(transport: Transport): ChatClient {
  return {
    async *stream(body, signal) {
      const response = await transport.raw("/chat/stream", {
        method: "POST",
        json: body,
        headers: { Accept: "text/event-stream" },
        signal: signal ?? null,
      });
      if (!response.ok || !response.body) throw await errorFromResponse(response);
      for await (const frame of readSseFrames(response.body, { signal })) {
        const event = chatEventFromFrame(frame);
        if (event) yield event;
      }
    },
    listSessions: <T>() =>
      transport.request<T>("/chat/sessions").then((result) => result.body),
    loadSession: <T>(id: string, tail?: number) =>
      transport
        .request<T>(`/chat/sessions/${encodeURIComponent(id)}`, {
          query: tail ? { tail } : undefined,
        })
        .then((result) => result.body),
    deleteSession: <T>(id: string, forgetPromptMemory = false) =>
      transport
        .request<T>(`/chat/sessions/${encodeURIComponent(id)}`, {
          method: "DELETE",
          query: { forget_prompt_memory: forgetPromptMemory },
        })
        .then((result) => result.body),
    renameSession: <T>(id: string, title: string) =>
      transport
        .request<T>(`/chat/sessions/${encodeURIComponent(id)}`, {
          method: "PATCH",
          json: { title },
        })
        .then((result) => result.body),
    patchSession: <T>(id: string, patch: { title?: string; grade?: string }) =>
      transport
        .request<T>(`/chat/sessions/${encodeURIComponent(id)}`, {
          method: "PATCH",
          json: patch,
        })
        .then((result) => result.body),
  };
}

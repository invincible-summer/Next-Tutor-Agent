/**
 * Chat streaming protocol (docs/architecture/client-platform.md): the discriminated union of SSE
 * events emitted by `POST /api/v1/chat/stream`. The server `type` field is
 * the SSE `event:` name; every event body carries its own `type` as well.
 *
 * Source of truth: services/api/app/api/v1/chat.py + app/agents/chat_agent.py.
 * Keep in sync when the server adds events (a server-side Pydantic event
 * model may replace this hand-written union later — see scripts/contracts/README.md).
 */

export interface ChatAnswerEvent {
  type: "answer";
  delta: string;
}

export interface ChatStepEvent {
  type: "step";
  step: string;
}

export interface ChatToolStartEvent {
  type: "tool_start";
  tool: string;
}

export interface ChatToolResultEvent {
  type: "tool_result";
  tool: string;
}

export interface ChatToolWarningEvent {
  type: "tool_warning";
  message: string;
}

export interface ChatToolProgressEvent {
  /** Synthesized by the stream route from progress messages. */
  type: "tool_progress";
  [key: string]: unknown;
}

export interface ChatRetryEvent {
  type: "retry";
  [key: string]: unknown;
}

export interface ChatThinkingEvent {
  type: "thinking";
  [key: string]: unknown;
}

export interface ChatHeartbeatEvent {
  type: "heartbeat";
}

export interface ChatDoneEvent {
  /** Terminal; stamped with session/trace identifiers by the route. */
  type: "done";
  session_id?: string;
  trace_id?: string;
  [key: string]: unknown;
}

export interface ChatHistorySavedEvent {
  type: "history_saved";
  session_id?: string;
  [key: string]: unknown;
}

export interface ChatErrorEvent {
  type: "error";
  code?: string;
  message?: string;
}

export type ChatStreamEvent =
  | ChatAnswerEvent
  | ChatStepEvent
  | ChatToolStartEvent
  | ChatToolResultEvent
  | ChatToolWarningEvent
  | ChatToolProgressEvent
  | ChatRetryEvent
  | ChatThinkingEvent
  | ChatHeartbeatEvent
  | ChatDoneEvent
  | ChatHistorySavedEvent
  | ChatErrorEvent;

/** SSE event names on the wire (the `event:` line). */
export type ChatStreamEventName = ChatStreamEvent["type"];

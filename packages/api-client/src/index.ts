/**
 * Platform-neutral REST/SSE client shared by the web and mobile apps.
 * Everything environment-specific (fetch implementation, token storage,
 * request metadata) is injected through `createApiClient`.
 *
 * Contracts (DTO types, event unions) stay in `@next-tutor/contracts`;
 * imports of that package here are type-only so Node's type stripping never
 * resolves it at runtime.
 */
export const API_CLIENT_VERSION = "0.2.0";

export type * from "@next-tutor/contracts";

export * from "./types.ts";
export * from "./errors.ts";
export { createTransport, type ApiClientConfig } from "./transport.ts";
export { createApiClient, type ApiClient } from "./client.ts";
export type {
  AuthClient,
  AuthResponse,
  AuthStatusResponse,
  AuthUserLike,
  LoginPayload,
  RegisterPayload,
} from "./auth.ts";
export type { ChatClient, ChatStreamRequest } from "./chat.ts";
export type { CreateWorkspaceResponse, WorkspaceClient, WorkspacePatch } from "./workspace.ts";
export type { IllustrationCallOptions, IllustrationClient, QuizIllustrationView } from "./illustration.ts";
export { ILLUSTRATION_POST_TIMEOUT_MS, toIllustrationError } from "./illustration.ts";
export type {
  IllustrationMode as ScenarioIllustrationMode,
  JobPollEvent,
  PollJobOptions,
  SubmitTurnPayload,
  ToolIllustrationClient,
} from "./tools/illustration.ts";
export { readSseFrames, readSseJson, parseFrame, type SseReadOptions } from "./sse/decoder.ts";
export { chatEventFromFrame, isTerminalChatEvent } from "./sse/events.ts";

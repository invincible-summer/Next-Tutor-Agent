/**
 * Platform-neutral REST/SSE client shared by the web and mobile apps.
 * Everything environment-specific (fetch implementation, token storage,
 * request metadata) is injected through `createApiClient`.
 *
 * Contracts (DTO types, event unions) stay in `@next-tutor/contracts`;
 * imports of that package here are type-only so Node's type stripping never
 * resolves it at runtime.
 */
export const API_CLIENT_VERSION = "0.3.0";

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
export type { GuestClient, GuestSession, GuestTextbookItem } from "./guest.ts";
export type { IllustrationCallOptions, IllustrationClient, QuizIllustrationView } from "./illustration.ts";
export { ILLUSTRATION_POST_TIMEOUT_MS, toIllustrationError } from "./illustration.ts";
export type {
  IllustrationMode as ScenarioIllustrationMode,
  JobPollEvent,
  PollJobOptions,
  SubmitTurnPayload,
  ToolIllustrationClient,
} from "./tools/illustration.ts";
export type {
  AssessmentClient,
  AssessmentStartPayload,
  AssessmentStartResponse,
  CatReport,
  ConceptRef,
  QuestionPublic,
  SubmissionAck,
  SubmissionPayload,
  TaskResult,
} from "./assessment.ts";
export type { CapabilityEntry, CapabilitiesClient, ProductCapabilities } from "./capabilities.ts";
export type {
  ClassroomClient,
  ClassroomJobEvent,
  JobEventsOptions,
  RunView,
} from "./classroom.ts";
export type {
  DiagramAssetItem,
  DiagramAssetQuery,
  DiagramMaterial,
  DiagramsClient,
  MaterialIllustration,
  MaterialInput,
  MaterialParameterization,
  MaterialScope,
  MaterialTemplate,
} from "./diagrams.ts";
export { STATIC_PARAMETERIZATION } from "./diagrams.ts";
export type {
  KnowledgeClient,
  KnowledgeConceptResponse,
  KnowledgeGraphQuery,
  KnowledgeGraphResponse,
  KnowledgeGraphView,
  KnowledgeNode,
} from "./knowledge.ts";
export type {
  DailyTask,
  GoalPayload,
  HabitStats,
  LearningClient,
  LearningPlanSummary,
  LaunchResponse,
  ReviewItem,
  SubTask,
  TaskCreatePayload,
  WeeklyPlan,
  WeekTask,
} from "./learning.ts";
export type {
  LibraryClient,
  LibraryFile,
  LibraryFolder,
  LibraryTree,
  LibraryUploadOutcome,
  TextbookClient,
  TextbookGraphPolicy,
  TextbookListItem,
  TextbookPatch,
  TextbookUploadFields,
  TextbookUploadOutcome,
} from "./library.ts";
export type {
  AgentHistory,
  NotesAgentMode,
  NotesChatPayload,
  NotesClient,
  NotesGeneratePayload,
  NotesStreamEvent,
  NoteDetail,
  NoteFolder,
  NoteRevisionMeta,
  NoteSavePayload,
  NoteSummary,
  VaultSnapshot,
} from "./notes.ts";
export { VAULT_AGENT_KEY } from "./notes.ts";
export type {
  SpeechCapabilities,
  SynthesisAudio,
  SynthesisInput,
  TranscriptionResult,
  VoiceAudioFile,
  VoiceCallOptions,
  VoiceClient,
} from "./voice.ts";
export { readSseFrames, readSseJson, parseFrame, type SseReadOptions } from "./sse/decoder.ts";
export { chatEventFromFrame, isTerminalChatEvent } from "./sse/events.ts";

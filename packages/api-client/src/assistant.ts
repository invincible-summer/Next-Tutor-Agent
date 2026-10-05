/**
 * Site-assistant domain (mirrors `app/api/v1/assistant.py`): capabilities and
 * guide, assistant conversations with turn submit/cancel, the turn SSE event
 * stream, action preview/approve/execute/ack/undo, site-wide entity search,
 * handoff drafts, multi-step workflows, proactive subscriptions /
 * notifications / reports, assistant preferences and read-aloud audio jobs.
 *
 * Conversation/action DTOs and the SSE event union are imported type-only
 * from `@next-tutor/contracts/assistant` (single source of truth); payloads
 * the web client keeps loose (search, workflows, subscriptions, preferences)
 * are declared locally with extension indexes.
 *
 * Error model: the backend answers a flat `{error: {code, message,
 * retryable, request_id}}` envelope — the transport surfaces it as
 * `ApiError.code` (`conversation_not_found`, `revision_conflict`,
 * `rate_limited`, `capability_disabled`, …); 401 maps to
 * `UnauthorizedError`, 409 to `ConflictError`.
 *
 * Idempotency: this domain carries caller-minted `client_request_id` /
 * `client_message_id` fields in the JSON bodies (not `Idempotency-Key`
 * headers); conversation deletion passes the expected revision via the
 * `If-Match` header.
 *
 * `streamTurnEvents` opens a single SSE connection (`GET
 * /assistant/turns/{id}/events?after_seq=`); reconnect with backoff stays
 * with the caller (Web re-subscribes with 1/2/4s delays and a moving
 * `after_seq` cursor). The generator ends after `turn_done`.
 *
 * Intentionally not exposed (no caller in the web client): POST
 * /assistant/workflows (workflows originate from turn actions), GET
 * /assistant/workflows/{id}/events (the web polls workflow views instead),
 * GET /assistant/voice/capabilities and POST /assistant/voice/preview.
 */
import type {
  AbortSignalLike,
  RequestOptions,
  SseFrame,
  Transport,
} from "./types.ts";
import { errorFromResponse } from "./errors.ts";
import { readSseFrames } from "./sse/decoder.ts";
import type {
  ActionAckRequest,
  ActionApproveRequest,
  ActionApproveResponse,
  ActionExecuteRequest,
  ActionExecutionResponse,
  ActionPreview,
  ActionUndoRequest,
  ActionUndoResponse,
  AssistantCapabilities,
  AssistantDraft,
  AssistantSseEvent,
  AssistantTurnRequest,
  ConversationCreated,
  ConversationDetailResponse,
  ConversationListResponse,
  GuideRequest,
  GuideResponse,
  TurnAcceptedResponse,
  TurnCancelResponse,
  TurnSnapshot,
} from "@next-tutor/contracts/assistant";

// --- site search (§20.4; caller-scope only) -----------------------------------

export interface SiteSearchItem {
  entity_kind: string;
  entity_id: string;
  title: string;
  workspace_id?: string | null;
  subtitle?: string | null;
  updated_at: number;
  match_kind: string;
  snippet?: string | null;
  target?: Record<string, unknown> | null;
}

export interface SiteSearchResponse {
  items: SiteSearchItem[];
  total: number;
  offset: number;
  limit: number;
  complete: boolean;
}

export interface AssistantSearchParams {
  q: string;
  kinds?: string[];
  workspace_id?: string;
  offset?: number;
  limit?: number;
  include_content?: boolean;
}

// --- workflows (C01/C03) --------------------------------------------------------

export interface WorkflowStepView {
  step_id: string;
  title: string;
  kind: "read" | "prepare" | "write" | "wait_job" | "handoff";
  depends_on: string[];
  state: string;
  operation: string;
  action_id?: string | null;
  domain_job?: Record<string, unknown> | null;
  output_ref?: Record<string, unknown> | null;
  error?: { code: string; message: string } | null;
}

export interface AssistantWorkflowView {
  workflow_id: string;
  conversation_id: string;
  revision: number;
  template: string;
  objective: string;
  state: string;
  steps: WorkflowStepView[];
  approved_plan_hash?: string | null;
  created_at: string;
  updated_at: string;
  cancel_requested?: boolean;
  result_targets?: Array<Record<string, unknown>>;
}

export interface WorkflowPlanPreview {
  workflow_id: string;
  template: string;
  objective: string;
  state: string;
  revision: number;
  steps: Array<Pick<WorkflowStepView, "step_id" | "title" | "kind"
    | "depends_on" | "operation">>;
  plan: {
    objective: string;
    scope?: Record<string, unknown>;
    will_create_or_modify: Array<{
      step_id: string;
      operation: string;
      input?: Record<string, unknown>;
    }>;
    user_involvement: Array<{ step_id: string; title: string }>;
    cancellable: boolean;
  };
  user_touchpoints: string[];
  plan_hash: string;
}

export interface WorkflowListResponse {
  items: AssistantWorkflowView[];
  total: number;
}

export interface AssistantWorkflowDetail {
  workflow: AssistantWorkflowView;
  preview: WorkflowPlanPreview;
}

export interface WorkflowApprovePayload {
  expected_revision: number;
  plan_hash: string;
  approved_step_ids: string[];
}

export interface WorkflowStartPayload {
  client_request_id: string;
  expected_revision: number;
}

export interface WorkflowStepRetryPayload {
  expected_revision: number;
  client_request_id: string;
}

export interface WorkflowResumePayload {
  expected_revision: number;
  choice_id?: string;
  approval_id?: string;
}

// --- proactive services: subscriptions / notifications / reports (C04/C05) ----

export interface AssistantSubscription {
  subscription_id: string;
  revision: number;
  kind: "weekly_brief" | "daily_tasks" | "due_reviews"
    | "unfinished_course" | string;
  enabled: boolean;
  timezone: string;
  local_time: string;
  weekdays: number[];
  scope: Record<string, unknown>;
  quiet_hours: { start: string; end: string };
  next_run_at: string;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssistantSubscriptionCreate {
  client_request_id: string;
  kind: string;
  timezone?: string;
  local_time?: string;
  weekdays?: number[];
  scope?: Record<string, unknown>;
}

export interface AssistantSubscriptionPatch {
  expected_revision: number;
  enabled?: boolean;
  timezone?: string;
  local_time?: string;
  weekdays?: number[];
  scope?: Record<string, unknown>;
  quiet_hours?: Record<string, string>;
}

export interface AssistantNotification {
  notification_id: string;
  kind: "subscription" | "workflow_result" | "action_attention" | string;
  title: string;
  summary: string;
  created_at: string;
  read_at: string | null;
  dismissed_at: string | null;
  expires_at: string;
  report_id: string | null;
  workflow_id: string | null;
  target: Record<string, unknown> | null;
  source_ids: string[];
}

export interface AssistantReport {
  report_id: string;
  kind?: string;
  window?: { start_at?: string; end_at?: string; timezone?: string;
             label?: string };
  learning_report?: Record<string, unknown>;
  generated_at?: string;
  complete?: boolean;
  expired?: boolean;
}

// --- preferences (B09/C05) ------------------------------------------------------

export interface AssistantPreferencesPayload {
  response_length?: "short" | "standard" | "detailed";
  tone?: "neutral" | "encouraging";
  default_scope?: "follow_page" | "all_workspaces";
  proactive_enabled?: boolean;
  voice_input_mode?: "hold" | "toggle";
  send_after_recording?: boolean;
  auto_read?: boolean;
  voice_policy?: "auto" | "cloud" | "local" | "silent";
  voice_id?: string | null;
  allow_local_fallback?: boolean;
  playback_rate?: 0.75 | 1 | 1.25 | 1.5;
  volume?: number;
  base_revision?: number;
  revision?: number;
  [key: string]: unknown;
}

// --- read-aloud audio jobs (B11) -------------------------------------------------

export interface AssistantAudioJobRequest {
  message_id: string;
  block_ids?: string[];
  policy?: "auto" | "cloud" | "local" | "silent";
  voice_id?: string;
  language?: "zh" | "en";
  allow_local_fallback?: boolean;
  client_request_id: string;
}

export interface AssistantAudioJob {
  job_id: string;
  state: "queued" | "running" | "ready" | "failed" | "cancelled" | (string & {});
  clips: { clip_id: string; state: string }[];
  truncated: boolean;
  [key: string]: unknown;
}

export interface AssistantClient {
  // --- capabilities & guide ---------------------------------------------------
  /** GET /assistant/capabilities — feature flags (not the product /capabilities). */
  capabilities(signal?: AbortSignalLike | null): Promise<AssistantCapabilities>;
  /** POST /assistant/guide — static product-help Q&A. */
  guide(body: GuideRequest): Promise<GuideResponse>;

  // --- conversations ------------------------------------------------------------
  /** POST /assistant/conversations — 201; `clientRequestId` dedups retries. */
  createConversation(clientRequestId: string, title?: string): Promise<ConversationCreated>;
  listConversations(offset?: number, limit?: number, signal?: AbortSignalLike | null): Promise<ConversationListResponse>;
  getConversation(conversationId: string, beforeSeq?: number, signal?: AbortSignalLike | null): Promise<ConversationDetailResponse>;
  /** DELETE — 204; `If-Match: <revision>` guards against stale deletes. */
  deleteConversation(conversationId: string, revision: number): Promise<void>;

  // --- turns ----------------------------------------------------------------------
  /** POST /assistant/conversations/{id}/turns — 202 acceptance. */
  submitTurn(conversationId: string, body: AssistantTurnRequest): Promise<TurnAcceptedResponse>;
  getTurn(turnId: string, signal?: AbortSignalLike | null): Promise<TurnSnapshot>;
  cancelTurn(turnId: string, clientRequestId: string): Promise<TurnCancelResponse>;
  /** GET /assistant/turns/{id}/events — SSE until `turn_done` (or abort). */
  streamTurnEvents(turnId: string, afterSeq?: number, signal?: AbortSignalLike | null): AsyncGenerator<AssistantSseEvent>;

  // --- actions (A10/B03) ------------------------------------------------------------
  getActionPreview(actionId: string, signal?: AbortSignalLike | null): Promise<ActionPreview>;
  approveAction(actionId: string, body: ActionApproveRequest): Promise<ActionApproveResponse>;
  undoAction(actionId: string, body: ActionUndoRequest): Promise<ActionUndoResponse>;
  getAction(actionId: string, clientInstanceId: string, signal?: AbortSignalLike | null): Promise<ActionExecutionResponse>;
  executeAction(actionId: string, body: ActionExecuteRequest): Promise<ActionExecutionResponse>;
  ackAction(actionId: string, body: ActionAckRequest): Promise<ActionExecutionResponse>;

  // --- site search ---------------------------------------------------------------------
  search(params: AssistantSearchParams, signal?: AbortSignalLike | null): Promise<SiteSearchResponse>;

  // --- handoff drafts (A13) --------------------------------------------------------------
  getDraft(draftId: string, signal?: AbortSignalLike | null): Promise<AssistantDraft>;
  consumeDraft(draftId: string, resultEntity?: Record<string, unknown> | null): Promise<AssistantDraft>;
  deleteDraft(draftId: string): Promise<void>;

  // --- workflows ----------------------------------------------------------------------------
  listWorkflows(offset?: number, limit?: number, state?: string, signal?: AbortSignalLike | null): Promise<WorkflowListResponse>;
  getWorkflow(workflowId: string, signal?: AbortSignalLike | null): Promise<AssistantWorkflowDetail>;
  approveWorkflow(workflowId: string, body: WorkflowApprovePayload): Promise<AssistantWorkflowView>;
  /** POST …/start — 202; the workflow runs in the background after acceptance. */
  startWorkflow(workflowId: string, body: WorkflowStartPayload): Promise<AssistantWorkflowView>;
  cancelWorkflow(workflowId: string): Promise<AssistantWorkflowView>;
  retryWorkflowStep(workflowId: string, stepId: string, body: WorkflowStepRetryPayload): Promise<AssistantWorkflowView>;
  resumeWorkflow(workflowId: string, body: WorkflowResumePayload): Promise<AssistantWorkflowView>;

  // --- subscriptions ---------------------------------------------------------------------------
  listSubscriptions(signal?: AbortSignalLike | null): Promise<{ items: AssistantSubscription[] }>;
  createSubscription(body: AssistantSubscriptionCreate): Promise<AssistantSubscription>;
  patchSubscription(subscriptionId: string, body: AssistantSubscriptionPatch): Promise<AssistantSubscription>;
  deleteSubscription(subscriptionId: string): Promise<void>;

  // --- notifications ------------------------------------------------------------------------------
  listNotifications(offset?: number, limit?: number, unreadOnly?: boolean, signal?: AbortSignalLike | null): Promise<{ items: AssistantNotification[]; total: number }>;
  markNotificationRead(notificationId: string): Promise<AssistantNotification>;
  dismissNotification(notificationId: string, muteEntity?: boolean): Promise<AssistantNotification>;

  // --- reports ----------------------------------------------------------------------------------------
  getReport(reportId: string, signal?: AbortSignalLike | null): Promise<AssistantReport>;
  deleteReport(reportId: string): Promise<void>;

  // --- preferences --------------------------------------------------------------------------------------
  getPreferences(signal?: AbortSignalLike | null): Promise<AssistantPreferencesPayload>;
  /** PUT — whitelist merge; `base_revision` in the patch enables conflict checks. */
  putPreferences(patch: AssistantPreferencesPayload): Promise<AssistantPreferencesPayload>;

  // --- read-aloud audio jobs ------------------------------------------------------------------------------
  /** POST /assistant/audio/jobs — 202 (200 on dedup hit); poll `getAudioJob`. */
  createAudioJob(body: AssistantAudioJobRequest): Promise<AssistantAudioJob>;
  getAudioJob(jobId: string, signal?: AbortSignalLike | null): Promise<AssistantAudioJob>;
  cancelAudioJob(jobId: string): Promise<AssistantAudioJob>;
  /** GET /assistant/audio/clips/{id}/content — audio bytes (media element on Web). */
  audioClipContent(clipId: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;
}

export function createAssistantClient(transport: Transport): AssistantClient {
  const base = "/assistant";
  const conversationBase = (conversationId: string) =>
    `${base}/conversations/${encodeURIComponent(conversationId)}`;
  const actionBase = (actionId: string) =>
    `${base}/actions/${encodeURIComponent(actionId)}`;
  const workflowBase = (workflowId: string) =>
    `${base}/workflows/${encodeURIComponent(workflowId)}`;
  const request = <T>(path: string, init: RequestOptions) =>
    transport.request<T>(path, init).then((result) => result.body);
  return {
    capabilities: (signal) =>
      request<AssistantCapabilities>(`${base}/capabilities`, { signal }),
    guide: (body) =>
      request<GuideResponse>(`${base}/guide`, { method: "POST", json: body }),

    createConversation: (clientRequestId, title) =>
      request<ConversationCreated>(`${base}/conversations`, {
        method: "POST",
        json: { client_request_id: clientRequestId, title },
      }),
    listConversations: (offset = 0, limit = 20, signal) =>
      request<ConversationListResponse>(`${base}/conversations`, {
        query: { offset, limit },
        signal,
      }),
    getConversation: (conversationId, beforeSeq, signal) =>
      request<ConversationDetailResponse>(conversationBase(conversationId), {
        query: beforeSeq ? { before_seq: beforeSeq } : undefined,
        signal,
      }),
    deleteConversation: (conversationId, revision) =>
      transport
        .request(conversationBase(conversationId), {
          method: "DELETE",
          headers: { "If-Match": String(revision) },
          responseType: "none",
        })
        .then(() => undefined),

    submitTurn: (conversationId, body) =>
      request<TurnAcceptedResponse>(`${conversationBase(conversationId)}/turns`, {
        method: "POST",
        json: body,
      }),
    getTurn: (turnId, signal) =>
      request<TurnSnapshot>(`${base}/turns/${encodeURIComponent(turnId)}`, { signal }),
    cancelTurn: (turnId, clientRequestId) =>
      request<TurnCancelResponse>(`${base}/turns/${encodeURIComponent(turnId)}/cancel`, {
        method: "POST",
        json: { client_request_id: clientRequestId },
      }),
    async *streamTurnEvents(turnId, afterSeq = 0, signal) {
      const response = await transport.raw(
        `${base}/turns/${encodeURIComponent(turnId)}/events`,
        {
          headers: { Accept: "text/event-stream" },
          query: { after_seq: afterSeq },
          signal: signal ?? null,
        },
      );
      if (!response.ok || !response.body) throw await errorFromResponse(response);
      for await (const frame of readSseFrames(response.body, { signal })) {
        const event = assistantEventFromFrame(frame);
        if (!event) continue;
        yield event;
        if (event.event === "turn_done") return;
      }
    },

    getActionPreview: (actionId, signal) =>
      request<ActionPreview>(`${actionBase(actionId)}/preview`, { signal }),
    approveAction: (actionId, body) =>
      request<ActionApproveResponse>(`${actionBase(actionId)}/approve`, {
        method: "POST",
        json: body,
      }),
    undoAction: (actionId, body) =>
      request<ActionUndoResponse>(`${actionBase(actionId)}/undo`, {
        method: "POST",
        json: body,
      }),
    getAction: (actionId, clientInstanceId, signal) =>
      request<ActionExecutionResponse>(actionBase(actionId), {
        query: { client_instance_id: clientInstanceId },
        signal,
      }),
    executeAction: (actionId, body) =>
      request<ActionExecutionResponse>(`${actionBase(actionId)}/execute`, {
        method: "POST",
        json: body,
      }),
    ackAction: (actionId, body) =>
      request<ActionExecutionResponse>(`${actionBase(actionId)}/ack`, {
        method: "POST",
        json: body,
      }),

    search: (params, signal) =>
      request<SiteSearchResponse>(`${base}/search`, {
        query: {
          q: params.q,
          kinds: params.kinds?.length ? params.kinds.join(",") : undefined,
          workspace_id: params.workspace_id || undefined,
          offset: params.offset || undefined,
          limit: params.limit || undefined,
          include_content: params.include_content ? "true" : undefined,
        },
        signal,
      }),

    getDraft: (draftId, signal) =>
      request<AssistantDraft>(`${base}/drafts/${encodeURIComponent(draftId)}`, { signal }),
    consumeDraft: (draftId, resultEntity) =>
      request<AssistantDraft>(`${base}/drafts/${encodeURIComponent(draftId)}/consume`, {
        method: "POST",
        json: { result_entity: resultEntity ?? null },
      }),
    deleteDraft: (draftId) =>
      transport
        .request(`${base}/drafts/${encodeURIComponent(draftId)}`, {
          method: "DELETE",
          responseType: "none",
        })
        .then(() => undefined),

    listWorkflows: (offset = 0, limit = 20, state, signal) =>
      request<WorkflowListResponse>(`${base}/workflows`, {
        query: { offset, limit, state: state || undefined },
        signal,
      }),
    getWorkflow: (workflowId, signal) =>
      request<AssistantWorkflowDetail>(workflowBase(workflowId), { signal }),
    approveWorkflow: (workflowId, body) =>
      request<AssistantWorkflowView>(`${workflowBase(workflowId)}/approve`, {
        method: "POST",
        json: body,
      }),
    startWorkflow: (workflowId, body) =>
      request<AssistantWorkflowView>(`${workflowBase(workflowId)}/start`, {
        method: "POST",
        json: body,
      }),
    cancelWorkflow: (workflowId) =>
      request<AssistantWorkflowView>(`${workflowBase(workflowId)}/cancel`, {
        method: "POST",
      }),
    retryWorkflowStep: (workflowId, stepId, body) =>
      request<AssistantWorkflowView>(
        `${workflowBase(workflowId)}/steps/${encodeURIComponent(stepId)}/retry`,
        { method: "POST", json: body },
      ),
    resumeWorkflow: (workflowId, body) =>
      request<AssistantWorkflowView>(`${workflowBase(workflowId)}/resume`, {
        method: "POST",
        json: body,
      }),

    listSubscriptions: (signal) =>
      request<{ items: AssistantSubscription[] }>(`${base}/subscriptions`, { signal }),
    createSubscription: (body) =>
      request<AssistantSubscription>(`${base}/subscriptions`, {
        method: "POST",
        json: body,
      }),
    patchSubscription: (subscriptionId, body) =>
      request<AssistantSubscription>(
        `${base}/subscriptions/${encodeURIComponent(subscriptionId)}`,
        { method: "PATCH", json: body },
      ),
    deleteSubscription: (subscriptionId) =>
      transport
        .request(`${base}/subscriptions/${encodeURIComponent(subscriptionId)}`, {
          method: "DELETE",
          responseType: "none",
        })
        .then(() => undefined),

    listNotifications: (offset = 0, limit = 20, unreadOnly = false, signal) =>
      request<{ items: AssistantNotification[]; total: number }>(`${base}/notifications`, {
        query: { offset, limit, unread_only: unreadOnly ? "true" : undefined },
        signal,
      }),
    markNotificationRead: (notificationId) =>
      request<AssistantNotification>(
        `${base}/notifications/${encodeURIComponent(notificationId)}/read`,
        { method: "POST" },
      ),
    dismissNotification: (notificationId, muteEntity = false) =>
      request<AssistantNotification>(
        `${base}/notifications/${encodeURIComponent(notificationId)}/dismiss`,
        { method: "POST", json: { mute_entity: muteEntity } },
      ),

    getReport: (reportId, signal) =>
      request<AssistantReport>(`${base}/reports/${encodeURIComponent(reportId)}`, { signal }),
    deleteReport: (reportId) =>
      transport
        .request(`${base}/reports/${encodeURIComponent(reportId)}`, {
          method: "DELETE",
          responseType: "none",
        })
        .then(() => undefined),

    getPreferences: (signal) =>
      request<AssistantPreferencesPayload>(`${base}/preferences`, { signal }),
    putPreferences: (patch) =>
      request<AssistantPreferencesPayload>(`${base}/preferences`, {
        method: "PUT",
        json: patch,
      }),

    createAudioJob: (body) =>
      request<AssistantAudioJob>(`${base}/audio/jobs`, { method: "POST", json: body }),
    getAudioJob: (jobId, signal) =>
      request<AssistantAudioJob>(`${base}/audio/jobs/${encodeURIComponent(jobId)}`, { signal }),
    cancelAudioJob: (jobId) =>
      request<AssistantAudioJob>(`${base}/audio/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: "POST",
      }),
    audioClipContent: (clipId, signal) =>
      transport
        .request<ArrayBuffer>(
          `${base}/audio/clips/${encodeURIComponent(clipId)}/content`,
          { responseType: "bytes", signal },
        )
        .then((result) => result.body),
  };
}

/**
 * Convert one SSE frame into an assistant event; null for malformed JSON.
 * The frame's `event:` name wins over the payload `event` field (the server
 * repeats it in both places); bare data frames fall back to the payload.
 */
function assistantEventFromFrame(frame: SseFrame): AssistantSseEvent | null {
  let payload: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(frame.data);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    payload = parsed as Record<string, unknown>;
  } catch {
    return null;
  }
  const event = frame.event
    ?? (typeof payload.event === "string" && payload.event ? payload.event : "message");
  return { ...payload, event } as AssistantSseEvent;
}

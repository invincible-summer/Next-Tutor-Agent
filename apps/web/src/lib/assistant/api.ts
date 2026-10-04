// 站内学习助手 API 客户端。
// 所有请求经 apiFetch（鉴权注入）；SSE 用 ReadableStream，
// 断线按 1/2/4 秒退避最多 3 次后交由 UI 显示「连接已中断」。
import { apiFetch } from "@/lib/api-fetch";
import { API_BASE } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useUIStore } from "@/lib/store";
import type {
  ActionAckRequest,
  ActionApproveResponse,
  ActionExecuteRequest,
  ActionExecutionResponse,
  ActionPreview,
  ActionUndoResponse,
  AssistantCapabilities,
  AssistantSseEvent,
  ConversationCreated,
  ConversationDetailResponse,
  ConversationListResponse,
  GuideRequest,
  GuideResponse,
  TurnAcceptedResponse,
  TurnCancelResponse,
  TurnSnapshot,
  AssistantTurnRequest,
} from "@next-tutor/contracts/assistant";

export class AssistantApiError extends Error {
  code: string;
  retryable: boolean;
  status: number;
  extra?: Record<string, unknown>;

  constructor(status: number, code: string, message: string,
              retryable = false, extra?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.extra = extra;
  }
}

async function parseError(res: Response): Promise<AssistantApiError> {
  let code = "internal_error";
  let message = res.statusText || t(useUIStore.getState().lang, "common.request.failed");
  let retryable = false;
  let extra: Record<string, unknown> | undefined;
  try {
    const body = await res.json();
    if (body?.error) {
      code = String(body.error.code ?? code);
      message = String(body.error.message ?? message);
      retryable = Boolean(body.error.retryable);
      extra = body.error.extra;
    }
  } catch {
    // 非 envelope 错误体按通用错误处理
  }
  return new AssistantApiError(res.status, code, message, retryable, extra);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(`${API_BASE}/assistant${path}`, init);
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function getCapabilities(): Promise<AssistantCapabilities> {
  return request<AssistantCapabilities>("/capabilities");
}

export function postGuide(body: GuideRequest): Promise<GuideResponse> {
  return request<GuideResponse>("/guide", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function createConversation(clientRequestId: string, title?: string):
    Promise<ConversationCreated> {
  return request<ConversationCreated>("/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_request_id: clientRequestId, title }),
  });
}

export function listConversations(offset = 0, limit = 20):
    Promise<ConversationListResponse> {
  return request<ConversationListResponse>(
    `/conversations?offset=${offset}&limit=${limit}`);
}

export function getConversation(cid: string, beforeSeq?: number):
    Promise<ConversationDetailResponse> {
  const q = beforeSeq ? `?before_seq=${beforeSeq}` : "";
  return request<ConversationDetailResponse>(`/conversations/${cid}${q}`);
}

export function deleteConversation(cid: string, revision: number):
    Promise<void> {
  return request<void>(`/conversations/${cid}`, {
    method: "DELETE",
    headers: { "If-Match": String(revision) },
  });
}

export function postTurn(cid: string, body: AssistantTurnRequest):
    Promise<TurnAcceptedResponse> {
  return request<TurnAcceptedResponse>(`/conversations/${cid}/turns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getTurn(turnId: string): Promise<TurnSnapshot> {
  return request<TurnSnapshot>(`/turns/${turnId}`);
}

export function cancelTurn(turnId: string, clientRequestId: string):
    Promise<TurnCancelResponse> {
  return request<TurnCancelResponse>(`/turns/${turnId}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_request_id: clientRequestId }),
  });
}

// --- 动作 execute / ack / 查询（A10） -------------------------

export function executeAssistantAction(
    actionId: string, body: ActionExecuteRequest): Promise<ActionExecutionResponse> {
  return request<ActionExecutionResponse>(`/actions/${actionId}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getAssistantAction(actionId: string, clientInstanceId: string):
    Promise<ActionExecutionResponse> {
  return request<ActionExecutionResponse>(
    `/actions/${actionId}?client_instance_id=${encodeURIComponent(clientInstanceId)}`);
}

export function ackAssistantAction(
    actionId: string, body: ActionAckRequest): Promise<ActionExecutionResponse> {
  return request<ActionExecutionResponse>(`/actions/${actionId}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// --- 预览与审批（B03 前端收口） ---------------------------

export function getAssistantActionPreview(actionId: string):
    Promise<ActionPreview> {
  return request<ActionPreview>(
    `/actions/${encodeURIComponent(actionId)}/preview`);
}

export function approveAssistantAction(actionId: string, body: {
  preview_id: string;
  parameter_hash: string;
  decision: "approve" | "reject";
}): Promise<ActionApproveResponse> {
  return request<ActionApproveResponse>(`/actions/${actionId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// --- 撤销 -----------------------------------------

export function undoAssistantAction(actionId: string, body: {
  client_request_id: string;
  expected_result_revision: string;
}): Promise<ActionUndoResponse> {
  return request<ActionUndoResponse>(`/actions/${actionId}/undo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// --- 全站实体检索（§20.4，B02；仅本人范围） --------------------------------

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

export function siteSearch(params: {
  q: string; kinds?: string[]; workspace_id?: string;
  offset?: number; limit?: number; include_content?: boolean;
}): Promise<SiteSearchResponse> {
  const search = new URLSearchParams();
  search.set("q", params.q);
  if (params.kinds?.length) search.set("kinds", params.kinds.join(","));
  if (params.workspace_id) search.set("workspace_id", params.workspace_id);
  if (params.offset) search.set("offset", String(params.offset));
  if (params.limit) search.set("limit", String(params.limit));
  if (params.include_content) search.set("include_content", "true");
  return request<SiteSearchResponse>(`/search?${search.toString()}`);
}

// --- 交接草稿读取/消费（§9.5/§19.6，A13；仅本人） ---------------------------

export function getAssistantDraft(draftId: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/drafts/${draftId}`);
}

export function consumeAssistantDraft(
    draftId: string,
    resultEntity?: Record<string, unknown>): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/drafts/${draftId}/consume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ result_entity: resultEntity ?? null }),
  });
}

export function deleteAssistantDraft(draftId: string): Promise<void> {
  return request<void>(`/drafts/${draftId}`, { method: "DELETE" });
}

// --- 工作流 / 办理事项（C01/C03） ---------------------------

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
      step_id: string; operation: string;
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

export function listAssistantWorkflows(offset = 0, limit = 20,
                                       state?: string):
    Promise<WorkflowListResponse> {
  const q = new URLSearchParams({
    offset: String(offset), limit: String(limit),
  });
  if (state) q.set("state", state);
  return request<WorkflowListResponse>(`/workflows?${q.toString()}`);
}

export function getAssistantWorkflow(workflowId: string):
    Promise<{ workflow: AssistantWorkflowView; preview: WorkflowPlanPreview }> {
  return request<{ workflow: AssistantWorkflowView;
                   preview: WorkflowPlanPreview }>(
    `/workflows/${encodeURIComponent(workflowId)}`);
}

export function approveAssistantWorkflow(workflowId: string, body: {
  expected_revision: number; plan_hash: string; approved_step_ids: string[];
}): Promise<AssistantWorkflowView> {
  return request<AssistantWorkflowView>(
    `/workflows/${encodeURIComponent(workflowId)}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
}

export function startAssistantWorkflow(workflowId: string, body: {
  client_request_id: string; expected_revision: number;
}): Promise<AssistantWorkflowView> {
  return request<AssistantWorkflowView>(
    `/workflows/${encodeURIComponent(workflowId)}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
}

export function cancelAssistantWorkflow(workflowId: string):
    Promise<AssistantWorkflowView> {
  return request<AssistantWorkflowView>(
    `/workflows/${encodeURIComponent(workflowId)}/cancel`, {
      method: "POST",
    });
}

export function retryAssistantWorkflowStep(workflowId: string,
                                           stepId: string, body: {
  expected_revision: number; client_request_id: string;
}): Promise<AssistantWorkflowView> {
  return request<AssistantWorkflowView>(
    `/workflows/${encodeURIComponent(workflowId)}/steps/`
    + `${encodeURIComponent(stepId)}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
}

export function resumeAssistantWorkflow(workflowId: string, body: {
  expected_revision: number; choice_id?: string; approval_id?: string;
}): Promise<AssistantWorkflowView> {
  return request<AssistantWorkflowView>(
    `/workflows/${encodeURIComponent(workflowId)}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
}

// --- 主动服务：订阅 / 收件箱 / 报告（C04/C05） ----------------

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

export function listAssistantSubscriptions():
    Promise<{ items: AssistantSubscription[] }> {
  return request<{ items: AssistantSubscription[] }>("/subscriptions");
}

export function createAssistantSubscription(body: {
  client_request_id: string; kind: string; timezone?: string;
  local_time?: string; weekdays?: number[];
  scope?: Record<string, unknown>;
}): Promise<AssistantSubscription> {
  return request<AssistantSubscription>("/subscriptions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function patchAssistantSubscription(id: string, body: {
  expected_revision: number;
  enabled?: boolean; timezone?: string; local_time?: string;
  weekdays?: number[]; scope?: Record<string, unknown>;
  quiet_hours?: Record<string, string>;
}): Promise<AssistantSubscription> {
  return request<AssistantSubscription>(
    `/subscriptions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
}

export function deleteAssistantSubscription(id: string): Promise<void> {
  return request<void>(`/subscriptions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function listAssistantNotifications(offset = 0, limit = 20,
                                           unreadOnly = false):
    Promise<{ items: AssistantNotification[]; total: number }> {
  return request<{ items: AssistantNotification[]; total: number }>(
    `/notifications?offset=${offset}&limit=${limit}`
    + `${unreadOnly ? "&unread_only=true" : ""}`);
}

export function markNotificationRead(id: string):
    Promise<AssistantNotification> {
  return request<AssistantNotification>(
    `/notifications/${encodeURIComponent(id)}/read`, { method: "POST" });
}

export function dismissAssistantNotification(id: string,
                                             muteEntity = false):
    Promise<AssistantNotification> {
  return request<AssistantNotification>(
    `/notifications/${encodeURIComponent(id)}/dismiss`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mute_entity: muteEntity }),
    });
}

export function getAssistantReport(reportId: string):
    Promise<AssistantReport> {
  return request<AssistantReport>(
    `/reports/${encodeURIComponent(reportId)}`);
}

export function deleteAssistantReport(reportId: string): Promise<void> {
  return request<void>(
    `/reports/${encodeURIComponent(reportId)}`, { method: "DELETE" });
}

// --- 助手偏好（B09 服务端 + C05 设置卡） ----------------

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

export function getAssistantPreferences():
    Promise<AssistantPreferencesPayload> {
  return request<AssistantPreferencesPayload>("/preferences");
}

export function putAssistantPreferences(
    patch: AssistantPreferencesPayload):
    Promise<AssistantPreferencesPayload> {
  return request<AssistantPreferencesPayload>("/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export interface SseHandlers {
  onEvent: (event: AssistantSseEvent) => void;
  onGone?: () => void; // 连接中断且重试耗尽
  signal?: AbortSignal;
}

/** 订阅一轮 SSE 事件；断线退避重连，只 GET events 不重发 turns（§11.5）。 */
export async function streamTurnEvents(
  turnId: string, afterSeq: number, handlers: SseHandlers,
): Promise<void> {
  const backoff = [1000, 2000, 4000];
  let attempt = 0;
  let cursor = afterSeq;
  while (!handlers.signal?.aborted) {
    let res: Response;
    try {
      res = await apiFetch(
        `${API_BASE}/assistant/turns/${turnId}/events?after_seq=${cursor}`,
        { signal: handlers.signal, headers: { Accept: "text/event-stream" } });
    } catch {
      if (handlers.signal?.aborted) return;
      if (attempt >= backoff.length) {
        handlers.onGone?.();
        return;
      }
      await new Promise((r) => setTimeout(r, backoff[attempt++]));
      continue;
    }
    if (!res.ok || !res.body) {
      if (res.status === 404) {
        handlers.onGone?.();
        return;
      }
      if (attempt >= backoff.length) {
        handlers.onGone?.();
        return;
      }
      await new Promise((r) => setTimeout(r, backoff[attempt++]));
      continue;
    }
    attempt = 0;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (!handlers.signal?.aborted) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      let eventName = "";
      let dataLines: string[] = [];
      for (const line of lines) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trim());
        } else if (line === "" && eventName && dataLines.length) {
          try {
            const payload = JSON.parse(dataLines.join("\n"));
            payload.event = eventName;
            const seq = Number(payload.event_seq ?? 0);
            if (seq > 0) {
              if (seq <= cursor) {
                eventName = ""; dataLines = [];
                continue; // 丢弃已处理序号
              }
              cursor = seq;
            }
            handlers.onEvent(payload as AssistantSseEvent);
            if (eventName === "turn_done") return;
          } catch {
            // 忽略坏帧
          }
          eventName = "";
          dataLines = [];
        } else if (line === "") {
          eventName = "";
          dataLines = [];
        }
      }
    }
    if (handlers.signal?.aborted) return;
    // 连接被服务端关闭（终态后正常结束由 turn_done return 覆盖）；
    // 其他关闭视为断线，进入退避。
    if (attempt >= backoff.length) {
      handlers.onGone?.();
      return;
    }
    await new Promise((r) => setTimeout(r, backoff[attempt++]));
  }
}

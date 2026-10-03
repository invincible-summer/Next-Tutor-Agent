// 助手前端状态（A06）。
// PanelMode 与 TurnState 相互独立：最小化不等于取消生成。
// 服务端持有完整会话；Zustand 只承载面板状态、实时增量与未读。
// 草稿按 user+conversation 存 sessionStorage；面板尺寸偏好存
// localStorage（账号维度，退出登录由 Provider 清理）。
"use client";

import { stringsFor } from "@/components/assistant/strings";
import { create } from "zustand";
import { useUIStore } from "@/lib/store";
import {
  AssistantApiError,
  cancelTurn,
  createConversation,
  deleteConversation,
  getConversation,
  getCapabilities,
  listAssistantNotifications,
  listAssistantWorkflows,
  listConversations,
  postTurn,
  streamTurnEvents,
  undoAssistantAction,
} from "./api";
import type { AssistantWorkflowView } from "./api";
import {
  autoExecuteEligible,
  automaticCandidate,
  executeAction as executeActionApi,
  ackAction as ackActionApi,
  fetchActionPreview,
  approvePreview,
  runPageCommand,
} from "./actions";
import { currentRouteEpoch } from "./page-context";
import type {
  ActionPreview,
  AssistantAction,
  AssistantBlock,
  AssistantCapabilities,
  AssistantMessage,
  AssistantRouteId,
  AssistantSseEvent,
  ConversationSummary,
} from "./types.generated";

export type PanelMode = "collapsed" | "standard" | "expanded";
export type PanelView = "conversation" | "history" | "tasks" | "inbox";
export type TurnState =
  | "idle" | "accepting" | "running" | "reconnecting"
  | "completed" | "cancelled" | "failed" | "interrupted";
export type ScopeMode = "follow_page" | "workspace" | "all_workspaces";

export interface PageContextSnapshot {
  schema_version: 1;
  route_id: AssistantRouteId;
  route_epoch: number;
  workspace_id?: string;
  view?: string;
}

interface AssistantState {
  // 面板
  mode: PanelMode;
  view: PanelView;
  unread: boolean;
  immersiveMode: boolean;
  userId: string | null;
  lang: "zh" | "en";
  // 能力
  capabilities: AssistantCapabilities | null;
  capsError: string | null;
  // 会话
  conversationId: string | null;
  conversationRevision: number;
  conversationTitle: string;
  messages: AssistantMessage[];
  hasMoreMessages: boolean;
  activeTurnId: string | null;
  turnState: TurnState;
  statusLabel: string;
  error: string | null;
  // 输入与范围
  draft: string;
  scopeMode: ScopeMode;
  scopeWorkspaceId: string | null;
  scopeLabel: string | null;
  pageContext: PageContextSnapshot;
  // 历史
  history: ConversationSummary[];
  historyTotal: number;
  historyPage: number;
  historyLoading: boolean;
  // 办理事项（§23.6，C03）
  tasks: AssistantWorkflowView[];
  tasksTotal: number;
  tasksPage: number;
  tasksLoading: boolean;
  tasksError: string | null;
  // 收件箱未读（§25.5 柔和未读点；只计数不拉正文）
  inboxUnread: number;
  // 运行簿记
  streamAbort: AbortController | null;
  pendingSend: AbortController | null;
  // 动作执行（A10）
  routerPush: ((url: string) => unknown) | null;
  actionBusy: Record<string, boolean>;
  downgradedActions: Record<string, boolean>;
  // 领域写预览与审批（§21.4，B03 前端收口）
  actionPreviews: Record<string, ActionPreview | undefined>;
  previewBusy: Record<string, boolean>;
  previewError: Record<string, string | undefined>;

  // actions
  setUserId: (userId: string | null) => void;
  setLang: (lang: "zh" | "en") => void;
  setRouterPush: (push: (url: string) => unknown) => void;
  runAction: (action: AssistantAction, approvalId?: string) => Promise<void>;
  loadActionPreview: (action: AssistantAction) => Promise<void>;
  confirmActionPreview: (action: AssistantAction,
    decision: "approve" | "reject") => Promise<void>;
  undoAction: (action: AssistantAction) => Promise<void>;
  maybeAutoExecute: () => Promise<void>;
  open: () => void;
  collapse: () => void;
  toggle: () => void;
  setMode: (mode: PanelMode) => void;
  setView: (view: PanelView) => void;
  setDraft: (text: string) => void;
  setPageContext: (ctx: PageContextSnapshot) => void;
  setScope: (mode: ScopeMode, workspaceId?: string | null, label?: string | null) => void;
  markRead: () => void;
  refreshCapabilities: () => Promise<void>;
  ensureConversation: () => Promise<string | null>;
  refreshHistory: (page?: number) => Promise<void>;
  refreshTasks: (page?: number) => Promise<void>;
  refreshInboxUnread: () => Promise<void>;
  openConversation: (cid: string) => Promise<void>;
  newConversation: () => Promise<void>;
  removeConversation: (cid: string, revision: number) => Promise<void>;
  send: (text: string, choice?: { block_id: string; option_id: string }) => Promise<void>;
  stop: () => Promise<void>;
  applyEvent: (event: AssistantSseEvent) => void;
  resetForIdentity: (userId: string | null) => void;
}

function draftKey(userId: string | null, cid: string | null): string | null {
  if (!userId || !cid) return null;
  return `edu-assistant-draft:${userId}:${cid}`;
}

function lastConversationKey(userId: string | null): string | null {
  return userId ? `edu-assistant-last:${userId}` : null;
}

function loadDraft(userId: string | null, cid: string | null): string {
  const key = draftKey(userId, cid);
  if (!key) return "";
  try {
    return sessionStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function saveDraft(userId: string | null, cid: string | null, text: string) {
  const key = draftKey(userId, cid);
  if (!key) return;
  try {
    if (text) sessionStorage.setItem(key, text);
    else sessionStorage.removeItem(key);
  } catch {
    // 私隐模式等场景静默失败
  }
}

let routeEpochCounter = 0;
export function nextRouteEpoch(): number {
  routeEpochCounter += 1;
  return routeEpochCounter;
}

const EMPTY_PAGE: PageContextSnapshot = {
  schema_version: 1, route_id: "home" as AssistantRouteId, route_epoch: 0,
};

export const useAssistantStore = create<AssistantState>((set, get) => ({
  mode: "collapsed",
  view: "conversation",
  unread: false,
  immersiveMode: false,
  userId: null,
  lang: "zh",
  capabilities: null,
  capsError: null,
  conversationId: null,
  conversationRevision: 0,
  conversationTitle: "",
  messages: [],
  hasMoreMessages: false,
  activeTurnId: null,
  turnState: "idle",
  statusLabel: "",
  error: null,
  draft: "",
  scopeMode: "follow_page",
  scopeWorkspaceId: null,
  scopeLabel: null,
  pageContext: EMPTY_PAGE,
  history: [],
  historyTotal: 0,
  historyPage: 0,
  historyLoading: false,
  tasks: [],
  tasksTotal: 0,
  tasksPage: 0,
  tasksLoading: false,
  tasksError: null,
  inboxUnread: 0,
  streamAbort: null,
  pendingSend: null,
  routerPush: null,
  actionBusy: {},
  downgradedActions: {},
  actionPreviews: {},
  previewBusy: {},
  previewError: {},

  setUserId: (userId) => set({ userId }),
  setLang: (lang) => set({ lang }),
  setRouterPush: (push) => set({ routerPush: push }),

  runAction: async (action, approvalId) => {
    const state = get();
    if (state.actionBusy[action.action_id]) return;
    // §22.4 本地 UI 偏好：客户端直接写 useUIStore，不经服务端（无 execute
    // 往返）；成功即终态，不产生业务回执。
    if (action.payload?.kind === "set_local_preference") {
      const ok = applyLocalPreference(action.payload.key,
                                       action.payload.value);
      patchActionState(set, action.action_id,
                       { state: ok ? "succeeded" : "failed" });
      return;
    }
    if (!["proposed", "failed", "needs_attention"].includes(
        action.state ?? "proposed")) {
      return;
    }
    const push = state.routerPush;
    if (!push) return;
    set((s) => ({
      actionBusy: { ...s.actionBusy, [action.action_id]: true },
    }));
    patchActionState(set, action.action_id, { state: "executing" });
    const invocationId = crypto.randomUUID();
    try {
      const epoch = currentRouteEpoch();
      let resp = await executeActionApi(
        action.action_id, invocationId, epoch, approvalId);
      const syncRevision = (revision: number) => {
        if (get().userId !== state.userId || get().conversationId !== state.conversationId) return false;
        set((current) => ({ conversationRevision: Math.max(current.conversationRevision, revision) }));
        return true;
      };
      if (!syncRevision(resp.conversation_revision)) return;
      // 异步准备（202/command=null）：按 1s/2s/4s 轮询最多 30s（§19.4）。
      // domain_write 成功后无页面命令、状态直接终态——同样要退出轮询。
      const terminal = (state?: string | null) => [
        "succeeded", "failed", "cancelled", "expired", "needs_attention",
      ].includes(String(state ?? ""));
      let waited = 0;
      const delays = [1000, 2000, 4000];
      let delayIndex = 0;
      while (!resp.command && !terminal(resp.action.state)
             && waited < 30000) {
        const delay = delays[Math.min(delayIndex, delays.length - 1)];
        delayIndex += 1;
        await new Promise((r) => setTimeout(r, delay));
        waited += delay;
        resp = await executeActionApi(
          action.action_id, invocationId, epoch, approvalId);
        if (!syncRevision(resp.conversation_revision)) return;
      }
      if (!resp.command || !resp.command_id || !resp.ack_token) {
        // 无命令可执行（已失败/已终结）：同步本地状态即可。
        patchActionState(set, action.action_id, {
          state: resp.action.state ?? action.state,
          // §21.5：business_result.undo 驱动撤销入口的显示。
          business_result: resp.business_result ?? undefined,
        });
        return;
      }
      patchActionState(set, action.action_id, { state: "awaiting_ack" });
      const result = await runPageCommand(resp.command, { push });
      const acked = await ackActionApi(
        action.action_id, resp.command_id, resp.ack_token, result);
      if (!syncRevision(acked.conversation_revision)) return;
      patchActionState(set, action.action_id, {
        state: acked.action.state ?? "succeeded",
        business_result: acked.business_result ?? undefined,
      });
    } catch (err) {
      // §11.7：409/preview_stale 等错误保留原状态并提示重新预览。
      const message = err instanceof AssistantApiError
        ? err.message : "";
      if (message) {
        set((s) => ({
          previewError: { ...s.previewError, [action.action_id]: message },
        }));
      }
      patchActionState(set, action.action_id, { state: action.state ?? "proposed" });
    } finally {
      set((s) => {
        const next = { ...s.actionBusy };
        delete next[action.action_id];
        return { actionBusy: next };
      });
    }
  },

  loadActionPreview: async (action) => {
    const state = get();
    if (state.previewBusy[action.action_id]) return;
    set((s) => ({
      previewBusy: { ...s.previewBusy, [action.action_id]: true },
      previewError: { ...s.previewError, [action.action_id]: undefined },
    }));
    try {
      const preview = await fetchActionPreview(action.action_id);
      set((s) => ({
        actionPreviews: { ...s.actionPreviews,
                          [action.action_id]: preview },
      }));
    } catch (err) {
      set((s) => ({
        previewError: { ...s.previewError,
          [action.action_id]: err instanceof AssistantApiError
            ? err.message : stringsFor(get().lang).errorPreview },
      }));
    } finally {
      set((s) => {
        const next = { ...s.previewBusy };
        delete next[action.action_id];
        return { previewBusy: next };
      });
    }
  },

  confirmActionPreview: async (action, decision) => {
    const state = get();
    const preview = state.actionPreviews[action.action_id];
    if (!preview || state.actionBusy[action.action_id]) return;
    set((s) => ({
      previewBusy: { ...s.previewBusy, [action.action_id]: true },
      previewError: { ...s.previewError, [action.action_id]: undefined },
    }));
    try {
      if (decision === "reject") {
        await approvePreview(action.action_id, preview, "reject");
        patchActionState(set, action.action_id, { state: "cancelled" });
        return;
      }
      const approval = await approvePreview(action.action_id, preview,
                                            "approve");
      // approve 只是许可；执行仍走统一 execute 链（§21.4）。
      await get().runAction(action, approval.approval_id);
    } catch (err) {
      set((s) => ({
        previewError: { ...s.previewError,
          [action.action_id]: err instanceof AssistantApiError
            ? err.message : stringsFor(get().lang).errorAction },
      }));
    } finally {
      set((s) => {
        const next = { ...s.previewBusy };
        delete next[action.action_id];
        return { previewBusy: next };
      });
    }
  },

  undoAction: async (action) => {
    const state = get();
    // §21.4：撤销只对携带前值快照的已完成动作开放。
    if (state.actionBusy[action.action_id]) return;
    if (action.state !== "succeeded"
        || action.undo_result
        || !action.business_result?.undo) {
      return;
    }
    const revision = action.business_result.result_revision ?? "";
    if (!revision) return;
    set((s) => ({
      actionBusy: { ...s.actionBusy, [action.action_id]: true },
      previewError: { ...s.previewError, [action.action_id]: undefined },
    }));
    try {
      const out = await undoAssistantAction(action.action_id, {
        client_request_id: crypto.randomUUID(),
        expected_result_revision: revision,
      });
      // 服务端记录 undo_result（state 保持 succeeded 终态，§19.3）。
      patchActionState(set, action.action_id,
                       { undo_result: out.action.undo_result ?? undefined });
    } catch (err) {
      set((s) => ({
        previewError: { ...s.previewError,
          [action.action_id]: err instanceof AssistantApiError
            ? err.message : stringsFor(get().lang).errorUndo },
      }));
    } finally {
      set((s) => {
        const next = { ...s.actionBusy };
        delete next[action.action_id];
        return { actionBusy: next };
      });
    }
  },

  maybeAutoExecute: async () => {
    const state = get();
    // §9.2：epoch 已变化 / 面板收起 / 标签页隐藏 → automatic 降为 user_click。
    if (!autoExecuteEligible(state.mode !== "collapsed")
        || currentRouteEpoch() !== state.pageContext.route_epoch) {
      downgradeAutomaticActions(set, state.messages);
      return;
    }
    const action = latestAutomaticAction(state.messages);
    if (action) {
      await get().runAction(action);
    }
  },

  open: () => {
    const { userId, conversationId } = get();
    const lastKey = lastConversationKey(userId);
    if (!conversationId && lastKey) {
      try {
        const last = sessionStorage.getItem(lastKey);
        if (last) {
          set({ conversationId: last, draft: loadDraft(userId, last) });
        }
      } catch {
        // ignore
      }
    }
    set({ mode: "standard", unread: false, view: "conversation" });
  },
  collapse: () => set({ mode: "collapsed" }),
  toggle: () => (get().mode === "collapsed" ? get().open() : get().collapse()),
  setMode: (mode) => {
    try {
      const { userId } = get();
      if (userId) {
        localStorage.setItem(`edu-assistant-panel:${userId}`, mode);
      }
    } catch {
      // ignore
    }
    set({ mode });
  },
  setView: (view) => set({ view }),
  setDraft: (text) => {
    const { userId, conversationId } = get();
    saveDraft(userId, conversationId, text);
    set({ draft: text });
  },
  setPageContext: (ctx) => set({ pageContext: ctx }),
  setScope: (mode, workspaceId = null, label = null) =>
    set({ scopeMode: mode, scopeWorkspaceId: workspaceId, scopeLabel: label }),
  markRead: () => set({ unread: false }),

  refreshCapabilities: async () => {
    try {
      const caps = await getCapabilities();
      set({ capabilities: caps, capsError: null });
    } catch (err) {
      const message = err instanceof AssistantApiError
        ? err.message : stringsFor(get().lang).errorCapabilities;
      set({ capsError: message });
    }
  },

  ensureConversation: async () => {
    const { userId, conversationId } = get();
    if (conversationId) return conversationId;
    if (!userId) return null;
    try {
      const created = await createConversation(crypto.randomUUID());
      const lastKey = lastConversationKey(userId);
      if (lastKey) {
        try {
          sessionStorage.setItem(lastKey, created.conversation_id);
        } catch {
          // ignore
        }
      }
      set({
        conversationId: created.conversation_id,
        conversationRevision: created.revision,
        conversationTitle: "",
        messages: [],
        hasMoreMessages: false,
      });
      return created.conversation_id;
    } catch (err) {
      const message = err instanceof AssistantApiError
        ? err.message : stringsFor(get().lang).errorConversationCreate;
      set({ error: message });
      return null;
    }
  },

  refreshHistory: async (page = 0) => {
    set({ historyLoading: true });
    try {
      const res = await listConversations(page * 20, 20);
      set({
        history: res.items, historyTotal: res.total,
        historyPage: page, historyLoading: false,
      });
    } catch {
      set({ historyLoading: false });
    }
  },

  refreshTasks: async (page = 0) => {
    set({ tasksLoading: true });
    try {
      const res = await listAssistantWorkflows(page * 20, 20);
      set({
        tasks: res.items, tasksTotal: res.total,
        tasksPage: page, tasksLoading: false, tasksError: null,
      });
    } catch (err) {
      set({
        tasksLoading: false,
        tasksError: err instanceof AssistantApiError && err.status === 401
          ? null : String((err as Error)?.message ?? err),
      });
    }
  },

  refreshInboxUnread: async () => {
    if (!get().userId) return;
    try {
      const res = await listAssistantNotifications(0, 1, true);
      set({ inboxUnread: res.total });
    } catch {
      // 未读计数获取失败不打扰用户（§25.5 只显示柔和点）
    }
  },

  openConversation: async (cid) => {
    const { userId } = get();
    try {
      const detail = await getConversation(cid);
      set({
        conversationId: cid,
        conversationRevision: detail.revision,
        conversationTitle: detail.title,
        messages: detail.messages,
        hasMoreMessages: detail.has_more,
        draft: loadDraft(userId, cid),
        view: "conversation",
        activeTurnId: detail.active_turn?.turn_id ?? null,
        turnState: detail.active_turn ? "running" : "idle",
      });
      const key = lastConversationKey(userId);
      if (key) {
        try {
          sessionStorage.setItem(key, cid);
        } catch {
          // ignore
        }
      }
    } catch (err) {
      const message = err instanceof AssistantApiError
        ? err.message : stringsFor(get().lang).errorConversationRead;
      set({ error: message });
    }
  },

  newConversation: async () => {
    const { userId, activeTurnId, turnState } = get();
    // §4.2：当前轮若仍运行，先停止再新建。
    if (activeTurnId && ["accepting", "running", "reconnecting"].includes(turnState)) {
      await get().stop();
    }
    set({
      conversationId: null, conversationRevision: 0, conversationTitle: "",
      messages: [], hasMoreMessages: false, activeTurnId: null,
      turnState: "idle", error: null, view: "conversation",
      draft: loadDraft(userId, null),
    });
  },

  removeConversation: async (cid, revision) => {
    try {
      await deleteConversation(cid, revision);
    } catch {
      // 删除失败留给下轮刷新暴露
    }
    await get().refreshHistory(get().historyPage);
    if (get().conversationId === cid) {
      set({
        conversationId: null, messages: [], conversationRevision: 0,
        conversationTitle: "", activeTurnId: null, turnState: "idle",
      });
    }
  },

  send: async (text, choice) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const state = get();
    if (["accepting", "running", "reconnecting"].includes(state.turnState)) {
      return; // 每会话只运行一轮，不隐式排队
    }
    const cid = await state.ensureConversation();
    if (!cid) return;
    const clientMessageId = crypto.randomUUID();
    const scope = state.scopeMode === "workspace" && state.scopeWorkspaceId
      ? { mode: "workspace" as const, workspace_id: state.scopeWorkspaceId }
      : state.scopeMode === "all_workspaces"
        ? { mode: "all_workspaces" as const }
        : { mode: "follow_page" as const };
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    set({
      turnState: "accepting", error: null, statusLabel: "",
      draft: "", activeTurnId: null,
    });
    saveDraft(state.userId, cid, "");
    try {
      const accepted = await postTurn(cid, {
        schema_version: 1,
        client_message_id: clientMessageId,
        expected_conversation_revision: state.conversationRevision || 1,
        text: trimmed,
        lang: state.lang,
        timezone: tz,
        scope,
        page_context: state.pageContext,
        choice: choice ?? null,
      });
      set({
        activeTurnId: accepted.turn_id,
        conversationRevision: accepted.conversation_revision,
        turnState: accepted.state === "running" ? "running" : "idle",
        // 乐观插入用户消息：正常流事件只带 assistant 侧，服务端为权威。
        messages: [...get().messages, {
          message_id: `local_${clientMessageId}`,
          seq: get().messages.length + 1,
          role: "user",
          created_at: new Date().toISOString(),
          turn_id: accepted.turn_id,
          status: "complete",
          scope: { mode: "workspace", workspace_ids: [], scope_revisions: {} },
          blocks: [{ block_id: "u1", type: "markdown", text: trimmed }],
          sources: [],
        } satisfies AssistantMessage],
      });
      if (accepted.state === "running" || accepted.duplicate) {
        const abort = new AbortController();
        set({ streamAbort: abort });
        await streamTurnEvents(accepted.turn_id, 0, {
          signal: abort.signal,
          onEvent: (event) => get().applyEvent(event),
          onGone: () => {
            const cur = get();
            if (["running", "reconnecting"].includes(cur.turnState)) {
              set({ turnState: "reconnecting",
                    statusLabel: "connection_lost" });
            }
          },
        });
        const abortNow = get().streamAbort;
        if (abortNow === abort) set({ streamAbort: null });
      }
    } catch (err) {
      const apiErr = err instanceof AssistantApiError ? err : null;
      // 409 conversation_changed：读最新快照，不自动重发。
      if (apiErr?.code === "conversation_changed") {
        await get().openConversation(cid);
        set({ draft: trimmed, turnState: "idle" });
        return;
      }
      set({
        turnState: apiErr ? "failed" : "idle",
        error: apiErr?.message ?? stringsFor(get().lang).errorSend,
        draft: trimmed,
      });
    }
  },

  stop: async () => {
    const { activeTurnId, streamAbort } = get();
    streamAbort?.abort();
    set({ streamAbort: null });
    if (activeTurnId) {
      try {
        await cancelTurn(activeTurnId, crypto.randomUUID());
      } catch {
        // 服务端不可达时本地仍按已停止处理
      }
    }
    set({ turnState: "idle", statusLabel: "" });
  },

  applyEvent: (event) => {
    const type = event.event;
    if (type === "status") {
      set({ statusLabel: event.label });
      return;
    }
    if (type === "text_delta") {
      set((s) => {
        const exists = s.messages.some(
          (m) => m.message_id === event.message_id);
        if (!exists) {
          // 首个增量：创建流式 assistant 消息占位。
          return {
            messages: [...s.messages, {
              message_id: event.message_id,
              seq: s.messages.length + 1,
              role: "assistant",
              created_at: new Date().toISOString(),
              turn_id: s.activeTurnId ?? "",
              status: "streaming",
              scope: { mode: "workspace", workspace_ids: [],
                       scope_revisions: {} },
              blocks: [{ block_id: event.block_id, type: "markdown",
                         text: event.delta }],
              sources: [],
            } satisfies AssistantMessage],
          };
        }
        return {
          messages: s.messages.map((m): AssistantMessage =>
            m.message_id === event.message_id
              ? {
                  ...m,
                  status: "streaming",
                  blocks: upsertBlockText<AssistantBlock>(
                      m.blocks, event.block_id, event.delta),
                }
              : m),
        };
      });
      return;
    }
    if (type === "block_upsert") {
      set((s) => ({
        messages: s.messages.map((m): AssistantMessage =>
          m.message_id === event.message_id
            ? { ...m,
                blocks: upsertBlock<AssistantBlock>(
                    m.blocks, event.block as AssistantBlock) }
            : m),
      }));
      return;
    }
    if (type === "snapshot") {
      const snap = event.snapshot;
      set({
        conversationRevision: snap.conversation_revision,
        messages: [snap.user_message, snap.assistant_message],
        turnState: mapServerState(snap.state),
        activeTurnId: snap.turn_id,
      });
      return;
    }
    if (type === "message_done") {
      const message = event.message;
      set((s) => ({
        messages: mergeMessage(s.messages, message),
      }));
      return;
    }
    if (type === "turn_done") {
      set((s) => ({
        turnState: mapServerState(event.state),
        conversationRevision: event.conversation_revision,
        statusLabel: "",
        unread: s.mode === "collapsed" ? true : s.unread,
        streamAbort: null,
      }));
      // §19.3：仅活跃标签页首次收到本轮新建 automatic 动作时自动执行；
      // 历史加载/快照恢复不触发（snapshot 分支无此调用）。
      if (event.state === "completed") {
        void get().maybeAutoExecute();
      }
    }
    if (type === "error") {
      set({ error: event.error?.message ?? stringsFor(get().lang).errorAnswer });
    }
  },

  resetForIdentity: (userId) => {
    const { streamAbort } = get();
    streamAbort?.abort();
    set({
      userId,
      capabilities: null, capsError: null,
      conversationId: null, conversationRevision: 0, conversationTitle: "",
      messages: [], hasMoreMessages: false,
      activeTurnId: null, turnState: "idle", statusLabel: "",
      error: null, draft: "", unread: false,
      history: [], historyTotal: 0, historyPage: 0,
      tasks: [], tasksTotal: 0, tasksPage: 0, tasksError: null,
      inboxUnread: 0,
      streamAbort: null, view: "conversation",
      scopeMode: "follow_page", scopeWorkspaceId: null, scopeLabel: null,
      actionBusy: {}, downgradedActions: {},
      actionPreviews: {}, previewBusy: {}, previewError: {},
    });
  },
}));

function mapServerState(state: string): TurnState {
  switch (state) {
    case "running": return "running";
    case "cancelled": return "cancelled";
    case "failed": return "failed";
    case "interrupted": return "interrupted";
    default: return "completed";
  }
}

function mergeMessage(messages: AssistantMessage[], incoming: AssistantMessage):
    AssistantMessage[] {
  const index = messages.findIndex((m) => m.message_id === incoming.message_id);
  if (index < 0) return [...messages, incoming];
  const next = [...messages];
  next[index] = incoming;
  return next;
}

// 块数组的按 block_id 追加/替换；B 保持消息块联合类型不变。
function upsertBlockText<B extends { block_id: string; type?: string }>(
    blocks: B[] | undefined, blockId: string, delta: string): B[] {
  const list = blocks ?? [];
  // text_delta 追加语义（§11.5）：只追加到对应 markdown 块。
  const index = list.findIndex((b) => b.block_id === blockId);
  if (index < 0) {
    return [...list, { block_id: blockId, type: "markdown", text: delta } as unknown as B];
  }
  const next = [...list];
  next[index] = { ...next[index], text: String((next[index] as { text?: string }).text ?? "") + delta } as B;
  return next;
}

function upsertBlock<B extends { block_id: string }>(
    blocks: B[] | undefined, block: B): B[] {
  const list = blocks ?? [];
  const index = list.findIndex((b) => b.block_id === block.block_id);
  if (index < 0) return [...list, block];
  const next = [...list];
  next[index] = block;
  return next;
}

type StoreSetter = {
  (partial: AssistantState | Partial<AssistantState> | ((s: AssistantState) => AssistantState | Partial<AssistantState>)): void;
};

// 动作卡本地状态回写：只改消息 actions 块中对应 action_id 的展示状态。
function patchActionState(
    set: StoreSetter, actionId: string, patch: Partial<AssistantAction>) {
  set((s) => ({
    messages: s.messages.map((m): AssistantMessage => ({
      ...m,
      blocks: (m.blocks ?? []).map((b): AssistantBlock =>
        b.type === "actions"
          ? {
              ...b,
              items: b.items.map((it) => it.action_id === actionId
                ? { ...it, ...patch } : it),
            }
          : b),
    })),
  }));
}


function applyLocalPreference(key: string, value: string): boolean {
  // §22.4 本地 UI 偏好：主题/字号/语言写现有 useUIStore 方法。
  try {
    if (typeof window === "undefined") return false;
    const ui = useUIStore.getState();
    if (key === "theme" && (value === "dark" || value === "light")) {
      if (ui.theme !== value) ui.toggleTheme();
      return true;
    }
    if (key === "lang" && (value === "zh" || value === "en")) {
      ui.setLang(value);
      return true;
    }
    if (key === "font_scale") {
      const scale = Number(value);
      if (Number.isFinite(scale) && scale > 0) {
        ui.setFontScale(scale);
        return true;
      }
    }
    return false;
  } catch {
    return false;
  }
}

function latestAutomaticAction(messages: AssistantMessage[]): AssistantAction | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message.role !== "assistant") continue;
    const blocks = message.blocks ?? [];
    for (let j = blocks.length - 1; j >= 0; j -= 1) {
      const block = blocks[j];
      if (block.type !== "actions") continue;
      const hit = automaticCandidate(block.items);
      return hit;
    }
    return null;
  }
  return null;
}

// §9.2：automatic 降为 user_click 的本地展示（服务端仍保留原 execution）。
function downgradeAutomaticActions(set: StoreSetter, messages: AssistantMessage[]) {
  const ids: string[] = [];
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    for (const block of message.blocks ?? []) {
      if (block.type !== "actions") continue;
      for (const item of block.items) {
        if (item.execution === "automatic"
            && (item.state ?? "proposed") === "proposed") {
          ids.push(item.action_id);
        }
      }
    }
  }
  if (!ids.length) return;
  set((s) => {
    const next = { ...s.downgradedActions };
    for (const id of ids) next[id] = true;
    return { downgradedActions: next };
  });
}

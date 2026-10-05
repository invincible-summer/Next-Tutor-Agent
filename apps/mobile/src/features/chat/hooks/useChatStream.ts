import { useCallback } from "react";

import type { ApiClient, ChatStreamEvent } from "@next-tutor/api-client";

import { apiClient } from "@/lib/api";
import { useI18n } from "@/providers/I18nProvider";
import { useUiPrefs } from "@/stores/ui";
import {
  gradeForApi,
  type AttachmentMeta,
  type ChatSessionDetail,
  type ToolCallRecord,
  type ToolResultData,
} from "../model/types";
import { useChatStore } from "../store/chatStore";

export interface ChatSendOptions {
  /** 会话绑定的工作区（仅新会话首条消息需要；绑定后服务端会话已存）。 */
  workspaceId?: string | null;
  publicTextbookIds?: string[];
  /** 发送成功绑定 session 后回调（路由层替换 URL）。 */
  onSessionBound?: (sessionId: string) => void;
}

export interface ChatStreamController {
  send: (
    message: string,
    attachments?: AttachmentMeta[],
    options?: ChatSendOptions,
  ) => Promise<void>;
  stop: () => void;
  regenerate: (options?: ChatSendOptions) => Promise<void>;
}

/**
 * 聊天流式循环（对齐 Web handleSend 语义）：
 * - thinking/answer 聚合在局部变量，50ms 节流 flush 进仓；
 * - generation 快照防串会话：done/error/abort 的残余写入在世代变化后丢弃；
 * - done 不落 return：继续排干 history_saved（携带 session_id 完成绑定）；
 * - 手动 stop 提交部分回答；切会话导致的 abort 丢弃部分回答；
 * - error 事件/异常把失败写成一条 assistant 消息（不打断已有记录）。
 */
export function useChatStream(client?: ApiClient): ChatStreamController {
  const { t, lang } = useI18n();
  const send = useCallback(
    async (
      message: string,
      attachments?: AttachmentMeta[],
      options?: ChatSendOptions,
    ) => {
      const api = client ?? apiClient();
      const chat = useChatStore.getState();
      if (chat.streaming || !message.trim()) return;
      chat.setStreaming(true);
      chat.resetPending();
      // 从仓里现取 messages（而非渲染闭包）：深链自动发送与 newChat 同一
      // commit 内发生时，闭包里的旧 transcript 会复活到新会话。
      chat.setMessages([
        ...useChatStore.getState().messages,
        { role: "user", content: message, attachments },
      ]);
      const ac = new AbortController();
      chat.setAborter(ac);
      const gen0 = useChatStore.getState().generation;
      const sameGeneration = () => useChatStore.getState().generation === gen0;

      let boundSid: string | null = null;
      const bindSession = (sid: string) => {
        if (!sameGeneration()) return;
        useChatStore.getState().setSessionId(sid);
        if (boundSid !== sid) {
          boundSid = sid;
          options?.onSessionBound?.(sid);
        }
      };

      let thinkingAccum = "";
      let answerAccum = "";
      const toolCallsAccum: ToolCallRecord[] = [];

      let flushTimer: ReturnType<typeof setTimeout> | null = null;
      const flush = () => {
        if (!sameGeneration()) return;
        if (flushTimer !== null) {
          clearTimeout(flushTimer);
          flushTimer = null;
        }
        useChatStore.getState().flushPending(thinkingAccum, answerAccum);
      };
      const scheduleFlush = () => {
        if (flushTimer !== null) return;
        flushTimer = setTimeout(() => {
          flushTimer = null;
          if (sameGeneration())
            useChatStore.getState().flushPending(thinkingAccum, answerAccum);
        }, 50);
      };

      try {
        // 新对话暂存的资料库引用：首条消息前 flush 绑定（sessionId "new"）。
        const pendingRefs = useChatStore.getState().pendingLibraryRefs;
        if (pendingRefs.length > 0) {
          const sid = useChatStore.getState().sessionId;
          const res = await api.chat.attachLibraryFiles<{
            results: AttachmentMeta[];
            session_id: string;
          }>(
            sid || "new",
            pendingRefs.map((r) => r.id),
            sid ? null : options?.workspaceId,
          );
          if (!sameGeneration() || ac.signal.aborted) return;
          if (res.results.some((r) => r.error))
            throw new Error(t("common.error.generic"));
          bindSession(res.session_id);
          useChatStore.getState().addFiles(res.results);
          useChatStore.getState().setPendingLibraryRefs([]);
        }
        const freshSid = useChatStore.getState().sessionId;
        const stream = api.chat.stream(
          {
            message,
            session_id: freshSid,
            workspace_id: freshSid ? null : (options?.workspaceId ?? null),
            grade: gradeForApi(useUiPrefs.getState().grade),
            lang,
            output_language:
              useUiPrefs.getState().outputLanguage === "auto"
                ? null
                : useUiPrefs.getState().outputLanguage,
            ...(attachments ? { attachments } : {}),
            ...(options?.publicTextbookIds?.length
              ? { public_textbook_ids: options.publicTextbookIds }
              : {}),
          },
          ac.signal,
        );
        for await (const ev of stream as AsyncIterable<ChatStreamEvent>) {
          if (!sameGeneration()) return;
          switch (ev.type) {
            case "thinking":
              thinkingAccum += String(
                (ev as Record<string, unknown>).content ?? "",
              );
              scheduleFlush();
              break;
            case "answer":
              answerAccum += ev.delta;
              scheduleFlush();
              break;
            case "step":
              useChatStore.getState().setCurrentStep(ev.step);
              useChatStore.getState().setHeartbeatElapsed(0);
              break;
            case "tool_start":
              useChatStore.getState().addToolStart(ev.tool);
              toolCallsAccum.push({ name: ev.tool });
              break;
            case "tool_progress":
              useChatStore
                .getState()
                .addToolProgress(
                  String((ev as Record<string, unknown>).message ?? ""),
                );
              break;
            case "tool_result": {
              const result = (ev as unknown as Record<string, unknown>)
                .result as ToolResultData;
              const last = toolCallsAccum[toolCallsAccum.length - 1];
              if (last) last.result = result;
              useChatStore.getState().setToolResult(result);
              break;
            }
            case "tool_warning":
              useChatStore.getState().addToolProgress(`⚠ ${ev.message}`);
              break;
            case "heartbeat":
              useChatStore
                .getState()
                .setHeartbeatElapsed(
                  Number(
                    (ev as unknown as Record<string, unknown>).elapsed ?? 0,
                  ),
                );
              break;
            case "retry":
              useChatStore.getState().setRetry({
                attempt: Number((ev as Record<string, unknown>).attempt ?? 0),
                reason: String((ev as Record<string, unknown>).reason ?? ""),
                visible: true,
              });
              break;
            case "done":
              flush();
              if (sameGeneration()) {
                useChatStore.getState().commitAssistant();
                if (ev.session_id) bindSession(ev.session_id);
              }
              useChatStore.getState().setStreaming(false);
              useChatStore.getState().setRetry(null);
              void api.chat
                .listSessions<{ sessions: never[] }>()
                .then((r) => {
                  if (sameGeneration())
                    useChatStore.getState().setSessions(r.sessions);
                })
                .catch(() => undefined);
              // 不 return：排干后续 history_saved（携带 session_id）。
              break;
            case "history_saved":
              if (sameGeneration() && ev.session_id) bindSession(ev.session_id);
              break;
            case "error":
              flush();
              if (sameGeneration()) {
                useChatStore.getState().setMessages([
                  ...useChatStore.getState().messages,
                  {
                    role: "assistant",
                    content: `**${t("chat.error.connect")}**\n\n${ev.message ?? ""}`,
                    thinking: "",
                  },
                ]);
              }
              useChatStore.getState().setStreaming(false);
              useChatStore.getState().setRetry(null);
              return;
          }
        }
      } catch (error) {
        const aborted =
          ac.signal.aborted ||
          (error instanceof Error &&
            (error.name === "AbortError" ||
              (error as { code?: string }).code === "request_aborted"));
        if (aborted) {
          if (
            sameGeneration() &&
            (answerAccum || thinkingAccum || toolCallsAccum.length > 0)
          ) {
            useChatStore.getState().setMessages([
              ...useChatStore.getState().messages,
              {
                role: "assistant",
                content: answerAccum || thinkingAccum || t("chat.stopped"),
                thinking: thinkingAccum,
                toolCalls: toolCallsAccum,
              },
            ]);
          }
        } else if (sameGeneration()) {
          useChatStore.getState().setMessages([
            ...useChatStore.getState().messages,
            {
              role: "assistant",
              content: `**${t("chat.error.interrupted")}**\n\n${
                error instanceof Error ? error.message : String(error)
              }`,
              thinking: "",
            },
          ]);
        }
      } finally {
        if (flushTimer !== null) {
          clearTimeout(flushTimer);
          flushTimer = null;
        }
        const state = useChatStore.getState();
        if (state.aborter === ac) state.setAborter(null);
        if (sameGeneration()) {
          state.setStreaming(false);
          state.setRetry(null);
        }
      }
    },
    [client, t, lang],
  );

  const stop = useCallback(() => {
    useChatStore.getState().aborter?.abort();
  }, []);

  const regenerate = useCallback(
    async (options?: ChatSendOptions) => {
      const msgs = useChatStore.getState().messages;
      let lastAssistant = -1;
      for (let i = msgs.length - 1; i >= 0; i -= 1) {
        if (msgs[i]?.role === "assistant") {
          lastAssistant = i;
          break;
        }
      }
      if (lastAssistant < 1) return;
      const userMsg = msgs[lastAssistant - 1];
      if (!userMsg || userMsg.role !== "user") return;
      useChatStore.getState().setMessages(msgs.slice(0, lastAssistant - 1));
      await send(userMsg.content, userMsg.attachments, options);
    },
    [send],
  );

  return { send, stop, regenerate };
}

/** 供测试与屏组件读取会话详情响应的窄类型守卫。 */
export function asSessionDetail(raw: unknown): ChatSessionDetail {
  return (raw ?? {}) as ChatSessionDetail;
}

import { registerSessionCleanup } from "@/lib/session-lifecycle";
import { create } from "zustand";

import type {
  AttachmentMeta,
  ChatMessage,
  RetryState,
  SessionItem,
  ToolResultData,
} from "../model/types";

/**
 * 聊天流式状态仓（镜像 Web useChatStore 语义）：
 * - SSE token 先聚合在局部变量，50ms 节流经 flushPending 写入（约 20 次/秒重渲染）；
 * - generation 世代号防串会话：newChat/loadFull 时 +1，流式循环快照它，
 *   切会话后旧流的残余写入一律丢弃；
 * - aborter 放仓里：组件重挂载后新页面仍能取消挂载前发起的流。
 */
export interface PendingToolCall {
  name: string;
  result?: ToolResultData | undefined;
}

interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  retry: RetryState | null;
  pendingThinking: string;
  pendingAnswer: string;
  pendingToolCalls: PendingToolCall[];
  activeTool: string | null;
  toolProgress: string[];
  currentStep: string | null;
  heartbeatElapsed: number;
  files: AttachmentMeta[];
  sessions: SessionItem[];
  generation: number;
  aborter: AbortController | null;
  /** 新对话里待绑定的资料库引用（尚无 sessionId，首条消息发出前 flush）。 */
  pendingLibraryRefs: { id: string; filename: string }[];

  setAborter: (aborter: AbortController | null) => void;
  setPendingLibraryRefs: (refs: { id: string; filename: string }[]) => void;
  setSessionId: (id: string | null) => void;
  setMessages: (messages: ChatMessage[]) => void;
  setStreaming: (streaming: boolean) => void;
  setRetry: (retry: RetryState | null) => void;
  flushPending: (thinking: string, answer: string) => void;
  addToolStart: (name: string) => void;
  setToolResult: (result: ToolResultData) => void;
  addToolProgress: (message: string) => void;
  setCurrentStep: (step: string | null) => void;
  setHeartbeatElapsed: (seconds: number) => void;
  commitAssistant: () => void;
  appendAssistantNote: (content: string) => void;
  resetPending: () => void;
  setFiles: (files: AttachmentMeta[]) => void;
  addFiles: (files: AttachmentMeta[]) => void;
  setSessions: (sessions: SessionItem[]) => void;
  loadFull: (
    messages: ChatMessage[],
    files: AttachmentMeta[],
    sessionId: string,
  ) => void;
  newChat: () => void;
}

interface PendingReset {
  retry: RetryState | null;
  pendingThinking: string;
  pendingAnswer: string;
  pendingToolCalls: PendingToolCall[];
  activeTool: string | null;
  toolProgress: string[];
  currentStep: string | null;
  heartbeatElapsed: number;
}

const PENDING_RESET: PendingReset = {
  retry: null,
  pendingThinking: "",
  pendingAnswer: "",
  pendingToolCalls: [],
  activeTool: null,
  toolProgress: [],
  currentStep: null,
  heartbeatElapsed: 0,
};

export const useChatStore = create<ChatState>((set) => ({
  sessionId: null,
  messages: [],
  streaming: false,
  files: [],
  sessions: [],
  generation: 0,
  aborter: null,
  pendingLibraryRefs: [],
  ...PENDING_RESET,

  setAborter: (aborter) => set({ aborter }),
  setPendingLibraryRefs: (refs) => set({ pendingLibraryRefs: refs }),
  setSessionId: (sessionId) => set({ sessionId }),
  setMessages: (messages) => set({ messages }),
  setStreaming: (streaming) => set({ streaming }),
  setRetry: (retry) => set({ retry }),
  flushPending: (pendingThinking, pendingAnswer) =>
    set({ pendingThinking, pendingAnswer }),
  addToolStart: (name) =>
    set((s) => ({
      activeTool: name,
      pendingToolCalls: [...s.pendingToolCalls, { name }],
    })),
  setToolResult: (result) =>
    set((s) => {
      const pendingToolCalls = [...s.pendingToolCalls];
      const last = pendingToolCalls[pendingToolCalls.length - 1];
      if (last)
        pendingToolCalls[pendingToolCalls.length - 1] = { ...last, result };
      return { pendingToolCalls, activeTool: null };
    }),
  addToolProgress: (message) =>
    set((s) => ({ toolProgress: [...s.toolProgress, message] })),
  setCurrentStep: (currentStep) => set({ currentStep }),
  setHeartbeatElapsed: (heartbeatElapsed) => set({ heartbeatElapsed }),
  commitAssistant: () =>
    set((s) => ({
      messages: [
        ...s.messages,
        {
          role: "assistant",
          content: s.pendingAnswer,
          thinking: s.pendingThinking,
          toolCalls: s.pendingToolCalls,
        },
      ],
      ...PENDING_RESET,
    })),
  appendAssistantNote: (content) =>
    set((s) => ({
      messages: [...s.messages, { role: "assistant", content, thinking: "" }],
    })),
  resetPending: () => set({ ...PENDING_RESET }),
  setFiles: (files) => set({ files }),
  addFiles: (incoming) => set((s) => ({ files: [...s.files, ...incoming] })),
  setSessions: (sessions) => set({ sessions }),
  loadFull: (messages, files, sessionId) =>
    set((s) => ({
      messages,
      files,
      sessionId,
      streaming: false,
      pendingLibraryRefs: [],
      ...PENDING_RESET,
      generation: s.generation + 1,
    })),
  newChat: () =>
    set((s) => ({
      sessionId: null,
      messages: [],
      files: [],
      streaming: false,
      pendingLibraryRefs: [],
      ...PENDING_RESET,
      generation: s.generation + 1,
    })),
}));

/** 测试辅助：回到出厂状态。 */
export function resetChatStore(): void {
  const current = useChatStore.getState();
  current.aborter?.abort();
  useChatStore.setState({
    sessionId: null,
    messages: [],
    streaming: false,
    files: [],
    sessions: [],
    generation: current.generation + 1,
    aborter: null,
    pendingLibraryRefs: [],
    ...PENDING_RESET,
  });
}

registerSessionCleanup(resetChatStore);

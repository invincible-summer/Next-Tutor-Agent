import { create } from "zustand";
import type { ChatMessage, Grade, SessionItem, AttachmentMeta, RetryState } from "./types";
import { type Lang, loadLang, saveLang } from "./i18n";

interface UIState {
  grade: Grade;
  setGrade: (g: Grade) => void;
  lang: Lang;
  setLang: (l: Lang) => void;
  outputLanguage: "auto" | "zh" | "en";
  setOutputLanguage: (o: "auto" | "zh" | "en") => void;
  theme: "light" | "dark";
  toggleTheme: () => void;
  fontScale: number; // 1|1.25|1.5|1.75
  setFontScale: (n: number) => void;
  mounted: boolean;
  hydrateClient: () => void;
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  navCollapsed: boolean;
  toggleNav: () => void;
}

export const useUIStore = create<UIState>((set, get) => ({
  // 默认「本科」：产品默认学段，开箱即用本科提示词（stage_brief/难度锚点）。
  // 「自动」仍可手动选择（后端空串语义不变）。
  grade: "本科",
  setGrade: (g) => set({ grade: g }),
  // SSR-safe defaults: do NOT read localStorage in the initializer, or the
  // first client render diverges from server HTML (hydration mismatch).
  // hydrateClient() reads persisted prefs AFTER mount.
  lang: "zh",
  setLang: (l) => {
    if (typeof window !== "undefined") saveLang(l);
    set({ lang: l });
  },
  outputLanguage: "auto",
  setOutputLanguage: (o) => {
    if (typeof window !== "undefined") localStorage.setItem("edu-agent-output-lang", o);
    set({ outputLanguage: o });
  },
  theme: "light",
  fontScale: 1,
  setFontScale: (n) => {
    if (typeof window !== "undefined") {
      localStorage.setItem("edu-agent-fs", String(n));
      document.documentElement.style.setProperty("--fs-scale", String(n));
    }
    set({ fontScale: n });
  },
  toggleTheme: () => {
    const next = get().theme === "dark" ? "light" : "dark";
    if (typeof window !== "undefined") {
      document.documentElement.classList.toggle("dark", next === "dark");
      localStorage.setItem("edu-agent-theme", next);
    }
    set({ theme: next });
  },
  mounted: false,
  hydrateClient: () => {
    // Runs once on mount (client only). The no-flash script already applied
    // the dark class to <html> before hydration, so reading it here is safe.
    const lang = typeof window !== "undefined" ? loadLang() : "zh";
    const ol = (typeof window !== "undefined" ? localStorage.getItem("edu-agent-output-lang") : null) as "auto" | "zh" | "en" | null;
    const theme = typeof window !== "undefined" && document.documentElement.classList.contains("dark") ? "dark" : "light";
    let fs = 1;
    try { const v = parseFloat(localStorage.getItem("edu-agent-fs") || "1"); if (v) fs = v; } catch { /* ignore */ }
    if (typeof window !== "undefined") document.documentElement.style.setProperty("--fs-scale", String(fs));
    set({ lang, outputLanguage: ol === "zh" || ol === "en" ? ol : "auto", theme, fontScale: fs, mounted: true });
  },
  sidebarOpen: true,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  // 主边栏刻意不持久化：每次进入站点默认收起（图标轨），仅本次会话内
  // 手动展开；常量初始化也顺带消除了旧版 initializer 读 localStorage
  // 与服务端 HTML 不一致的 hydration 隐患。
  navCollapsed: true,
  toggleNav: () => set((s) => ({ navCollapsed: !s.navCollapsed })),
}));

interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  streaming: boolean;
  retry: RetryState | null;
  // Streaming accumulators (Paper_Agent pattern)
  pendingThinking: string;
  pendingAnswer: string;
  pendingToolCalls: { name: string; result?: unknown }[];
  activeTool: string | null;
  toolProgress: string[];
  currentStep: string | null;
  heartbeatElapsed: number;
  files: AttachmentMeta[];
  sessions: SessionItem[];
  /** 会话世代号：newChat/loadFull 时 +1。流式循环在发送时快照它，若中途
   * 切换了会话（世代变化），done/error/中断时的残余写入一律丢弃，避免
   * 旧流把内容写进新会话。 */
  generation: number;
  /** 当前流的 AbortController 放 store 里（而非组件 ref）：组件重挂载后
   * 新页面仍能取消挂载前发起的流。 */
  aborter: AbortController | null;
  setAborter: (ac: AbortController | null) => void;
  /** 新对话里待绑定的资料库引用（尚无 sessionId，首条消息发出前 flush） */
  pendingLibraryRefs: { id: string; filename: string }[];
  setPendingLibraryRefs: (r: { id: string; filename: string }[]) => void;
  setSessionId: (id: string | null) => void;
  setMessages: (m: ChatMessage[]) => void;
  setStreaming: (s: boolean) => void;
  setRetry: (r: RetryState | null) => void;
  appendThinking: (d: string) => void;
  appendAnswer: (d: string) => void;
  /** Bulk-set the streaming accumulators (throttled flush from the SSE loop,
   * replacing per-token append calls that re-rendered the page every token). */
  flushPending: (thinking: string, answer: string) => void;
  addToolStart: (name: string) => void;
  setToolResult: (result: unknown) => void;
  addToolProgress: (msg: string) => void;
  setCurrentStep: (step: string | null) => void;
  setHeartbeatElapsed: (n: number) => void;
  commitAssistant: () => void;
  /** 追加一条本地 assistant 消息（答题卡互动后的自动点评等）。
   *  仅前端展示，不回写后端会话；刷新后由 transcript 记录兜底。 */
  appendAssistantNote: (content: string) => void;
  resetPending: () => void;
  setFiles: (f: AttachmentMeta[]) => void;
  addFiles: (f: AttachmentMeta[]) => void;
  setSessions: (s: SessionItem[]) => void;
  loadFull: (messages: ChatMessage[], files: AttachmentMeta[], sessionId: string) => void;
  newChat: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  sessionId: null,
  messages: [],
  streaming: false,
  retry: null,
  pendingThinking: "",
  pendingAnswer: "",
  pendingToolCalls: [],
  activeTool: null,
  toolProgress: [],
  currentStep: null,
  heartbeatElapsed: 0,
  files: [],
  sessions: [],
  generation: 0,
  aborter: null,
  setAborter: (ac) => set({ aborter: ac }),
  pendingLibraryRefs: [],
  setPendingLibraryRefs: (r) => set({ pendingLibraryRefs: r }),
  setSessionId: (id) => set({ sessionId: id }),
  setMessages: (m) => set({ messages: m }),
  setStreaming: (s) => set({ streaming: s }),
  setRetry: (r) => set({ retry: r }),
  appendThinking: (d) => set((s) => ({ pendingThinking: s.pendingThinking + d })),
  appendAnswer: (d) => set((s) => ({ pendingAnswer: s.pendingAnswer + d })),
  flushPending: (thinking, answer) => set({ pendingThinking: thinking, pendingAnswer: answer }),
  addToolStart: (name) => set((s) => ({
    activeTool: name,
    pendingToolCalls: [...s.pendingToolCalls, { name }],
  })),
  setToolResult: (result) => set((s) => {
    const tc = [...s.pendingToolCalls];
    if (tc.length > 0) tc[tc.length - 1].result = result;
    return { pendingToolCalls: tc, activeTool: null };
  }),
  addToolProgress: (msg) => set((s) => ({ toolProgress: [...s.toolProgress, msg] })),
  setCurrentStep: (step) => set({ currentStep: step }),
  setHeartbeatElapsed: (n) => set({ heartbeatElapsed: n }),
  commitAssistant: () => set((s) => ({
    messages: [...s.messages, {
      role: "assistant" as const,
      content: s.pendingAnswer,
      thinking: s.pendingThinking,
      toolCalls: s.pendingToolCalls as never,
    }],
    pendingThinking: "",
    pendingAnswer: "",
    pendingToolCalls: [],
    activeTool: null,
    toolProgress: [],
    currentStep: null,
    heartbeatElapsed: 0,
    retry: null,
  })),
  resetPending: () => set({
    pendingThinking: "", pendingAnswer: "", pendingToolCalls: [],
    activeTool: null, toolProgress: [], currentStep: null, heartbeatElapsed: 0, retry: null,
  }),
  appendAssistantNote: (content) => set((s) => ({
    messages: [...s.messages, {
      role: "assistant" as const, content, thinking: "", toolCalls: [] as never,
    }],
  })),
  setFiles: (f) => set({ files: f }),
  addFiles: (f) => set((s) => ({ files: [...s.files, ...f] })),
  setSessions: (s) => set({ sessions: s }),
  loadFull: (messages, files, sessionId) => set((s) => ({
    messages, files, sessionId,
    // 会话整体替换必须连同流式态一起复位：语音轮挂断/出错后走重载路径，
    // streaming 悬空 true 曾把输入框与电话按钮永久锁死。
    streaming: false, retry: null,
    pendingThinking: "", pendingAnswer: "", pendingToolCalls: [],
    activeTool: null, toolProgress: [], currentStep: null, heartbeatElapsed: 0,
    pendingLibraryRefs: [],
    generation: s.generation + 1,
  })),
  newChat: () => set((s) => ({
    sessionId: null, messages: [], files: [],
    streaming: false, retry: null,
    pendingThinking: "", pendingAnswer: "", pendingToolCalls: [],
    activeTool: null, toolProgress: [], currentStep: null, heartbeatElapsed: 0,
    pendingLibraryRefs: [],
    generation: s.generation + 1,
  })),
}));

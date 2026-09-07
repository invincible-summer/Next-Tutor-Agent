// M-Notes 页面状态：仓库快照 + 当前笔记 + 脏态自动保存 + 每笔记专属智能体。
// 约定与 useChatStore 一致：SSR 安全（不在 initializer 读 localStorage）、
// 流式增量在本地累积、AbortController 挂在 store 上。
import { create } from "zustand";
import type {
  AgentHistory,
  AgentMessage,
  AgentMode,
  NoteDetail,
  NoteSummary,
  VaultSnapshot,
} from "./types-notes";
import { normalizeAgentMode } from "./types-notes";

export type SaveState = "saved" | "dirty" | "saving" | "error" | "conflict";

/** 笔记页布局：AI 面板宽度钳制范围与默认值（仅作用于笔记模块内部）。 */
export const NOTES_LAYOUT_DEFAULTS = {
  rightWidth: 352,
  rightMin: 280,
  rightMax: 560,
} as const;

const LAYOUT_KEY = "edu-agent-notes-layout";

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(n)));
}

function persistLayout(patch: Partial<{ rightWidth: number }>) {
  if (typeof window === "undefined") return;
  const prev = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}") as Record<string, unknown>;
  localStorage.setItem(LAYOUT_KEY, JSON.stringify({ ...prev, ...patch }));
}

interface NotesState {
  // --- layout（笔记页 AI 面板：每次进页默认打开、会话内可折叠，开合不
  //     持久化；只记住拖宽后的右栏宽度） ---
  rightWidth: number;
  focusMode: boolean;
  setRightWidth: (w: number) => void;
  hydrateLayout: () => void;
  setFocusMode: (v: boolean) => void;


  // --- vault snapshot ---
  vault: VaultSnapshot | null;
  vaultError: string;
  vaultLoading: boolean;
  loadVault: () => Promise<void>;

  // --- current note ---
  currentId: string | null;
  detail: NoteDetail | null;
  content: string; // 编辑器缓冲（可能与 detail.content 不同：用户正在改）
  saveState: SaveState;
  saveError: string;
  conflictDetail: NoteDetail | null; // 409 时的服务器最新版
  openNote: (noteId: string | null) => Promise<void>;
  setContent: (content: string) => void;
  saveNow: (extra?: { title?: string }) => Promise<void>;
  reloadCurrent: () => Promise<void>;
  closeNote: () => void;

  // --- ai panel（每笔记专属智能体：2026-09 重构，无线程概念） ---
  agentMode: AgentMode;
  setAgentMode: (m: AgentMode) => void;
  /** 服务端 mode_changed 事件同步（不回写 PATCH，避免循环） */
  applyModeFromServer: (m: string) => void;
  aiPanelOpen: boolean;
  toggleAiPanel: () => void;
  agent: AgentHistory | null;
  agentMessages: AgentMessage[];
  /** 当前智能体对应的存储键：笔记 id，或仓库级 "_vault" */
  agentKey: string;
  loadAgent: (noteKey?: string) => Promise<void>;
  setAgentPlan: (plan: AgentHistory["pending_plan"]) => void;
  clearAgentChat: () => Promise<void>;
  aiStreaming: boolean;
  pendingAnswer: string;
  aiError: string;
  aborter: AbortController | null;

  // --- live updates from agent (SSE) ---
  /** 编辑器脏态时收到的助手更新：置横幅由用户决断，而不是静默跳过/覆盖 */
  pendingRemoteRefresh: { noteId: string; title: string } | null;
  dismissPendingRemoteRefresh: () => void;
  acceptPendingRemoteRefresh: () => Promise<void>;
  applyRemoteUpdate: (noteId: string, content: string, revision: number, title?: string) => void;

  // --- selectors helpers ---
  noteById: (id: string) => NoteSummary | undefined;
}

const EMPTY_VAULT: VaultSnapshot = {
  folders: [],
  notes: [],
  tags: {},
  custom_templates: [],
  stats: {
    note_count: 0,
    folder_count: 0,
    link_count: 0,
    unresolved_links: [],
    due_review_count: 0,
    due_review_ids: [],
  },
};

export const useNotesStore = create<NotesState>((set, get) => ({
  rightWidth: NOTES_LAYOUT_DEFAULTS.rightWidth,
  focusMode: false,
  setRightWidth: (w) => {
    const width = clamp(w, NOTES_LAYOUT_DEFAULTS.rightMin, NOTES_LAYOUT_DEFAULTS.rightMax);
    persistLayout({ rightWidth: width });
    set({ rightWidth: width });
  },
  hydrateLayout: () => {
    if (typeof window === "undefined") return;
    try {
      // 旧版本 localStorage 可能残留 leftOpen/leftWidth/aiPanelOpen 字段，
      // 读取时自然忽略（AI 面板开合已改为每次进页默认打开）
      const raw = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}") as {
        rightWidth?: number;
      };
      const patch: Partial<NotesState> = {};
      if (typeof raw.rightWidth === "number") {
        patch.rightWidth = clamp(raw.rightWidth,
          NOTES_LAYOUT_DEFAULTS.rightMin, NOTES_LAYOUT_DEFAULTS.rightMax);
      }
      if (Object.keys(patch).length > 0) set(patch);
    } catch { /* 损坏的布局 JSON 直接忽略 */ }
  },
  setFocusMode: (v) => set({ focusMode: v }),

  vault: null,
  vaultError: "",
  vaultLoading: false,
  loadVault: async () => {
    set({ vaultLoading: true });
    try {
      const { getVault } = await import("./api-notes");
      const vault = await getVault();
      set({ vault, vaultError: "", vaultLoading: false });
    } catch (e) {
      set({ vaultError: e instanceof Error ? e.message : "加载失败",
           vaultLoading: false });
    }
  },

  currentId: null,
  detail: null,
  content: "",
  saveState: "saved",
  saveError: "",
  conflictDetail: null,
  openNote: async (noteId) => {
    if (!noteId) {
      set({ currentId: null, detail: null, content: "",
            saveState: "saved", saveError: "", conflictDetail: null });
      return;
    }
    try {
      const { getNote } = await import("./api-notes");
      const detail = await getNote(noteId);
      // 切到「另一篇」笔记前若正在编辑，先尽力保存一次。同 id 重开
      // （远程更新/载入最新）绝不自动保存——旧缓冲的 base_revision 已过期，
      // 保存必 409，还会把助手刚写入的内容顶掉。
      const prev = get().detail;
      if (prev && prev.note.id !== noteId && get().saveState === "dirty") {
        await get().saveNow();
      }
      set({ currentId: noteId, detail, content: detail.content,
            saveState: "saved", saveError: "", conflictDetail: null });
    } catch (e) {
      set({ saveError: e instanceof Error ? e.message : "打开失败" });
    }
  },
  setContent: (content) => {
    const detail = get().detail;
    set({ content, saveState: "dirty",
          saveError: "",
          conflictDetail: null,
          detail: detail
            ? { ...detail, note: { ...detail.note,
                word_count: content.replace(/\s/g, "").length } }
            : null });
  },
  saveNow: async (extra) => {
    const { currentId, detail, content } = get();
    if (!currentId || !detail) return;
    set({ saveState: "saving" });
    try {
      const { saveNote } = await import("./api-notes");
      await saveNote(currentId, {
        title: extra?.title ?? detail.note.title,
        content,
        base_revision: detail.note.revision,
      });
      const { getNote } = await import("./api-notes");
      const fresh = await getNote(currentId);
      set({ detail: fresh, saveState: "saved", saveError: "",
            conflictDetail: null });
      void get().loadVault();
    } catch (e) {
      const err = e as Error & { status?: number; body?: {
        note?: NoteSummary; content?: string } };
      if (err.status === 409 && err.body?.note) {
        set({ saveState: "conflict", saveError: err.message,
              conflictDetail: {
                note: err.body.note,
                content: err.body.content || "",
                backlinks: [], links: { resolved: [], unresolved: [] },
                inline_tags: [],
              } });
      } else {
        set({ saveState: "error",
              saveError: err.message || "保存失败" });
      }
    }
  },
  reloadCurrent: async () => {
    const id = get().currentId;
    if (id) await get().openNote(id);
  },
  closeNote: () => {
    get().openNote(null);
  },

  agentMode: "ask",
  setAgentMode: (m) => {
    const mode = normalizeAgentMode(m);
    if (typeof window !== "undefined") {
      localStorage.setItem("edu-agent-notes-mode", mode);
    }
    set({ agentMode: mode });
    // 模式是每笔记智能体的持久状态（服务端枚举校验）
    const key = get().agentKey;
    if (key) {
      void import("./api-notes").then((api) => api.patchNoteAgent(key, mode))
        .catch(() => undefined);
    }
  },
  applyModeFromServer: (m) => {
    const mode = normalizeAgentMode(m);
    if (typeof window !== "undefined") {
      localStorage.setItem("edu-agent-notes-mode", mode);
    }
    const agent = get().agent;
    set({ agentMode: mode,
          agent: agent ? { ...agent, mode } : agent });
  },
  aiPanelOpen: true,
  toggleAiPanel: () => set((s) => ({ aiPanelOpen: !s.aiPanelOpen })),
  agent: null,
  agentMessages: [],
  agentKey: "",
  loadAgent: async (noteKey) => {
    const key = noteKey ?? get().agentKey;
    if (!key) return;
    set({ agentKey: key });
    try {
      const { getNoteAgent } = await import("./api-notes");
      const agent = await getNoteAgent(key);
      set({ agent, agentMessages: agent.messages || [],
            agentMode: normalizeAgentMode(String(agent.mode || "ask")) });
    } catch {
      // 笔记不存在等场景：保持空历史，不阻塞页面
      set({ agent: null, agentMessages: [] });
    }
  },
  setAgentPlan: (plan) => {
    const agent = get().agent;
    if (agent) set({ agent: { ...agent, pending_plan: plan } });
  },
  clearAgentChat: async () => {
    const key = get().agentKey;
    if (!key) return;
    try {
      const { clearNoteAgent } = await import("./api-notes");
      await clearNoteAgent(key);
      await get().loadAgent(key);
    } catch { /* keep old */ }
  },
  aiStreaming: false,
  pendingAnswer: "",
  aiError: "",
  aborter: null,

  pendingRemoteRefresh: null,
  dismissPendingRemoteRefresh: () => set({ pendingRemoteRefresh: null }),
  acceptPendingRemoteRefresh: async () => {
    const pending = get().pendingRemoteRefresh;
    set({ pendingRemoteRefresh: null });
    if (pending && pending.noteId === get().currentId) {
      await get().reloadCurrent();
    }
  },

  applyRemoteUpdate: (noteId, content, revision, title) => {
    const state = get();
    if (state.currentId === noteId) {
      if (state.saveState === "dirty") {
        // 用户正在编辑：不踩输入，也不静默丢弃——置横幅由用户决断
        set({ pendingRemoteRefresh: { noteId, title: title || "" } });
      } else {
        set({ content, saveState: "saved",
              detail: state.detail
                ? { ...state.detail, content,
                    note: { ...state.detail.note, revision } }
                : null,
              pendingRemoteRefresh: null });
      }
    }
    void state.loadVault();
  },

  noteById: (id) =>
    (get().vault || EMPTY_VAULT).notes.find((n) => n.id === id),
}));

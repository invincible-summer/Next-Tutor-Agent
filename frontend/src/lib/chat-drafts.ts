// 聊天草稿仓（plan.md §3.2 末段）：
// 模式切换（对话 ↔ 课堂）与路由卸载会销毁 ChatInput 的 useState，正文和
// 待上传附件随组件一起丢。这里提供 owner+session（新会话用 workspace）键的
// 内存草稿仓，最多 20 项 LRU：
//   - SPA 内切换：正文 + 未上传 File 对象 + 已上传/暂存引用全部保留；
//   - 刷新/重开：只从 sessionStorage 恢复正文和可序列化引用，File bytes
//     绝不序列化，未上传文件提示重新选择；
//   - 发送成功、删除会话、登出清除对应草稿；不自动上传文件换草稿保存。
import type { AttachmentMeta } from "./types";

const SESSION_KEY = "edu-agent-chat-drafts";
const MAX_DRAFTS = 20;

export interface ChatDraft {
  body: string;
  /** SPA 内保留的未上传 File 对象（刷新后丢失，靠 filesMeta 提示重选）。 */
  pendingFiles: File[];
  /** 未上传文件的名字/大小提示（可序列化）。 */
  pendingFileNames: string[];
  /** 已 attach 的资料库引用（可序列化的 AttachmentMeta）。 */
  attachedRefs: AttachmentMeta[];
  /** 新对话里暂存未绑定的资料库引用（id+filename，随 store 类型）。 */
  pendingLibraryRefs: { id: string; filename: string }[];
  updatedAt: number;
}

/** sessionStorage 可序列化子集（不含 File）。 */
interface PersistedDraft {
  body: string;
  pendingFileNames: string[];
  attachedRefs: AttachmentMeta[];
  pendingLibraryRefs: { id: string; filename: string }[];
}

const drafts = new Map<string, ChatDraft>();

export function draftKey(ownerId: string, sessionId: string | null,
  workspaceId: string | null): string {
  // 新会话按工作区分键：同一区的新对话草稿不互串，无工作区归 "bare"。
  const scope = sessionId ?? (workspaceId ? `ws:${workspaceId}` : "bare");
  return `${ownerId || "anon"}::${scope}`;
}

function loadPersisted(): Record<string, PersistedDraft> {
  if (typeof window === "undefined") return {};
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Record<string, PersistedDraft>) : {};
  } catch {
    return {};
  }
}

function persist(): void {
  if (typeof window === "undefined") return;
  try {
    const out: Record<string, PersistedDraft> = {};
    for (const [key, d] of drafts) {
      out[key] = {
        body: d.body,
        pendingFileNames: d.pendingFileNames,
        attachedRefs: d.attachedRefs,
        pendingLibraryRefs: d.pendingLibraryRefs,
      };
    }
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(out));
  } catch { /* 配额满等情况：内存草稿仍然有效 */ }
}

/** 保存/更新草稿（LRU 上限 20：挤掉最旧的非当前键）。 */
export function saveDraft(key: string, draft: Partial<ChatDraft>): void {
  const prev = drafts.get(key);
  const next: ChatDraft = {
    body: draft.body ?? "",
    pendingFiles: draft.pendingFiles ?? [],
    pendingFileNames: draft.pendingFileNames ?? [],
    attachedRefs: draft.attachedRefs ?? [],
    pendingLibraryRefs: draft.pendingLibraryRefs ?? [],
    updatedAt: Date.now(),
  };
  // 全空草稿不占仓位。
  if (!next.body && next.pendingFiles.length === 0 &&
      next.pendingFileNames.length === 0 && next.attachedRefs.length === 0 &&
      next.pendingLibraryRefs.length === 0) {
    drafts.delete(key);
    persist();
    return;
  }
  if (prev) drafts.delete(key);
  drafts.set(key, next);
  while (drafts.size > MAX_DRAFTS) {
    const oldest = drafts.keys().next().value as string | undefined;
    if (oldest === undefined || oldest === key) break;
    drafts.delete(oldest);
  }
  persist();
}

/**
 * 取草稿：内存优先；刷新后内存为空时从 sessionStorage 复原（File 对象
 * 无法复原——pendingFileNames 保留用于提示重新选择）。取出即从仓中
 * 移除（调用方把它装回输入框，成为新的活动编辑状态）。
 */
export function takeDraft(key: string): ChatDraft | null {
  const mem = drafts.get(key);
  if (mem) {
    drafts.delete(key);
    persist();
    return mem;
  }
  const persisted = loadPersisted()[key];
  if (!persisted) return null;
  if (!persisted.body && !persisted.attachedRefs?.length &&
      !persisted.pendingLibraryRefs?.length &&
      !persisted.pendingFileNames?.length) {
    return null;
  }
  return {
    body: persisted.body ?? "",
    pendingFiles: [], // File bytes 不序列化：刷新后需重新选择
    pendingFileNames: persisted.pendingFileNames ?? [],
    attachedRefs: persisted.attachedRefs ?? [],
    pendingLibraryRefs: persisted.pendingLibraryRefs ?? [],
    updatedAt: Date.now(),
  };
}

/** 只读查看（不取出）——用于「草稿已保留」提示等。 */
export function peekDraft(key: string): ChatDraft | null {
  return drafts.get(key) ?? null;
}

export function clearDraft(key: string): void {
  drafts.delete(key);
  const persisted = loadPersisted();
  if (key in persisted) {
    delete persisted[key];
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(persisted));
    } catch { /* ignore */ }
  }
}

/** 登出：清空全部草稿（含 sessionStorage）。 */
export function clearAllDrafts(): void {
  drafts.clear();
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch { /* ignore */ }
}

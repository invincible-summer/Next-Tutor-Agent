/**
 * Notes domain (mirrors `app/api/v1/notes.py`): vault CRUD, folders,
 * revisions, templates, per-note agent chat and SSE generation streams.
 * The backend answers plain string `detail` errors (no envelope); stale
 * saves surface as `ConflictError` (409 with the server note/content on
 * `details`). Exports return binary bytes — platform layers turn them into
 * downloads. `_vault` is the reserved note key for vault-level agent chat.
 */
import type {
  AbortSignalLike,
  FormDataLike,
  RequestOptions,
  Transport,
} from "./types.ts";
import { errorFromResponse } from "./errors.ts";
import { readSseFrames } from "./sse/decoder.ts";

export const VAULT_AGENT_KEY = "_vault";

export interface NoteSummary {
  id: string;
  title: string;
  folder_id: string;
  tags: string[];
  template_id?: string;
  status: string;
  revision: number;
  source?: string;
  review: {
    enabled: boolean;
    next_review_at?: number | null;
    easiness?: number;
    interval?: number;
    repetitions?: number;
  };
  created_at: number | string;
  updated_at: number | string;
  created_by?: string;
  word_count?: number;
  [key: string]: unknown;
}

export interface NoteFolder {
  id: string;
  name: string;
  parent_id: string;
  created_at: number | string;
  updated_at: number | string;
  note_count?: number;
}

export interface NoteResourceLink {
  type: string;
  resource_id?: string;
  url?: string;
  status?: string;
  resolved?: boolean;
  title?: string;
  folder_id?: string;
  folder_name?: string;
  updated_at?: number | string;
  message_count?: number;
}

export interface NoteDetail {
  note: NoteSummary;
  content: string;
  backlinks: NoteSummary[];
  links: {
    resolved: { title: string; note_id: string }[];
    unresolved: string[];
    resources: NoteResourceLink[];
  };
  inline_tags: string[];
}

export interface VaultStats {
  note_count: number;
  folder_count: number;
  link_count: number;
  unresolved_links: string[];
  due_review_count: number;
  due_review_ids: string[];
}

export interface VaultSnapshot {
  folders: (NoteFolder & { note_count: number })[];
  notes: NoteSummary[];
  tags: Record<string, number>;
  custom_templates: { id: string; name: string; content: string; created_at: number | string }[];
  stats: VaultStats;
}

export interface NotesGraphNode {
  id: string;
  title: string;
  kind: string;
  folder_id?: string;
  folder_name?: string;
  tags?: string[];
  ghost?: boolean;
  status?: string;
  updated_at?: number | string;
  [key: string]: unknown;
}

export interface NoteCreatePayload {
  title?: string;
  folder_id?: string;
  template_id?: string;
  content?: string;
  tags?: string[];
  review_enabled?: boolean | null;
  status?: string;
}

export interface NoteSavePayload {
  title?: string;
  content: string;
  /** Optimistic concurrency; stale values answer 409. */
  base_revision?: number | null;
  summary?: string;
}

export interface NotePatchPayload {
  title?: string;
  folder_id?: string | null;
  tags?: string[] | null;
  status?: string;
  review_enabled?: boolean | null;
}

export interface NoteRevisionMeta {
  revision: number;
  ts: number;
  author: string;
  word_count: number;
}

export interface AgentMessage {
  role: "user" | "assistant";
  content: string;
  context?: unknown;
  ts?: number;
}

export type NotesAgentMode = "ask" | "plan" | "authorize";

export interface AgentHistory {
  note_id: string;
  mode: NotesAgentMode;
  messages: AgentMessage[];
  pending_plan: Record<string, unknown> | null;
  working: { stage: string; tool?: string; started_at?: number; can_stop?: boolean; run_id?: string };
  created_at: number;
  updated_at: number;
  modes: NotesAgentMode[];
}

/** One SSE frame from the generate/chat streams: event name wins over body type. */
export interface NotesStreamEvent {
  type: string;
  [key: string]: unknown;
}

export interface NotesGeneratePayload {
  template_id: string;
  sources?: {
    source_mode?: "sessions" | "workspace" | "textbooks";
    workspace_id?: string;
    session_ids?: string[];
    textbook_ids?: string[];
    use_error_notebook?: boolean;
  };
  target?: { folder_id?: string; title?: string };
  instructions?: string;
}

export interface NotesChatPayload {
  message: string;
  context?: { note_id?: string; scope?: string };
  mode?: string;
  action?: "" | "approve_plan" | "reject_plan";
  attachments?: { id: string; filename: string }[];
}

export interface NotesUploadResult {
  results: (
    | { id: string; filename: string; char_count: number; chunk_count: number; ocr_used?: boolean; preview_text?: string }
    | { filename: string; error: string; warning?: string }
  )[];
}

export interface NotesClient {
  vault(signal?: AbortSignalLike | null): Promise<VaultSnapshot>;
  search(q: string, signal?: AbortSignalLike | null): Promise<{ results: NoteSummary[] }>;
  graph(signal?: AbortSignalLike | null): Promise<{ nodes: NotesGraphNode[]; edges: { source: string; target: string; title?: string; resolved?: boolean; kind?: string }[] }>;
  createNote(payload: NoteCreatePayload): Promise<{ note: NoteSummary }>;
  getNote(noteId: string, signal?: AbortSignalLike | null): Promise<NoteDetail>;
  saveNote(noteId: string, payload: NoteSavePayload): Promise<{ note: NoteSummary }>;
  patchNote(noteId: string, patch: NotePatchPayload): Promise<{ note: NoteSummary; links_rewritten?: number }>;
  deleteNote(noteId: string): Promise<{ status: string }>;
  createFolder(name: string, parentId?: string): Promise<{ folder: NoteFolder }>;
  renameFolder(folderId: string, patch: { name?: string | null; parent_id?: string | null }): Promise<void>;
  deleteFolder(folderId: string): Promise<{ status: string; moved_notes: number; moved_to_unfiled: number }>;
  bulkMove(noteIds: string[], folderId?: string): Promise<{ status: string; moved: string[]; missing: string[] }>;
  bulkDelete(noteIds: string[]): Promise<{ status: string; archived: unknown[]; missing: string[] }>;
  listRevisions(noteId: string): Promise<{ revisions: NoteRevisionMeta[] }>;
  readRevision(noteId: string, revision: number): Promise<{ revision: number; content: string }>;
  restoreRevision(noteId: string, revision: number): Promise<{ note: NoteSummary }>;
  templates(signal?: AbortSignalLike | null): Promise<{ templates: Record<string, unknown>[] }>;
  createTemplate(name: string, content?: string): Promise<{ template: { id: string; name: string; content: string; created_at: number | string; builtin: boolean } }>;
  deleteTemplate(templateId: string): Promise<void>;
  /** `noteKey` defaults to `_vault` for vault-level chat. */
  getAgent(noteKey?: string, signal?: AbortSignalLike | null): Promise<AgentHistory>;
  patchAgentMode(noteKey: string, mode: NotesAgentMode): Promise<{ note_id: string; mode: NotesAgentMode }>;
  clearAgent(noteKey: string): Promise<void>;
  submitReview(noteId: string, quality: number): Promise<{ review: NonNullable<NoteSummary["review"]>; note: NoteSummary }>;
  dueReviews(signal?: AbortSignalLike | null): Promise<{ due: { note: NoteSummary; card: Record<string, unknown> }[] }>;
  /** GET /notes/notes/{id}/export — markdown bytes. */
  exportNote(noteId: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;
  /** GET /notes/export — vault (or folder) zip bytes. */
  exportVault(folderId?: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;
  /** POST /notes/upload — multipart `files`; caller appends platform file parts. */
  upload(form: FormDataLike): Promise<NotesUploadResult>;
  /** POST /notes/generate — SSE until the agent finishes. */
  generateStream(payload: NotesGeneratePayload, signal?: AbortSignalLike | null): AsyncGenerator<NotesStreamEvent>;
  /** POST /notes/chat/stream — SSE agent chat. */
  chatStream(payload: NotesChatPayload, signal?: AbortSignalLike | null): AsyncGenerator<NotesStreamEvent>;
}

export function createNotesClient(transport: Transport): NotesClient {
  const noteBase = (noteId: string) => `/notes/notes/${encodeURIComponent(noteId)}`;
  const agentBase = (noteKey: string) => `${noteBase(noteKey)}/agent`;
  const stream = async function* (
    path: string,
    json: unknown,
    signal: AbortSignalLike | null | undefined,
  ): AsyncGenerator<NotesStreamEvent> {
    const response = await transport.raw(path, {
      method: "POST",
      json,
      headers: { Accept: "text/event-stream" },
      signal: signal ?? null,
    } satisfies RequestOptions);
    if (!response.ok || !response.body) throw await errorFromResponse(response);
    for await (const frame of readSseFrames(response.body, { signal })) {
      let data: Record<string, unknown>;
      try {
        const parsed: unknown = JSON.parse(frame.data);
        data = typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : { data: parsed };
      } catch {
        data = { message: frame.data };
      }
      yield {
        ...data,
        // Event name wins over any body `type` (chat SSE convention).
        type: frame.event ?? (typeof data["type"] === "string" ? (data["type"] as string) : "message"),
      };
    }
  };
  return {
    vault: (signal) =>
      transport.request<VaultSnapshot>("/notes/vault", { signal }).then((result) => result.body),
    search: (q, signal) =>
      transport.request<{ results: NoteSummary[] }>("/notes/search", { query: { q }, signal })
        .then((result) => result.body),
    graph: (signal) =>
      transport.request<{ nodes: NotesGraphNode[]; edges: { source: string; target: string; title?: string; resolved?: boolean; kind?: string }[] }>(
        "/notes/graph",
        { signal },
      ).then((result) => result.body),
    createNote: (payload) =>
      transport.request<{ note: NoteSummary }>("/notes/notes", { method: "POST", json: payload })
        .then((result) => result.body),
    getNote: (noteId, signal) =>
      transport.request<NoteDetail>(noteBase(noteId), { signal }).then((result) => result.body),
    saveNote: (noteId, payload) =>
      transport.request<{ note: NoteSummary }>(noteBase(noteId), { method: "PUT", json: payload })
        .then((result) => result.body),
    patchNote: (noteId, patch) =>
      transport.request<{ note: NoteSummary; links_rewritten?: number }>(noteBase(noteId), {
        method: "PATCH",
        json: patch,
      }).then((result) => result.body),
    deleteNote: (noteId) =>
      transport.request<{ status: string }>(noteBase(noteId), { method: "DELETE" })
        .then((result) => result.body),
    createFolder: (name, parentId = "") =>
      transport.request<{ folder: NoteFolder }>("/notes/folders", {
        method: "POST",
        json: { name, parent_id: parentId },
      }).then((result) => result.body),
    renameFolder: (folderId, patch) =>
      transport
        .request(`/notes/folders/${encodeURIComponent(folderId)}`, {
          method: "PATCH",
          json: patch,
          responseType: "none",
        })
        .then(() => undefined),
    deleteFolder: (folderId) =>
      transport
        .request<{ status: string; moved_notes: number; moved_to_unfiled: number }>(
          `/notes/folders/${encodeURIComponent(folderId)}`,
          { method: "DELETE" },
        )
        .then((result) => result.body),
    bulkMove: (noteIds, folderId = "") =>
      transport
        .request<{ status: string; moved: string[]; missing: string[] }>("/notes/bulk/move", {
          method: "POST",
          json: { note_ids: noteIds, folder_id: folderId },
        })
        .then((result) => result.body),
    bulkDelete: (noteIds) =>
      transport
        .request<{ status: string; archived: unknown[]; missing: string[] }>("/notes/bulk/delete", {
          method: "POST",
          json: { note_ids: noteIds },
        })
        .then((result) => result.body),
    listRevisions: (noteId) =>
      transport
        .request<{ revisions: NoteRevisionMeta[] }>(`${noteBase(noteId)}/revisions`)
        .then((result) => result.body),
    readRevision: (noteId, revision) =>
      transport
        .request<{ revision: number; content: string }>(
          `${noteBase(noteId)}/revisions/${encodeURIComponent(String(revision))}`,
        )
        .then((result) => result.body),
    restoreRevision: (noteId, revision) =>
      transport
        .request<{ note: NoteSummary }>(
          `${noteBase(noteId)}/revisions/${encodeURIComponent(String(revision))}/restore`,
          { method: "POST" },
        )
        .then((result) => result.body),
    templates: (signal) =>
      transport.request<{ templates: Record<string, unknown>[] }>("/notes/templates", { signal })
        .then((result) => result.body),
    createTemplate: (name, content = "") =>
      transport
        .request<{ template: { id: string; name: string; content: string; created_at: number | string; builtin: boolean } }>(
          "/notes/templates",
          { method: "POST", json: { name, content } },
        )
        .then((result) => result.body),
    deleteTemplate: (templateId) =>
      transport
        .request(`/notes/templates/${encodeURIComponent(templateId)}`, {
          method: "DELETE",
          responseType: "none",
        })
        .then(() => undefined),
    getAgent: (noteKey = VAULT_AGENT_KEY, signal) =>
      transport.request<AgentHistory>(agentBase(noteKey), { signal }).then((result) => result.body),
    patchAgentMode: (noteKey, mode) =>
      transport
        .request<{ note_id: string; mode: NotesAgentMode }>(agentBase(noteKey), {
          method: "PATCH",
          json: { mode },
        })
        .then((result) => result.body),
    clearAgent: (noteKey) =>
      transport
        .request(agentBase(noteKey), { method: "DELETE", responseType: "none" })
        .then(() => undefined),
    submitReview: (noteId, quality) =>
      transport
        .request<{ review: NonNullable<NoteSummary["review"]>; note: NoteSummary }>(
          `${noteBase(noteId)}/review`,
          { method: "POST", json: { quality } },
        )
        .then((result) => result.body),
    dueReviews: (signal) =>
      transport
        .request<{ due: { note: NoteSummary; card: Record<string, unknown> }[] }>("/notes/reviews/due", {
          signal,
        })
        .then((result) => result.body),
    exportNote: (noteId, signal) =>
      transport
        .request<ArrayBuffer>(`${noteBase(noteId)}/export`, { responseType: "bytes", signal })
        .then((result) => result.body),
    exportVault: (folderId, signal) =>
      transport
        .request<ArrayBuffer>("/notes/export", {
          responseType: "bytes",
          query: folderId ? { folder_id: folderId } : undefined,
          signal,
        })
        .then((result) => result.body),
    upload: (form) =>
      transport
        .request<NotesUploadResult>("/notes/upload", { method: "POST", body: form })
        .then((result) => result.body),
    generateStream: (payload, signal) => stream("/notes/generate", payload, signal),
    chatStream: (payload, signal) => stream("/notes/chat/stream", payload, signal),
  };
}

/**
 * Library domain (mirrors `app/api/v1/library.py` plus the textbook
 * registry `app/api/v1/textbook.py`): personal material folders/files and
 * the 教材库 (textbooks = library files + registry + graph). Textbook
 * uploads accept a platform FormData the caller fills with `files` parts;
 * this module injects the scalar form fields (`level`, `scope`, …) so the
 * wire names stay shared. Downloads resolve to bytes — platform layers
 * turn them into files/blob URLs.
 */
import type {
  AbortSignalLike,
  FormDataLike,
  Transport,
} from "./types.ts";

export interface LibraryFolder {
  id: string;
  name: string;
  workspace_id?: string;
  file_count?: number;
  created_at: number | string;
  updated_at: number | string;
}

export interface LibraryFile {
  id: string;
  filename: string;
  original_filename?: string;
  folder_id: string;
  char_count?: number;
  chunk_count?: number;
  summary?: string;
  topics?: string[];
  has_original?: boolean;
  created_at?: number | string;
  updated_at?: number | string;
  [key: string]: unknown;
}

export interface LibraryTree {
  folders: LibraryFolder[];
  files: LibraryFile[];
}

export type LibraryUploadOutcome =
  | { id: string; filename: string; folder_id: string; char_count: number; chunk_count: number }
  | { filename: string; error: string };

export type TextbookRefreshMode = "rag_graph" | "full_ocr" | "quality_ocr" | "graph_only";

export interface VolumeCoverage {
  file_id?: string;
  name?: string;
  chapter_count?: number;
  section_count?: number;
  concept_count?: number;
  [key: string]: unknown;
}

export interface TextbookVolume {
  file_id: string;
  filename: string;
  original_filename?: string;
  char_count?: number;
  has_original?: boolean;
  updated_at?: number | string;
  effective_limits?: Record<string, number>;
  coverage?: VolumeCoverage[];
  [key: string]: unknown;
}

export interface TextbookListItem {
  id: string;
  kind?: "single" | "group";
  file_id?: string;
  file_ids?: string[];
  title: string;
  group_name?: string;
  group_note?: string;
  subject?: string;
  level?: string;
  scope?: "private" | "public";
  status?: string;
  progress?: Record<string, unknown>;
  chapter_count?: number;
  concept_count?: number;
  coverage?: VolumeCoverage[];
  warnings?: string[];
  error?: string;
  created_at?: number | string;
  updated_at?: number | string;
  volumes?: TextbookVolume[];
  [key: string]: unknown;
}

export interface TextbookOutlineChapter {
  chapter: string;
  concept_count: number;
  concepts: string[];
}

export interface TextbookPatch {
  title?: string;
  group_name?: string;
  group_note?: string;
  subject?: string;
  level?: string;
}

export interface TextbookGraphPolicy {
  default_max_chapters?: number;
  default_max_concepts?: number;
  volume_overrides: Record<string, { max_chapters?: number; max_concepts?: number }>;
}

export interface TextbookUploadFields {
  level?: string;
  scope?: "private" | "public";
  subject?: string;
  group?: string;
  groupNote?: string;
  groupId?: string;
  defaultMaxChapters?: number;
  defaultMaxConcepts?: number;
  volumeOverrides?: TextbookGraphPolicy["volume_overrides"];
}

export type TextbookUploadOutcome =
  | { filename: string; status: "building"; group_id: string; id: string }
  | { filename: string; error: string };

export interface TextbookClient {
  list(signal?: AbortSignalLike | null): Promise<{ textbooks: TextbookListItem[] }>;
  get(textbookId: string, signal?: AbortSignalLike | null): Promise<{ textbook: TextbookListItem; outline: TextbookOutlineChapter[] }>;
  figureStatus(textbookId: string, signal?: AbortSignalLike | null): Promise<{ status: string; has_markers: boolean; volumes: { file_id: string; filename: string; has_markers: boolean }[] }>;
  quality(textbookId: string, signal?: AbortSignalLike | null): Promise<Record<string, unknown>>;
  patch(textbookId: string, patch: TextbookPatch): Promise<{ textbook: TextbookListItem }>;
  patchVolume(textbookId: string, fileId: string, filename: string): Promise<{ status: string; file_id: string; filename: string }>;
  removeVolume(groupId: string, fileId: string): Promise<{ status: string; group_id?: string; remaining?: string[]; empty?: boolean; trash_item?: unknown }>;
  rebuildGraph(textbookId: string, mode?: TextbookRefreshMode): Promise<{ textbook_id: string; status: string; mode: string }>;
  cancel(textbookId: string): Promise<{ status: string; textbook_id: string; record_status?: string }>;
  archive(textbookId: string): Promise<{ status: string; textbook_id: string; trash_item?: unknown }>;
  bulkRebuild(ids: string[], mode?: TextbookRefreshMode): Promise<{ status: string; mode: string; count: number; results: Record<string, unknown>[] }>;
  bulkCancel(ids: string[]): Promise<{ status: string; count: number; results: Record<string, unknown>[] }>;
  graphPolicy(textbookId: string, signal?: AbortSignalLike | null): Promise<{ textbook_id: string; scope?: string; graph_policy: TextbookGraphPolicy; volumes?: unknown[] }>;
  setGraphPolicy(textbookId: string, policy: TextbookGraphPolicy): Promise<{ status: string; textbook_id: string; graph_policy: TextbookGraphPolicy; mode?: string }>;
  /** POST /textbooks/upload — caller appends `files` parts; scalar fields injected here. */
  upload(form: FormDataLike, fields?: TextbookUploadFields): Promise<{ results: TextbookUploadOutcome[] }>;
  /** GET /textbooks/{id}/download — original file bytes. */
  download(textbookId: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;
  /** GET /textbooks/{group}/volumes/{file}/download — volume original bytes. */
  downloadVolume(groupId: string, fileId: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;
}

export interface LibraryClient {
  list(signal?: AbortSignalLike | null): Promise<LibraryTree>;
  createFolder(name: string): Promise<{ folder: LibraryFolder }>;
  renameFolder(folderId: string, name: string): Promise<{ folder: LibraryFolder }>;
  /** 404 unless empty; workspace-owned folders are renamed via the workspace. */
  deleteFolder(folderId: string): Promise<{ status: string; folder_id: string; trash_item?: unknown }>;
  /** POST /library/upload?folder_id= — caller appends `files` parts. */
  upload(form: FormDataLike, folderId?: string): Promise<{ results: LibraryUploadOutcome[] }>;
  renameFile(fileId: string, filename: string): Promise<{ file: LibraryFile }>;
  moveFile(fileId: string, folderId?: string): Promise<{ status: string; file_id: string; folder_id: string }>;
  deleteFile(fileId: string): Promise<{ status: string; file_id: string; trash_item?: unknown }>;
  /** GET /library/files/{id}/download — original bytes (absent for legacy uploads → 404). */
  downloadFile(fileId: string, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;
  /** GET /library/files/{id}/page/{n} — rendered PDF page PNG bytes. */
  filePage(fileId: string, page: number, signal?: AbortSignalLike | null): Promise<ArrayBuffer>;
  textbooks: TextbookClient;
}

export function createLibraryClient(transport: Transport): LibraryClient {
  return {
    list: (signal) =>
      transport.request<LibraryTree>("/library", { signal }).then((result) => result.body),
    createFolder: (name) =>
      transport.request<{ folder: LibraryFolder }>("/library/folders", { method: "POST", json: { name } })
        .then((result) => result.body),
    renameFolder: (folderId, name) =>
      transport
        .request<{ folder: LibraryFolder }>(`/library/folders/${encodeURIComponent(folderId)}`, {
          method: "PATCH",
          json: { name },
        })
        .then((result) => result.body),
    deleteFolder: (folderId) =>
      transport
        .request<{ status: string; folder_id: string; trash_item?: unknown }>(
          `/library/folders/${encodeURIComponent(folderId)}`,
          { method: "DELETE" },
        )
        .then((result) => result.body),
    upload: (form, folderId = "") =>
      transport
        .request<{ results: LibraryUploadOutcome[] }>("/library/upload", {
          method: "POST",
          body: form,
          query: { folder_id: folderId },
        })
        .then((result) => result.body),
    renameFile: (fileId, filename) =>
      transport
        .request<{ file: LibraryFile }>(`/library/files/${encodeURIComponent(fileId)}`, {
          method: "PATCH",
          json: { filename },
        })
        .then((result) => result.body),
    moveFile: (fileId, folderId = "") =>
      transport
        .request<{ status: string; file_id: string; folder_id: string }>(
          `/library/files/${encodeURIComponent(fileId)}/move`,
          { method: "POST", json: { folder_id: folderId } },
        )
        .then((result) => result.body),
    deleteFile: (fileId) =>
      transport
        .request<{ status: string; file_id: string; trash_item?: unknown }>(
          `/library/files/${encodeURIComponent(fileId)}`,
          { method: "DELETE" },
        )
        .then((result) => result.body),
    downloadFile: (fileId, signal) =>
      transport
        .request<ArrayBuffer>(`/library/files/${encodeURIComponent(fileId)}/download`, {
          responseType: "bytes",
          signal,
        })
        .then((result) => result.body),
    filePage: (fileId, page, signal) =>
      transport
        .request<ArrayBuffer>(`/library/files/${encodeURIComponent(fileId)}/page/${encodeURIComponent(String(page))}`, {
          responseType: "bytes",
          signal,
        })
        .then((result) => result.body),
    textbooks: {
      list: (signal) =>
        transport.request<{ textbooks: TextbookListItem[] }>("/textbooks", { signal })
          .then((result) => result.body),
      get: (textbookId, signal) =>
        transport
          .request<{ textbook: TextbookListItem; outline: TextbookOutlineChapter[] }>(
            `/textbooks/${encodeURIComponent(textbookId)}`,
            { signal },
          )
          .then((result) => result.body),
      figureStatus: (textbookId, signal) =>
        transport
          .request<{ status: string; has_markers: boolean; volumes: { file_id: string; filename: string; has_markers: boolean }[] }>(
            `/textbooks/${encodeURIComponent(textbookId)}/figure-status`,
            { signal },
          )
          .then((result) => result.body),
      quality: (textbookId, signal) =>
        transport
          .request<Record<string, unknown>>(`/textbooks/${encodeURIComponent(textbookId)}/quality`, { signal })
          .then((result) => result.body),
      patch: (textbookId, patch) =>
        transport
          .request<{ textbook: TextbookListItem }>(`/textbooks/${encodeURIComponent(textbookId)}`, {
            method: "PATCH",
            json: patch,
          })
          .then((result) => result.body),
      patchVolume: (textbookId, fileId, filename) =>
        transport
          .request<{ status: string; file_id: string; filename: string }>(
            `/textbooks/${encodeURIComponent(textbookId)}/volumes/${encodeURIComponent(fileId)}`,
            { method: "PATCH", json: { filename } },
          )
          .then((result) => result.body),
      removeVolume: (groupId, fileId) =>
        transport
          .request<{ status: string; group_id?: string; remaining?: string[]; empty?: boolean; trash_item?: unknown }>(
            `/textbooks/${encodeURIComponent(groupId)}/volumes/${encodeURIComponent(fileId)}`,
            { method: "DELETE" },
          )
          .then((result) => result.body),
      rebuildGraph: (textbookId, mode = "rag_graph") =>
        transport
          .request<{ textbook_id: string; status: string; mode: string }>(
            `/textbooks/${encodeURIComponent(textbookId)}/rebuild_graph`,
            { method: "POST", json: { mode } },
          )
          .then((result) => result.body),
      cancel: (textbookId) =>
        transport
          .request<{ status: string; textbook_id: string; record_status?: string }>(
            `/textbooks/${encodeURIComponent(textbookId)}/cancel`,
            { method: "POST" },
          )
          .then((result) => result.body),
      archive: (textbookId) =>
        transport
          .request<{ status: string; textbook_id: string; trash_item?: unknown }>(
            `/textbooks/${encodeURIComponent(textbookId)}`,
            { method: "DELETE" },
          )
          .then((result) => result.body),
      bulkRebuild: (ids, mode = "rag_graph") =>
        transport
          .request<{ status: string; mode: string; count: number; results: Record<string, unknown>[] }>(
            "/textbooks/bulk/rebuild",
            { method: "POST", json: { ids, mode } },
          )
          .then((result) => result.body),
      bulkCancel: (ids) =>
        transport
          .request<{ status: string; count: number; results: Record<string, unknown>[] }>(
            "/textbooks/bulk/cancel",
            { method: "POST", json: { ids } },
          )
          .then((result) => result.body),
      graphPolicy: (textbookId, signal) =>
        transport
          .request<{ textbook_id: string; scope?: string; graph_policy: TextbookGraphPolicy; volumes?: unknown[] }>(
            `/textbooks/${encodeURIComponent(textbookId)}/graph-policy`,
            { signal },
          )
          .then((result) => result.body),
      setGraphPolicy: (textbookId, policy) =>
        transport
          .request<{ status: string; textbook_id: string; graph_policy: TextbookGraphPolicy; mode?: string }>(
            `/textbooks/${encodeURIComponent(textbookId)}/graph-policy`,
            { method: "PUT", json: policy },
          )
          .then((result) => result.body),
      upload: (form, fields = {}) => {
        const wire: Array<[string, string]> = [
          ["level", fields.level ?? "其他"],
          ["scope", fields.scope ?? "private"],
          ["subject", fields.subject ?? ""],
          ["group", fields.group ?? ""],
          ["group_note", fields.groupNote ?? ""],
          ["group_id", fields.groupId ?? ""],
        ];
        if (fields.defaultMaxChapters !== undefined) {
          wire.push(["default_max_chapters", String(fields.defaultMaxChapters)]);
        }
        if (fields.defaultMaxConcepts !== undefined) {
          wire.push(["default_max_concepts", String(fields.defaultMaxConcepts)]);
        }
        if (fields.volumeOverrides !== undefined) {
          wire.push(["volume_overrides", JSON.stringify(fields.volumeOverrides)]);
        }
        for (const [name, value] of wire) form.append(name, value);
        return transport
          .request<{ results: TextbookUploadOutcome[] }>("/textbooks/upload", {
            method: "POST",
            body: form,
          })
          .then((result) => result.body);
      },
      download: (textbookId, signal) =>
        transport
          .request<ArrayBuffer>(`/textbooks/${encodeURIComponent(textbookId)}/download`, {
            responseType: "bytes",
            signal,
          })
          .then((result) => result.body),
      downloadVolume: (groupId, fileId, signal) =>
        transport
          .request<ArrayBuffer>(
            `/textbooks/${encodeURIComponent(groupId)}/volumes/${encodeURIComponent(fileId)}/download`,
            { responseType: "bytes", signal },
          )
          .then((result) => result.body),
    },
  };
}

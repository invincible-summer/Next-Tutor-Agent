/**
 * Workspace domain: workspace CRUD and session membership. JSON methods are
 * fully shared; the multipart upload accepts a platform-built FormData
 * (`FormDataLike`) so browser `File` and RN `{uri,name,type}` payloads stay
 * platform concerns.
 */
import type { AbortSignalLike, FormDataLike, Transport } from "./types.ts";

export interface CreateWorkspaceResponse {
  workspace_id: string;
  name: string;
  library_folder_id?: string;
}

export interface WorkspacePatch {
  name?: string;
  folder_ids?: string[];
  file_ids?: string[];
}

export interface WorkspaceClient {
  list<T = { workspaces: unknown[] }>(): Promise<T>;
  get<T = unknown>(id: string, signal?: AbortSignalLike | null): Promise<T>;
  create(
    name: string,
    folderIds?: string[],
    fileIds?: string[],
  ): Promise<CreateWorkspaceResponse>;
  update(id: string, patch: WorkspacePatch): Promise<void>;
  /** PATCH shorthand carrying only `{name}` (rename UI). */
  rename(id: string, name: string): Promise<void>;
  remove(id: string): Promise<void>;
  moveSession(workspaceId: string, sessionId: string): Promise<void>;
  removeSession(workspaceId: string, sessionId: string): Promise<void>;
  uploadFiles<T = unknown>(workspaceId: string, form: FormDataLike): Promise<T>;
}

export function createWorkspaceClient(transport: Transport): WorkspaceClient {
  return {
    list: <T>() => transport.request<T>("/workspaces").then((result) => result.body),
    get: <T>(id: string, signal?: AbortSignalLike | null) =>
      transport
        .request<T>(`/workspaces/${encodeURIComponent(id)}`, { signal })
        .then((result) => result.body),
    create: (name, folderIds = [], fileIds = []) =>
      transport
        .request<CreateWorkspaceResponse>("/workspaces", {
          method: "POST",
          json: { name, folder_ids: folderIds, file_ids: fileIds },
        })
        .then((result) => result.body),
    update: (id, patch) =>
      transport
        .request(`/workspaces/${encodeURIComponent(id)}`, {
          method: "PATCH",
          json: patch,
          responseType: "none",
        })
        .then(() => undefined),
    rename: (id, name) =>
      transport
        .request(`/workspaces/${encodeURIComponent(id)}`, {
          method: "PATCH",
          json: { name },
          responseType: "none",
        })
        .then(() => undefined),
    remove: (id) =>
      transport
        .request(`/workspaces/${encodeURIComponent(id)}`, {
          method: "DELETE",
          responseType: "none",
        })
        .then(() => undefined),
    moveSession: (workspaceId, sessionId) =>
      transport
        .request(`/workspaces/${encodeURIComponent(workspaceId)}/sessions`, {
          method: "POST",
          json: { session_id: sessionId },
          responseType: "none",
        })
        .then(() => undefined),
    removeSession: (workspaceId, sessionId) =>
      transport
        .request(
          `/workspaces/${encodeURIComponent(workspaceId)}/sessions/${encodeURIComponent(sessionId)}`,
          { method: "DELETE", responseType: "none" },
        )
        .then(() => undefined),
    uploadFiles: <T>(workspaceId: string, form: FormDataLike) =>
      transport
        .request<T>(`/workspaces/${encodeURIComponent(workspaceId)}/upload`, {
          method: "POST",
          body: form,
        })
        .then((result) => result.body),
  };
}

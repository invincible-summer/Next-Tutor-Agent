/**
 * Archive domain (mirrors `app/api/v1/trash.py`): the unified recycle bin —
 * the UI route is `/archive`, the API namespace `/trash`. Lists archived
 * resources (optionally filtered by `resource_type`), restores an item into
 * chosen workspaces, purges items permanently, empties the bin and
 * reads/writes the per-user retention policy. The backend answers plain
 * string `detail` errors (404 unknown item, 409 restore name collision),
 * surfaced by the transport as `ApiError` / `ConflictError`.
 */
import type { AbortSignalLike, Transport } from "./types.ts";

export type TrashResourceType =
  | "session"
  | "library_file"
  | "library_folder"
  | "textbook"
  | "textbook_volume"
  | "workspace"
  | "knowledge_graph"
  | "notes_note"
  | "classroom_lesson"
  | (string & {});

export interface TrashItem {
  id: string;
  resource_type: TrashResourceType;
  original_id: string;
  title: string;
  deleted_at: number;
  deleted_at_iso: string;
  expires_at: number | null;
  retention_days: number;
  size_bytes?: number;
  /** Project-relative, user-visible recycle-bin location (never a host path). */
  archive_location?: string;
  version: number;
  metadata: {
    workspace_id?: string;
    workspace_ids?: string[];
    file_ids?: string[];
    topic_key?: string;
    session_count?: number;
    file_count?: number;
    round_count?: number;
    has_public_memory?: boolean;
    memory_forget_status?: "recent" | "compacted" | "legacy_unknown" | "none" | "unavailable";
    memory_forget_result?: string;
    [key: string]: unknown;
  };
}

export interface TrashListResponse {
  status: string;
  items: TrashItem[];
}

export interface TrashPolicy {
  default_days: number;
  user_max_days: number;
  forced_max_days: number;
  mode: "auto" | "manual";
  cleanup_interval_seconds: number;
  retention_days: number;
  can_keep_manually: boolean;
}

export interface TrashRestoreResult {
  status: string;
  item_id: string;
  resource_type: string;
  original_id: string;
  /** Textbook that resumes building after the restore, when applicable. */
  textbook_id: string | null;
  [key: string]: unknown;
}

export interface TrashPurgeResult {
  status: string;
  item_id: string;
  memory_forget: Record<string, number>;
  source_attribution_detached: Record<string, number>;
  [key: string]: unknown;
}

export interface TrashEmptyResult {
  status: string;
  purged: number;
  failed: string[];
}

export interface ArchiveClient {
  /** GET /trash — archived items; `resourceType` filters when non-empty. */
  list(resourceType?: TrashResourceType, signal?: AbortSignalLike | null): Promise<TrashListResponse>;
  /** GET /trash/{itemId} — single item manifest (unwrapped from the envelope). */
  get(itemId: string, signal?: AbortSignalLike | null): Promise<TrashItem>;
  /** POST /trash/{itemId}/restore — restore into the chosen workspaces. */
  restore(itemId: string, workspaceIds?: string[]): Promise<TrashRestoreResult>;
  /** DELETE /trash/{itemId} — permanent delete of one item. */
  purge(itemId: string): Promise<TrashPurgeResult>;
  /** DELETE /trash — purge every item; per-item failures land in `failed`. */
  empty(): Promise<TrashEmptyResult>;
  /** GET /trash/policy — effective retention policy for the caller. */
  getPolicy(signal?: AbortSignalLike | null): Promise<TrashPolicy>;
  /** PUT /trash/policy — set retention days (server clamps to the allowed range). */
  setPolicy(retentionDays: number): Promise<TrashPolicy>;
}

export function createArchiveClient(transport: Transport): ArchiveClient {
  const itemBase = (itemId: string) => `/trash/${encodeURIComponent(itemId)}`;
  return {
    list: (resourceType, signal) =>
      transport
        .request<TrashListResponse>("/trash", {
          query: resourceType ? { resource_type: resourceType } : undefined,
          signal,
        })
        .then((result) => result.body),
    get: (itemId, signal) =>
      transport
        .request<{ status: string; item: TrashItem }>(itemBase(itemId), { signal })
        .then((result) => result.body.item),
    restore: (itemId, workspaceIds = []) =>
      transport
        .request<TrashRestoreResult>(`${itemBase(itemId)}/restore`, {
          method: "POST",
          json: { workspace_ids: workspaceIds },
        })
        .then((result) => result.body),
    purge: (itemId) =>
      transport
        .request<TrashPurgeResult>(itemBase(itemId), { method: "DELETE" })
        .then((result) => result.body),
    empty: () =>
      transport
        .request<TrashEmptyResult>("/trash", { method: "DELETE" })
        .then((result) => result.body),
    getPolicy: (signal) =>
      transport
        .request<TrashPolicy>("/trash/policy", { signal })
        .then((result) => result.body),
    setPolicy: (retentionDays) =>
      transport
        .request<TrashPolicy>("/trash/policy", {
          method: "PUT",
          json: { retention_days: retentionDays },
        })
        .then((result) => result.body),
  };
}

import type { ChatSSEEvent, SessionDetail, SessionItem, AttachmentMeta, LibraryFolder, LibraryFile, LibraryTree, WorkspaceDetail, WorkspaceItem, QuizSourceRef } from "./types";
import { apiFetch } from "./api-fetch";

// Single source of truth for the API origin. Covers all three deployment
// shapes without code changes:
//
//   1. Dev (start.sh):        NEXT_PUBLIC_BACKEND_URL=http://localhost:PORT  -> direct
//   2. Prod same-origin:      env unset                                       -> relative "/api/v1"
//      (nginx/CDN serves the built frontend and reverse-proxies /api/* to the
//       backend; no origin knowledge needed on the client)
//   3. Prod cross-origin:     NEXT_PUBLIC_BACKEND_URL=https://api.example.com -> direct
//
// Previously REST went through the Next.js rewrite proxy while SSE called the
// backend directly. The rewrite target port is fixed at Next.js startup, but
// start.sh can fall back to a different backend port (8000 -> 8765 -> 8123)
// when 8000 is taken, so the proxy and the real backend drifted: every REST
// call (upload, session list, workspace CRUD) returned 500 while SSE worked.
// Routing everything through API_BASE removes the divergence. CORS is wide
// open on the backend, so direct cross-origin calls are fine.
//
// NOTE: there is intentionally NO `window.location.hostname:8000` fallback.
// That fallback was the root cause of the 500s: in same-origin prod it would
// point at a port nothing listens on. When the env var is unset we serve
// same-origin via a relative path instead, which is correct for both dev
// (Next.js rewrite) and prod (reverse proxy).
export const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL
  ? `${process.env.NEXT_PUBLIC_BACKEND_URL}/api/v1`
  : "/api/v1";
const BASE = API_BASE;

export async function listWorkspaces(): Promise<{ workspaces: import("./types").WorkspaceItem[] }> {
  return apiFetch(`${BASE}/workspaces`).then((r) => r.json());
}

export async function getWorkspace(id: string): Promise<import("./types").WorkspaceDetail> {
  return apiFetch(`${BASE}/workspaces/${encodeURIComponent(id)}`).then((r) => r.json());
}

export async function createWorkspace(
  name: string,
  folderIds: string[] = [],
  fileIds: string[] = [],
): Promise<{ workspace_id: string; name: string; library_folder_id?: string }> {
  const res = await apiFetch(`${BASE}/workspaces`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, folder_ids: folderIds, file_ids: fileIds }),
  });
  if (!res.ok) throw new Error(`Create workspace failed: ${res.status}`);
  return res.json();
}

export async function updateWorkspace(
  id: string,
  patch: { name?: string; folder_ids?: string[]; file_ids?: string[] },
): Promise<void> {
  const res = await apiFetch(`${BASE}/workspaces/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Update workspace failed: ${res.status}`);
}

export async function renameWorkspace(id: string, name: string): Promise<void> {
  await apiFetch(`${BASE}/workspaces/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function deleteWorkspace(id: string): Promise<void> {
  await apiFetch(`${BASE}/workspaces/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function moveSessionToWorkspace(wsId: string, sessionId: string): Promise<void> {
  await apiFetch(`${BASE}/workspaces/${encodeURIComponent(wsId)}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export async function removeSessionFromWorkspace(wsId: string, sessionId: string): Promise<void> {
  await apiFetch(`${BASE}/workspaces/${encodeURIComponent(wsId)}/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

export async function uploadWorkspaceFiles(wsId: string, files: File[]): Promise<{ results: AttachmentMeta[]; workspace_id: string }> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await apiFetch(`${BASE}/workspaces/${encodeURIComponent(wsId)}/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`Workspace upload failed: ${res.status}`);
  return res.json();
}

export const listSessions = (): Promise<{ sessions: SessionItem[] }> =>
  apiFetch(`${BASE}/chat/sessions`).then((r) => r.json());

export async function getModelInfo(): Promise<{
  llm_model: string;
  multimodal_configured: boolean;
  multimodal_model: string;
}> {
  const res = await apiFetch(`${BASE}/model-info`);
  if (!res.ok) throw new Error(`model-info failed: ${res.status}`);
  return res.json();
}

export const loadSession = (id: string, tail?: number): Promise<SessionDetail> =>
  apiFetch(`${BASE}/chat/sessions/${encodeURIComponent(id)}${tail ? `?tail=${tail}` : ""}`).then((r) => {
    // apiFetch is a bare fetch wrapper: HTTP errors (404 foreign/missing
    // session) do NOT reject. Throw here so callers' .catch paths (not-found
    // UI) actually fire instead of passing an error body downstream, where
    // `detail.messages === undefined` would poison the store and crash render.
    if (!r.ok) throw new Error(`load session failed: ${r.status}`);
    return r.json();
  });

/** P2 组合快照：一次返回侧边栏所需的会话摘要 + 工作区列表 + 各工作区详情
 *  （带 ETag，数据未变 304 由浏览器缓存复用）。替代原来的三级 N+1 瀑布。 */
export const getSidebarSnapshot = (): Promise<{
  sessions: SessionItem[];
  workspaces: WorkspaceItem[];
  details: Record<string, WorkspaceDetail>;
}> =>
  apiFetch(`${BASE}/sidebar`).then((r) => {
    if (!r.ok) throw new Error(`sidebar failed: ${r.status}`);
    return r.json();
  });

export const deleteSession = (id: string, forgetPromptMemory = false) =>
  apiFetch(`${BASE}/chat/sessions/${encodeURIComponent(id)}?forget_prompt_memory=${forgetPromptMemory}`, {
    method: "DELETE",
  }).then((r) => r.json());

export const renameSession = (id: string, title: string) =>
  apiFetch(`${BASE}/chat/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  }).then((r) => r.json());

/** P1: 会话内切换学段并持久化（grade 已是后端事实源形式："" = 自动）。 */
export const patchSession = (id: string, body: { title?: string; grade?: string }) =>
  apiFetch(`${BASE}/chat/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => r.json());

/** P2/P3 教材库：列表（供资料中心教材库视图 + 对话学段跟随教材预填）。 */
export async function getTextbooks(): Promise<TextbookListItem[]> {
  const res = await apiFetch(`${BASE}/textbooks`);
  if (!res.ok) throw new Error(`List textbooks failed: ${res.status}`);
  return (await res.json()).textbooks ?? [];
}

export interface TextbookVolume {
  file_id: string;
  filename: string;
  original_filename?: string;
  char_count: number;
  has_original: boolean;
  updated_at?: number;
  effective_limits?: { max_chapters?: number | null; max_concepts?: number | null };
  coverage?: Record<string, unknown> | null;
}

export interface TextbookListItem {
  id: string;
  kind: string; // single | group（教材组：多卷合一图谱）
  file_id: string;
  file_ids: string[];
  volumes: TextbookVolume[];
  topic_key: string;
  title: string;
  group_name?: string;
  group_note?: string;
  subject: string;
  level: string;
  original_filename?: string;
  scope: string; // private | public（P6-B：公用教材库所有账号可选用）
  status: string;
  progress: { stage: string; done: number; total: number };
  chapter_count: number;
  concept_count: number;
  warnings: string[];
  error: string;
  filename: string;
  char_count: number;
  has_original: boolean;
  created_at: number;
  updated_at: number;
  graph_policy?: TextbookGraphPolicy;
  coverage?: Record<string, unknown>[];
  ocr_state?: { version?: number; updated_at?: number; volumes?: Record<string, {
    status?: string; target_pages?: number[]; successful_pages?: number[]; pending_pages?: number[];
    paused_pages?: number[]; attempts?: Record<string, number>; next_retry_at?: number | null;
    last_error_code?: string; last_error_summary?: string; policy_mode?: string;
  }> };
  rag_index?: Record<string, unknown>;
}

export interface TextbookGraphPolicy {
  default_max_chapters: number | null;
  default_max_concepts: number | null;
  volume_overrides: Record<string, { max_chapters: number | null; max_concepts: number | null }>;
}

export async function getTextbookGraphPolicy(id: string) {
  const res = await apiFetch(`${BASE}/textbooks/${encodeURIComponent(id)}/graph-policy`);
  if (!res.ok) throw new Error(`Get graph policy failed: ${res.status}`);
  return res.json() as Promise<{ graph_policy: TextbookGraphPolicy; volumes: TextbookVolume[] }>;
}

export async function setTextbookGraphPolicy(id: string, graphPolicy: TextbookGraphPolicy) {
  const res = await apiFetch(`${BASE}/textbooks/${encodeURIComponent(id)}/graph-policy`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(graphPolicy),
  });
  if (!res.ok) throw new Error(`Set graph policy failed: ${res.status}`);
  return res.json();
}

export interface TextbookOutlineChapter {
  chapter: string;
  concept_count: number;
  concepts: string[];
}

export interface TextbookDetail {
  textbook: TextbookListItem;
  outline: TextbookOutlineChapter[];
}

/** P2 教材库：详情（含章节大纲，供 TextbookDrawer 展示）。 */
export async function getTextbook(id: string): Promise<TextbookDetail> {
  const res = await apiFetch(`${BASE}/textbooks/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`Get textbook failed: ${res.status}`);
  return res.json();
}

/** P2 教材库：上传（multipart，逐文件失败也 200）。P6：level 学段五选一 + scope 公用（管理员）。
 * group 非空 = 本次全部文件编为教材组（多卷合一图谱）；groupId = 追加到既有组。 */
export async function uploadTextbooks(
  files: File[],
  opts?: { level?: string; scope?: string; subject?: string; group?: string; groupNote?: string; groupId?: string;
    defaultMaxChapters?: number | null; defaultMaxConcepts?: number | null;
    volumeOverrides?: Record<string, { max_chapters: number | null; max_concepts: number | null }> },
): Promise<{ results: { id?: string; group_id?: string; filename: string; status?: string; error?: string }[] }> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  form.set("level", opts?.level || "其他");
  form.set("scope", opts?.scope || "private");
  if (opts?.subject) form.set("subject", opts.subject);
  if (opts?.group) form.set("group", opts.group);
  if (opts?.groupNote) form.set("group_note", opts.groupNote);
  if (opts?.groupId) form.set("group_id", opts.groupId);
  if (opts?.defaultMaxChapters != null) form.set("default_max_chapters", String(opts.defaultMaxChapters));
  if (opts?.defaultMaxConcepts != null) form.set("default_max_concepts", String(opts.defaultMaxConcepts));
  if (opts?.volumeOverrides) form.set("volume_overrides", JSON.stringify(opts.volumeOverrides));
  const res = await apiFetch(`${BASE}/textbooks/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`Textbook upload failed: ${res.status}`);
  return res.json();
}

/** 教材组：移除一卷（剩余卷自动重建组图谱；删空则整组删除）。 */
export async function removeTextbookVolume(
  groupId: string,
  fileId: string,
): Promise<{ status: string; remaining?: string[] }> {
  const res = await apiFetch(
    `${BASE}/textbooks/${encodeURIComponent(groupId)}/volumes/${encodeURIComponent(fileId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`Remove textbook volume failed: ${res.status}`);
  return res.json();
}

/** 教材组：下载某卷原件（公用组所有账号可下载）。 */
export const downloadTextbookVolume = (groupId: string, fileId: string) =>
  downloadViaFetch(
    `${BASE}/textbooks/${encodeURIComponent(groupId)}/volumes/${encodeURIComponent(fileId)}/download`,
  );

/** P6-B：教材原件下载（自有 + 公用均可）。 */
export const downloadTextbook = (id: string) =>
  downloadViaFetch(`${BASE}/textbooks/${encodeURIComponent(id)}/download`);

/** P6-B4 管理端：账号列表/占用统计/聊天清理/彻底删除（仅管理员；非管理员 403）。 */
export interface AdminUserStorage {
  chat_bytes: number;
  uploads_bytes: number;
  notes_bytes: number;
  students_bytes: number;
  knowledge_bytes: number;
  trash_bytes: number;
  total_bytes: number;
  session_count: number;
  file_count: number;
}

export interface AdminUser {
  id: string;
  email: string;
  username: string;
  role: string;
  created_at: number;
  last_login_at: number;
  storage?: AdminUserStorage;
}

export interface AdminUsersResponse {
  users: AdminUser[];
  summary: { count: number; total_bytes: number };
}

/** 清理/彻底删除的释放报告（后端 account_data 返回）。 */
export interface AdminCleanupReport {
  scope?: string;
  freed_bytes: number;
  sessions?: number;
  workspaces?: number;
  library_files?: number;
  trash_items?: number;
}

/** P7 图表标记状态：旧书（无 [图/[页码= 标记）刷新弹窗默认推荐 full_ocr 升级。 */
export interface TextbookFigureStatus {
  status: string;
  has_markers: boolean;
  volumes: { file_id: string; filename: string; has_markers: boolean }[];
}

export const getTextbookFigureStatus = (id: string): Promise<TextbookFigureStatus> =>
  apiFetch(`${BASE}/textbooks/${encodeURIComponent(id)}/figure-status`).then((r) => {
    if (!r.ok) throw new Error(`figure-status failed: ${r.status}`);
    return r.json();
  });

/** P7 图表证据「查看原页」：取 PDF 原件页快照 blob（新标签打开）。 */
export async function openFilePageSnapshot(fileId: string, page: number): Promise<void> {
  const res = await apiFetch(
    `${BASE}/library/files/${encodeURIComponent(fileId)}/page/${page}`);
  if (!res.ok) throw new Error(`snapshot failed: ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function getAdminUsers(): Promise<AdminUsersResponse> {
  const res = await apiFetch(`${BASE}/admin/users`);
  if (!res.ok) throw new Error(`List users failed: ${res.status}`);
  const body = await res.json();
  return { users: body?.users ?? [], summary: body?.summary ?? { count: 0, total_bytes: 0 } };
}

/** 清理账号的聊天侧数据：all=连会话一起删；uploads_only=仅上传文件、保留会话文本。 */
export async function clearAdminUserChat(
  userId: string,
  scope: "all" | "uploads_only",
): Promise<AdminCleanupReport> {
  const res = await apiFetch(`${BASE}/admin/users/${encodeURIComponent(userId)}/clear-chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
  if (!res.ok) throw new Error(`Clear user chat failed: ${res.status}`);
  return (await res.json()).report;
}

/** 彻底删除账号及其名下全部数据（聊天/上传/笔记/学习档案/图谱/回收站），不可恢复。 */
export async function purgeAdminUser(userId: string): Promise<AdminCleanupReport> {
  const res = await apiFetch(`${BASE}/admin/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Purge user failed: ${res.status}`);
  return (await res.json()).report;
}

/** 管理台「数据清理」：孤儿数据（测试残留/注销遗物/无引用 trace 等）扫描与清理。 */
export interface AdminOrphanCategory {
  items: number;
  bytes: number;
  samples: string[];
}

export interface AdminOrphanReport {
  protected_ids: string[];
  categories: Record<string, AdminOrphanCategory>;
  total_items: number;
  total_bytes: number;
}

export interface AdminOrphanPurgeResult {
  status: string;
  dry_run: boolean;
  categories: Record<string, { deleted: number; bytes: number; total: number }>;
  total_deleted: number;
  total_bytes: number;
}

export async function scanAdminOrphanData(): Promise<AdminOrphanReport> {
  const res = await apiFetch(`${BASE}/admin/orphan-data`);
  if (!res.ok) throw new Error(`Scan orphan data failed: ${res.status}`);
  return (await res.json()).report;
}

export async function purgeAdminOrphanData(opts?: {
  dry_run?: boolean;
  categories?: string[];
}): Promise<AdminOrphanPurgeResult> {
  const res = await apiFetch(`${BASE}/admin/orphan-data/purge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dry_run: opts?.dry_run ?? false, categories: opts?.categories ?? null }),
  });
  if (!res.ok) throw new Error(`Purge orphan data failed: ${res.status}`);
  return (await res.json()).report;
}

/** P2 教材库：编辑标题/学科/学段。 */
export async function patchTextbook(
  id: string,
  body: { title?: string; group_name?: string; group_note?: string; subject?: string; level?: string },
): Promise<{ textbook: TextbookListItem }> {
  const res = await apiFetch(`${BASE}/textbooks/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Patch textbook failed: ${res.status}`);
  return res.json();
}

export async function patchTextbookVolume(
  textbookId: string,
  fileId: string,
  filename: string,
): Promise<{ status: string; file_id: string; filename: string }> {
  const res = await apiFetch(
    `${BASE}/textbooks/${encodeURIComponent(textbookId)}/volumes/${encodeURIComponent(fileId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename }),
    },
  );
  if (!res.ok) throw new Error(`Rename textbook volume failed: ${res.status}`);
  return res.json();
}

export type TextbookRefreshMode = "rag_graph" | "full_ocr" | "graph_only";

/** 教材刷新：默认复用既有文本重建 RAG+图谱，不调用 OCR。 */
export async function rebuildTextbookGraph(
  id: string,
  mode: TextbookRefreshMode = "rag_graph",
): Promise<{ status: string; mode: TextbookRefreshMode; uses_existing_text: boolean; ocr_requested: boolean }> {
  const res = await apiFetch(
    `${BASE}/textbooks/${encodeURIComponent(id)}/rebuild_graph`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }) },
  );
  if (!res.ok) throw new Error(`Rebuild textbook graph failed: ${res.status}`);
  return res.json();
}

/** P2 教材库：删除（级联：图谱+文件+向量+记录）。 */
export async function deleteTextbook(id: string): Promise<{ status: string }> {
  const res = await apiFetch(`${BASE}/textbooks/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete textbook failed: ${res.status}`);
  return res.json();
}

/** 终止教材解析（OCR / 构建）：合作式停止，已有文本与切片保留。 */
export async function cancelTextbookParse(
  id: string,
): Promise<{ status: string; textbook_id: string; record_status: string }> {
  const res = await apiFetch(
    `${BASE}/textbooks/${encodeURIComponent(id)}/cancel`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Cancel textbook parse failed: ${res.status}`);
  return res.json();
}

/** 批量操作逐项结果：building（含 idempotent_reuse）| missing | forbidden | skipped | cancelled */
export interface TextbookBulkResultItem {
  textbook_id: string;
  status: string;
  idempotent_reuse?: boolean;
  uses_existing_text?: boolean;
  ocr_requested?: boolean;
  record_status?: string;
}

/** 教材批量重建：逐本幂等发起（服务端 per-owner 刷新锁 + 构建队列串行）。 */
export async function bulkRebuildTextbooks(
  ids: string[],
  mode: TextbookRefreshMode = "rag_graph",
): Promise<{ status: string; mode: TextbookRefreshMode; count: number; results: TextbookBulkResultItem[] }> {
  const res = await apiFetch(
    `${BASE}/textbooks/bulk/rebuild`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, mode }) },
  );
  if (!res.ok) throw new Error(`Bulk rebuild textbooks failed: ${res.status}`);
  return res.json();
}

/** 教材批量取消：只对活动态（building/ocr_waiting/ocr_paused）执行，空闲项 skipped。 */
export async function bulkCancelTextbooks(
  ids: string[],
): Promise<{ status: string; count: number; results: TextbookBulkResultItem[] }> {
  const res = await apiFetch(
    `${BASE}/textbooks/bulk/cancel`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) },
  );
  if (!res.ok) throw new Error(`Bulk cancel textbooks failed: ${res.status}`);
  return res.json();
}

export async function deleteWorkspaceFile(wsId: string, fileId: string): Promise<void> {
  const res = await apiFetch(
    `${BASE}/workspaces/${encodeURIComponent(wsId)}/files/${encodeURIComponent(fileId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`Delete workspace file failed: ${res.status}`);
}

// --- M7 资料库（Library）API ---

export const getLibrary = (): Promise<LibraryTree> =>
  apiFetch(`${BASE}/library`).then((r) => r.json());

export async function renameLibraryFile(id: string, filename: string): Promise<LibraryFile> {
  const res = await apiFetch(`${BASE}/library/files/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  if (!res.ok) throw new Error(`Rename library file failed: ${res.status}`);
  return (await res.json()).file;
}

export async function createLibraryFolder(name: string): Promise<{ folder: LibraryFolder }> {
  const res = await apiFetch(`${BASE}/library/folders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Create folder failed: ${res.status}`);
  return res.json();
}

export async function renameLibraryFolder(id: string, name: string): Promise<void> {
  const res = await apiFetch(`${BASE}/library/folders/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Rename folder failed: ${res.status}`);
}

export async function deleteLibraryFolder(id: string): Promise<void> {
  const res = await apiFetch(`${BASE}/library/folders/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete folder failed: ${res.status}`);
}

export async function uploadLibraryFiles(
  folderId: string,
  files: File[],
): Promise<{ results: (AttachmentMeta & { folder_id?: string; error?: string })[] }> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await apiFetch(
    `${BASE}/library/upload?folder_id=${encodeURIComponent(folderId)}`,
    { method: "POST", body: form },
  );
  if (!res.ok) throw new Error(`Library upload failed: ${res.status}`);
  return res.json();
}

/** Collect per-file failures from an upload response into one warning message
 *  ("filename: reason；…"). Returns null when every file succeeded. Upload
 *  endpoints answer 200 with per-file `error` entries (too large / unsupported
 *  format / no extractable text), so callers must check this — res.ok alone
 *  only catches transport-level failures. */
export function uploadFailures(results: { filename: string; error?: string | null }[]): string | null {
  const failed = results.filter((r) => r.error);
  return failed.length > 0 ? failed.map((f) => `${f.filename}: ${f.error}`).join("；") : null;
}

export async function attachLibraryFiles(
  sessionId: string,
  fileIds: string[],
  workspaceId?: string | null,
): Promise<{ results: AttachmentMeta[]; errors: { file_id: string; filename?: string; error: string }[]; session_id: string }> {
  // sessionId="new" lets the backend create the session first, so a brand-new
  // chat can reference library files from its very first turn.
  const params = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
  const res = await apiFetch(
    `${BASE}/chat/sessions/${encodeURIComponent(sessionId)}/attach_library${params}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_ids: fileIds }),
    },
  );
  if (!res.ok) throw new Error(`Attach library files failed: ${res.status}`);
  return res.json();
}

export async function moveLibraryFile(fileId: string, folderId: string): Promise<void> {
  const res = await apiFetch(`${BASE}/library/files/${encodeURIComponent(fileId)}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_id: folderId }),
  });
  if (!res.ok) throw new Error(`Move file failed: ${res.status}`);
}

export async function deleteLibraryFile(fileId: string): Promise<void> {
  const res = await apiFetch(`${BASE}/library/files/${encodeURIComponent(fileId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Delete file failed: ${res.status}`);
}

/** 经 apiFetch（带 JWT）下载文件为 Blob，再触发浏览器保存。 */
async function downloadViaFetch(url: string): Promise<void> {
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  let filename = "";
  const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
  const plain = cd.match(/filename="?([^";]+)"?/i);
  if (star) filename = decodeURIComponent(star[1]);
  else if (plain) filename = plain[1];
  // 兜底：响应头没给文件名时按 content-type 补一个带后缀的默认名——
  // 下载下来的文件必须保留名称与扩展名，否则无法直接打开。
  if (!filename) {
    const mime = (res.headers.get("content-type") || "").split(";")[0].trim();
    const ext = MIME_EXT[mime] || ".bin";
    filename = `download${ext}`;
  }
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

const MIME_EXT: Record<string, string> = {
  "application/pdf": ".pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
  "text/plain": ".txt",
  "text/markdown": ".md",
};

export const downloadLibraryFile = (fileId: string) =>
  downloadViaFetch(`${BASE}/library/files/${encodeURIComponent(fileId)}/download`);

export const downloadSessionFile = (sessionId: string, fileId: string) =>
  downloadViaFetch(
    `${BASE}/chat/sessions/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(fileId)}/download`,
  );

export async function uploadFiles(
  files: File[],
  sessionId?: string,
  grade?: string,
  workspaceId?: string | null,
): Promise<{ results: AttachmentMeta[]; session_id: string }> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const params = new URLSearchParams();
  if (sessionId) params.set("session_id", sessionId);
  if (grade) params.set("grade", grade);
  if (workspaceId) params.set("workspace_id", workspaceId);
  const res = await apiFetch(`${BASE}/chat/upload?${params}`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  return res.json();
}

export async function ocrImage(file: File): Promise<{ text: string; filename?: string; warning?: string }> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await apiFetch(`${BASE}/chat/ocr`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`OCR failed: ${res.status}`);
  return res.json();
}

// --- P10 语音通话（可插拔 STT/TTS，默认关闭） ---

export async function voiceStatus(): Promise<{ enabled: boolean; stt: string | null; tts: string | null }> {
  return apiFetch(`${BASE}/voice/status`).then((r) => r.json());
}

export async function voiceTicket(): Promise<{ ticket: string; expires_in: number }> {
  const res = await apiFetch(`${BASE}/voice/ticket`, { method: "POST" });
  if (!res.ok) throw new Error(`Voice ticket failed: ${res.status}`);
  return res.json();
}

export async function* chatStream(
  body: { message: string; session_id?: string | null; workspace_id?: string | null; grade?: string; lang?: string; output_language?: string | null; attachments?: unknown[] },
  signal?: AbortSignal,
): AsyncGenerator<ChatSSEEvent> {
  const res = await apiFetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Chat stream failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("event:")) {
        currentEvent = trimmed.slice(6).trim();
      } else if (trimmed.startsWith("data:")) {
        try {
          const payload = JSON.parse(trimmed.slice(5).trim());
          payload.type = currentEvent;
          yield payload as ChatSSEEvent;
        } catch {
          // skip malformed
        }
      }
    }
  }
}

// --- M8 UX Intelligence (read-only) ---

export async function getUxProfile(): Promise<UxProfileSummary> {
  return apiFetch(`${BASE}/ux/profile`).then((r) => r.json());
}

export async function getUxMotivation(): Promise<UxMotivation> {
  return apiFetch(`${BASE}/ux/motivation`).then((r) => r.json());
}

export async function getUxActivity(days = 14): Promise<UxActivity> {
  const q = new URLSearchParams({ days: String(days) });
  return apiFetch(`${BASE}/ux/activity?${q.toString()}`).then((r) => r.json());
}

export async function getUxGreeting(lang: string, grade: string): Promise<UxGreeting> {
  const q = new URLSearchParams({ lang, grade });
  return apiFetch(`${BASE}/ux/greeting?${q.toString()}`).then((r) => r.json());
}

export type GradeVerdict = "correct" | "partial" | "wrong" | null;

/** W3/D06 结构化批改明细（F01 展示；由服务端按冻结量规计算）。 */
export interface QuizStructuredFeedback {
  first_error?: { description?: string; preceding_correct?: string };
  hypotheses?: { kind?: string; statement?: string }[];
  uncertainties?: string[];
  feedback?: { strength?: string; next_step?: string };
  score?: number | null;
  rubric_id?: string;
}

/** W3/F02 关键步骤提示（量规派生；服务端记录 assistance）。 */
export async function fetchQuizHint(sessionId: string, stem: string): Promise<{
  status: string; hint: string; message?: string;
}> {
  const res = await apiFetch(
    `${BASE}/quiz/hint?session_id=${encodeURIComponent(sessionId)}&stem=${encodeURIComponent(stem)}`);
  if (!res.ok) throw new Error(`Hint failed: ${res.status}`);
  return res.json();
}

/** W3/F05 异议标记（账本审计；修正走重答 supersede）。 */
export async function disputeQuizAttempt(attemptId: string, reason = ""): Promise<{
  status: string; message?: string;
}> {
  const res = await apiFetch(`${BASE}/quiz/dispute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ attempt_id: attemptId, reason }),
  });
  if (!res.ok) throw new Error(`Dispute failed: ${res.status}`);
  return res.json();
}

export async function recordAnswer(body: {
  stem: string;
  q_type: string;
  student_answer: string;
  correct_answer: string;
  options?: Record<string, string>;
  explanation?: string;
  knowledge_point?: string;
  grade?: string;
  session_id?: string;
  subject?: string;
  difficulty?: number;
  /** Grounded provenance（additive，仅审计/学习记录；服务端以原始生成记录为准） */
  grounding_mode?: string;
  grounding_tier?: string;
  source_refs?: QuizSourceRef[];
}): Promise<{ status: string; result?: { score: number; concept_status: string; verdict: string }; message?: string }> {
  const res = await apiFetch(`${BASE}/quiz/record`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Record failed: ${res.status}`);
  return res.json();
}

export async function* gradeAnswer(
  body: {
    stem: string;
    q_type: string;
    student_answer: string;
    correct_answer: string;
    explanation?: string;
    knowledge_point?: string;
    grade?: string;
    session_id?: string;
    subject?: string;
    /** false = 只生成点评，不写掌握度/作答记录（MC 已走 /quiz/record） */
    record?: boolean;
  },
  signal?: AbortSignal,
): AsyncGenerator<
  | { type: "delta"; content: string }
  | { type: "retry"; attempt?: number; reason?: string }
  | { type: "done"; verdict: GradeVerdict; feedback: string; full: string;
      score?: number; concept_status?: string; attempt_id?: string;
      structured?: QuizStructuredFeedback }
  | { type: "error"; message: string }
> {
  const res = await apiFetch(`${BASE}/quiz/grade`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`Grade failed: ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("event:")) {
        currentEvent = trimmed.slice(6).trim();
      } else if (trimmed.startsWith("data:")) {
        try {
          const payload = JSON.parse(trimmed.slice(5).trim());
          if (currentEvent === "delta") yield { type: "delta", content: payload.content };
          else if (currentEvent === "retry") yield { type: "retry", attempt: payload.attempt, reason: payload.reason };
          else if (currentEvent === "done") yield { type: "done", verdict: payload.verdict, feedback: payload.feedback, full: payload.full };
          else if (currentEvent === "error") yield { type: "error", message: payload.message };
        } catch {
          // skip malformed
        }
      }
    }
  }
}
import type { UxProfileSummary, UxMotivation, UxActivity, UxGreeting } from "./types";

// --- Unified recycle bin / data lifecycle ---
export async function listTrash(resourceType = ""): Promise<{ status: string; items: import("./types").TrashItem[] }> {
  const q = resourceType ? `?resource_type=${encodeURIComponent(resourceType)}` : "";
  const res = await apiFetch(`${BASE}/trash${q}`);
  if (!res.ok) throw new Error(`List trash failed: ${res.status}`);
  return res.json();
}

export async function getTrashPolicy(): Promise<import("./types").TrashPolicy> {
  const res = await apiFetch(`${BASE}/trash/policy`);
  if (!res.ok) throw new Error(`Get trash policy failed: ${res.status}`);
  return res.json();
}

export async function setTrashPolicy(retentionDays: number): Promise<import("./types").TrashPolicy> {
  const res = await apiFetch(`${BASE}/trash/policy`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ retention_days: retentionDays }),
  });
  if (!res.ok) throw new Error(`Set trash policy failed: ${res.status}`);
  return res.json();
}

export async function restoreTrashItem(itemId: string, workspaceIds: string[] = []): Promise<void> {
  const res = await apiFetch(`${BASE}/trash/${encodeURIComponent(itemId)}/restore`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace_ids: workspaceIds }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Restore failed: ${res.status}`);
}

export async function purgeTrashItem(itemId: string): Promise<void> {
  const res = await apiFetch(`${BASE}/trash/${encodeURIComponent(itemId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Purge failed: ${res.status}`);
}

export async function emptyTrash(): Promise<{ purged: number; failed: string[] }> {
  const res = await apiFetch(`${BASE}/trash`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Empty trash failed: ${res.status}`);
  return res.json();
}

export async function getPromptMemoryProfile(): Promise<import("./types").PromptMemoryProfile> {
  const res = await apiFetch(`${BASE}/memory/prompt-profile`);
  if (!res.ok) throw new Error(`Get prompt memory failed: ${res.status}`);
  return res.json();
}

export async function setPromptMemoryWindow(windowSize: number): Promise<import("./types").PromptMemoryProfile> {
  const res = await apiFetch(`${BASE}/memory/prompt-profile/window`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ window_size: windowSize }),
  });
  if (!res.ok) throw new Error(`Set prompt memory window failed: ${res.status}`);
  return res.json();
}

export async function getPromptMemorySessionStatus(sessionId: string): Promise<"recent" | "compacted" | "legacy_unknown" | "none"> {
  const res = await apiFetch(`${BASE}/memory/prompt-profile/sessions/${encodeURIComponent(sessionId)}`);
  if (!res.ok) return "none";
  return (await res.json()).status ?? "none";
}

export async function getAdminRetentionPolicy(): Promise<Record<string, number | string>> {
  const res = await apiFetch(`${BASE}/admin/data-retention`);
  if (!res.ok) throw new Error(`Get retention policy failed: ${res.status}`);
  return res.json();
}

export async function setAdminRetentionPolicy(policy: Record<string, number | string>): Promise<Record<string, number | string>> {
  const res = await apiFetch(`${BASE}/admin/data-retention`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(policy),
  });
  if (!res.ok) throw new Error(`Set retention policy failed: ${res.status}`);
  return res.json();
}

export async function getAdminPromptMemoryPolicy(): Promise<Record<string, number>> {
  const res = await apiFetch(`${BASE}/admin/prompt-memory-policy`);
  if (!res.ok) throw new Error(`Get prompt policy failed: ${res.status}`);
  return res.json();
}

export async function setAdminPromptMemoryPolicy(policy: Record<string, number>): Promise<Record<string, number>> {
  const res = await apiFetch(`${BASE}/admin/prompt-memory-policy`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(policy),
  });
  if (!res.ok) throw new Error(`Set prompt policy failed: ${res.status}`);
  return res.json();
}

export async function getAdminPublicTrash(): Promise<import("./types").TrashItem[]> {
  const res = await apiFetch(`${BASE}/admin/public-trash`);
  if (!res.ok) throw new Error(`Get public trash failed: ${res.status}`);
  return (await res.json()).items ?? [];
}

export async function restoreAdminPublicTrash(itemId: string): Promise<void> {
  const res = await apiFetch(`${BASE}/admin/public-trash/${encodeURIComponent(itemId)}/restore`, { method: "POST" });
  if (!res.ok) throw new Error(`Restore public trash failed: ${res.status}`);
}

export async function purgeAdminPublicTrash(itemId: string): Promise<void> {
  const res = await apiFetch(`${BASE}/admin/public-trash/${encodeURIComponent(itemId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`Purge public trash failed: ${res.status}`);
}

export async function getTrashItem(itemId: string): Promise<import("./types").TrashItem> {
  const res = await apiFetch(`${BASE}/trash/${encodeURIComponent(itemId)}`);
  if (!res.ok) throw new Error(`Get trash item failed: ${res.status}`);
  return (await res.json()).item;
}

export interface AdminOCRPolicy {
  configured_concurrency: number;
  effective_concurrency: number;
  pending_concurrency: number | null;
  active_ocr_jobs: number;
  active_ocr_pages: number;
  waiting_ocr_pages: number;
  retry_waiting_pages: number;
  next_retry_at: number | null;
  failure_mode: "persistent_api" | "bounded_then_local" | "bounded_api_only";
  max_attempts: number;
  retry_interval_seconds: number;
  request_timeout_seconds: number;
  policy_version: number;
  min_concurrency: number;
  max_concurrency: number;
  generation: number;
  scope: string;
}

export async function getAdminOCRPolicy(): Promise<AdminOCRPolicy> {
  const res = await apiFetch(`${BASE}/admin/ocr-policy`);
  if (!res.ok) throw new Error(`Get OCR policy failed: ${res.status}`);
  return res.json();
}

export async function setAdminOCRPolicy(policy: {
  concurrency: number;
  failure_mode: AdminOCRPolicy["failure_mode"];
  max_attempts: number;
  retry_interval_seconds: number;
  request_timeout_seconds: number;
}): Promise<AdminOCRPolicy> {
  const res = await apiFetch(`${BASE}/admin/ocr-policy`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(policy),
  });
  if (!res.ok) throw new Error(`Set OCR policy failed: ${res.status}`);
  return res.json();
}

export interface AdminTextbookPipelinePolicy {
  mode: "parallel" | "legacy";
  build_concurrency: number;
  volume_concurrency: number;
  llm_concurrency: number;
  effective_limits: { build: number; volume: number; llm: number };
  gate_active: number;
  gate_waiting: number;
  min_build_concurrency: number;
  max_build_concurrency: number;
  min_volume_concurrency: number;
  max_volume_concurrency: number;
  min_llm_concurrency: number;
  max_llm_concurrency: number;
  modes: string[];
  updated_at: number;
  scope: string;
}

export async function getAdminTextbookPipelinePolicy(): Promise<AdminTextbookPipelinePolicy> {
  const res = await apiFetch(`${BASE}/admin/textbook-pipeline`);
  if (!res.ok) throw new Error(`Get textbook pipeline policy failed: ${res.status}`);
  return res.json();
}

export async function setAdminTextbookPipelinePolicy(policy: {
  mode: AdminTextbookPipelinePolicy["mode"];
  build_concurrency: number;
  volume_concurrency: number;
  llm_concurrency: number;
}): Promise<AdminTextbookPipelinePolicy> {
  const res = await apiFetch(`${BASE}/admin/textbook-pipeline`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(policy),
  });
  if (!res.ok) throw new Error(`Set textbook pipeline policy failed: ${res.status}`);
  return res.json();
}

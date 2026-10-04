import { API_BASE } from "./api";
import { apiFetch } from "./api-fetch";
import type { IllustrationMode } from "./api-illustrations";
import type { QuestionIllustrationData } from "./types";

export interface SelectedMaterialRef { asset_id: string; version: number }
export type ToolIllustrationStatus = "queued" | "running" | "ready" | "failed";
export interface IllustrationTurn {
  turn_id: string; message: string; mode: IllustrationMode;
  selected_materials: SelectedMaterialRef[]; job_id: string;
  status: ToolIllustrationStatus; revision: number | null; created_at: number | string;
  request_id?: string; source_revision?: number;
}
export interface IllustrationRevision {
  revision: number; artifact_id: string; mode: IllustrationMode;
  illustration: QuestionIllustrationData; created_at: number | string;
}
export interface IllustrationSessionSummary {
  session_id: string; title: string; revision: number;
  active_job_id: string | null; created_at: number | string; updated_at: number | string;
}
export interface IllustrationSession extends IllustrationSessionSummary {
  turns: IllustrationTurn[]; revisions: IllustrationRevision[];
}
export interface ToolIllustrationJob {
  job_id: string; session_id: string; turn_id: string; mode: IllustrationMode;
  status: ToolIllustrationStatus; stage: string; progress: number; base_revision: number;
  revision: number | null; artifact_id: string | null; illustration: QuestionIllustrationData | null;
  selected_materials: SelectedMaterialRef[];
  failure: { code: string; retryable: boolean } | null;
  source_revision?: number;
}
export class IllustrationToolError extends Error {
  constructor(readonly code: string, readonly status = 0) { super(code); this.name = "IllustrationToolError"; }
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${API_BASE}/tools/illustration${path}`, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    throw new IllustrationToolError(typeof detail === "string" ? detail : detail?.error?.code ?? detail?.code ?? `status_${response.status}`, response.status);
  }
  return body as T;
}
function json(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}
export const listIllustrationSessions = (signal?: AbortSignal) =>
  request<{ items: IllustrationSessionSummary[]; total: number }>("/sessions", { signal });
export const createIllustrationSession = (title = "", signal?: AbortSignal) =>
  request<IllustrationSession>("/sessions", { ...json({ title }), signal });
export const getIllustrationSession = (id: string, signal?: AbortSignal) =>
  request<IllustrationSession>(`/sessions/${encodeURIComponent(id)}`, { signal });
export const deleteIllustrationSession = (id: string, signal?: AbortSignal) =>
  request<{ deleted: boolean }>(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE", signal });
export const startIllustrationTurn = (id: string, body: {
  message: string; mode: IllustrationMode; selected_materials: SelectedMaterialRef[]; base_revision: number; source_revision?: number; request_id: string;
}, signal?: AbortSignal) => request<ToolIllustrationJob>(`/sessions/${encodeURIComponent(id)}/turns`, { ...json(body), signal });
export const getToolIllustrationJob = (id: string, signal?: AbortSignal) =>
  request<ToolIllustrationJob>(`/jobs/${encodeURIComponent(id)}`, { signal });
export const retryToolIllustrationJob = (id: string, signal?: AbortSignal) =>
  request<ToolIllustrationJob>(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST", signal });

import { API_BASE } from "./api";
import { apiFetch } from "./api-fetch";
import type { QuestionIllustrationData } from "./types";

export type MaterialScope = "private" | "public";
export type MaterialSource = "upload" | "manual" | "llm";
export interface MaterialParameterization {
  schema_version: "1.0.0";
  parameters: Record<string, { type: "number" | "integer" | "string" | "color";
    role: "quantity" | "text" | "schematic" | "appearance"; description: string; default: string | number;
    unit?: string; minimum?: number; maximum?: number; max_length?: number }>;
  bindings: { parameter: string; element_id: string; attribute: string; factor?: number; offset?: number }[];
}
export const STATIC_PARAMETERIZATION: MaterialParameterization = { schema_version: "1.0.0", parameters: {}, bindings: [] };
export interface MaterialInput {
  title: string; description: string; subject: string; aliases: string[];
  svg: string; scope: MaterialScope; source: MaterialSource; enabled: boolean;
  guidance_note?: string;
  parameterization?: MaterialParameterization;
  base_revision?: number;
}
export interface DiagramMaterial extends MaterialInput {
  id: string; revision: number; latest_revision?: number; revisions?: number[];
  content_hash: string; illustration: QuestionIllustrationData; validation: string;
}
export interface MaterialTemplate { id: string; title: string; subject: string; description: string; svg: string; parameterization: MaterialParameterization }
async function read<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const response = await apiFetch(`${API_BASE}/diagram-materials${path}`, { ...init, signal });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : String(response.status));
  return body;
}
function json(body: unknown, method = "POST"): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}
export const materialTemplates = (signal?: AbortSignal) => read<{ templates: MaterialTemplate[]; guide: string[] }>("/templates", undefined, signal);
export const listMaterials = (scope: MaterialScope, q: string, page: number, signal?: AbortSignal, filters?: { subject?: string; enabled_only?: boolean }) =>
  read<{ items: DiagramMaterial[]; total: number }>(`?${new URLSearchParams({ scope, q, page: String(page), per: "12",
    ...(filters?.subject ? { subject: filters.subject } : {}), ...(filters?.enabled_only ? { enabled_only: "true" } : {}) })}`, undefined, signal);
export const getMaterial = (id: string, revision?: number, signal?: AbortSignal) =>
  read<DiagramMaterial>(`/${encodeURIComponent(id)}${revision ? `?revision=${revision}` : ""}`, undefined, signal);
export const previewMaterial = (svg: string, signal?: AbortSignal, parameterization: MaterialParameterization = STATIC_PARAMETERIZATION, params: Record<string, string | number> = {}) =>
  read<{ svg: string; illustration: QuestionIllustrationData; parameterization: MaterialParameterization }>("/preview", json({ svg, parameterization, params }), signal);
export const generateMaterial = (requirement: string, current_svg: string, signal?: AbortSignal, current_parameterization: MaterialParameterization = STATIC_PARAMETERIZATION) =>
  read<{ svg: string; illustration: QuestionIllustrationData; parameterization?: MaterialParameterization }>("/generate", json({ requirement, current_svg, current_parameterization }), signal);
export const saveMaterial = (body: MaterialInput, id?: string, signal?: AbortSignal) =>
  read<DiagramMaterial>(id ? `/${encodeURIComponent(id)}` : "", json(body, id ? "PUT" : "POST"), signal);
export const deleteMaterial = (id: string, revision: number, signal?: AbortSignal) =>
  read<{ deleted: boolean }>(`/${encodeURIComponent(id)}?base_revision=${revision}`, { method: "DELETE" }, signal);

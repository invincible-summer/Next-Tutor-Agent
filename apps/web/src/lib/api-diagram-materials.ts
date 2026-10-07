import type { QuestionIllustrationData } from "./types";
import { ApiError } from "@next-tutor/api-client";
import { apiClient } from "@/platform/api-client";

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
export type UnifiedMaterialSource = "builtin" | "public";
export interface UnifiedMaterialCard {
  id: string;
  asset_id: string;
  source: UnifiedMaterialSource;
  title: string;
  english?: string;
  description: string;
  aliases: string[];
  subject: string;
  subjects: string[];
  family: string;
  category: string;
  education_levels: string[];
  asset_kind: string;
  version: number;
  license?: string;
  illustration: QuestionIllustrationData;
}
export interface UnifiedMaterialPage {
  catalog_version: string;
  total: number;
  page: number;
  per: number;
  source_counts: { builtin: number; public: number };
  items: UnifiedMaterialCard[];
}
export interface MaterialTemplate { id: string; title: string; subject: string; description: string; svg: string; parameterization: MaterialParameterization }
async function read<T>(path: string, init?: { method?: string; json?: unknown; signal?: AbortSignal | null }): Promise<T> {
  // 传输（鉴权/元数据/读超时/重试）在共享 transport；这里保留 Web 的
  // 错误语义：detail 字符串或 HTTP 状态。
  try {
    const result = await apiClient().transport.request<T>(`/diagram-materials${path}`, init);
    return result.body as T;
  } catch (error) {
    if (error instanceof ApiError) {
      const detail = (error.details as { detail?: unknown } | undefined)?.detail;
      throw new Error(typeof detail === "string" ? detail : String(error.status));
    }
    throw error;
  }
}
function json(body: unknown, method = "POST"): { method: string; json: unknown } {
  return { method, json: body };
}
export const materialTemplates = (signal?: AbortSignal) => read<{ templates: MaterialTemplate[]; guide: string[] }>("/templates", { signal });
export const listMaterials = (scope: MaterialScope, q: string, page: number, signal?: AbortSignal, filters?: { subject?: string; enabled_only?: boolean }) =>
  read<{ items: DiagramMaterial[]; total: number }>(`?${new URLSearchParams({ scope, q, page: String(page), per: "12",
    ...(filters?.subject ? { subject: filters.subject } : {}), ...(filters?.enabled_only ? { enabled_only: "true" } : {}) })}`, { signal });
export const listMaterialCatalog = (q: string, page: number, signal?: AbortSignal, filters?: { subject?: string; family?: string; education_level?: string; asset_kind?: string }) =>
  read<UnifiedMaterialPage>(`/catalog?${new URLSearchParams({ q, page: String(page), per: "12",
    ...(filters?.subject ? { subject: filters.subject } : {}), ...(filters?.family ? { family: filters.family } : {}),
    ...(filters?.education_level ? { education_level: filters.education_level } : {}), ...(filters?.asset_kind ? { asset_kind: filters.asset_kind } : {}) })}`, { signal });
export const getMaterial = (id: string, revision?: number, signal?: AbortSignal) =>
  read<DiagramMaterial>(`/${encodeURIComponent(id)}${revision ? `?revision=${revision}` : ""}`, { signal });
export const previewMaterial = (svg: string, signal?: AbortSignal, parameterization: MaterialParameterization = STATIC_PARAMETERIZATION, params: Record<string, string | number> = {}) =>
  read<{ svg: string; illustration: QuestionIllustrationData; parameterization: MaterialParameterization }>("/preview", { ...json({ svg, parameterization, params }), signal });
export const generateMaterial = (requirement: string, current_svg: string, signal?: AbortSignal, current_parameterization: MaterialParameterization = STATIC_PARAMETERIZATION) =>
  read<{ svg: string; illustration: QuestionIllustrationData; parameterization?: MaterialParameterization }>("/generate", { ...json({ requirement, current_svg, current_parameterization }), signal });
export const saveMaterial = (body: MaterialInput, id?: string, signal?: AbortSignal) =>
  read<DiagramMaterial>(id ? `/${encodeURIComponent(id)}` : "", { ...json(body, id ? "PUT" : "POST"), signal });
export const deleteMaterial = (id: string, revision: number, signal?: AbortSignal) =>
  read<{ deleted: boolean }>(`/${encodeURIComponent(id)}?base_revision=${revision}`, { method: "DELETE", signal });
